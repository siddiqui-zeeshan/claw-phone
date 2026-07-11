"""Tool-use execution loop for multi-turn function calling."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from spare_paw.router.openrouter import OpenRouterClient
    from spare_paw.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


async def _stream_and_assemble(
    client: "OpenRouterClient",
    messages: list[dict[str, Any]],
    model: str,
    tools: list[dict[str, Any]] | None,
    on_token: Callable[[str], None] | None,
    semaphore_held: bool = False,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Consume a streaming chat completion and return (assistant_message, usage).

    Emits each text delta to ``on_token`` in real time. Assembles tool_calls
    from incremental deltas into the final OpenAI-format structure.
    """
    text_parts: list[str] = []
    tool_calls_by_index: dict[int, dict[str, Any]] = {}
    usage: dict[str, int] = {}

    stream_kwargs: dict[str, Any] = {}
    if semaphore_held:
        stream_kwargs["semaphore_held"] = True

    async for chunk in client.chat_stream(messages, model, tools, **stream_kwargs):
        if chunk.kind == "text_delta" and chunk.content:
            text_parts.append(chunk.content)
            if on_token is not None:
                on_token(chunk.content)
        elif chunk.kind == "tool_call_delta" and chunk.tool_index is not None:
            tc = tool_calls_by_index.setdefault(
                chunk.tool_index,
                {"id": "", "type": "function",
                 "function": {"name": "", "arguments": ""}},
            )
            if chunk.tool_id:
                tc["id"] = chunk.tool_id
            if chunk.tool_name:
                tc["function"]["name"] = chunk.tool_name
            if chunk.arguments_fragment:
                tc["function"]["arguments"] += chunk.arguments_fragment
        elif chunk.kind == "done":
            usage = chunk.usage or {}

    assistant_message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(text_parts) if text_parts else None,
    }
    if tool_calls_by_index:
        assistant_message["tool_calls"] = [
            tool_calls_by_index[i] for i in sorted(tool_calls_by_index)
        ]
    return assistant_message, usage


@dataclass
class ToolEvent:
    """Event emitted during tool loop execution for UI feedback."""

    kind: str  # "tool_start" | "tool_end" | "llm_start" | "llm_end"
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    result_preview: str | None = None
    iteration: int = 0


# Loop outcomes. Anything other than OUTCOME_OK means ``text`` is a diagnostic
# message, not a model reply — callers must not present it as a normal answer.
OUTCOME_OK = "ok"
OUTCOME_LLM_TIMEOUT = "llm_timeout"
OUTCOME_BUDGET_EXHAUSTED = "budget_exhausted"
OUTCOME_MAX_ITERATIONS = "max_iterations"


@dataclass
class ToolLoopResult:
    """Structured result of a tool loop run: final text plus how it ended."""

    text: str
    outcome: str = OUTCOME_OK
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.outcome == OUTCOME_OK


def coerce_loop_result(value: Any) -> ToolLoopResult:
    """Normalize any historical run_tool_loop return shape into a ToolLoopResult.

    Accepts a ToolLoopResult (passed through), a ``(text, usage)`` tuple
    (track_usage mode), or a bare string — the latter two are assumed OK.
    """
    if isinstance(value, ToolLoopResult):
        return value
    if isinstance(value, tuple) and len(value) == 2:
        return ToolLoopResult(text=value[0], usage=dict(value[1] or {}))
    return ToolLoopResult(text=value if isinstance(value, str) else str(value))


# Per-tool overrides of the generic tool timeout. consult_main needs a full
# main-agent LLM round-trip (semaphore wait + retries), so the generic
# tool_timeout would kill it structurally under load.
TOOL_TIMEOUT_OVERRIDES: dict[str, float] = {
    "consult_main": 180.0,
}

# Per-turn (not per-session) call limits. Tools not listed are unlimited.
DEFAULT_TOOL_LIMITS: dict[str, int] = {
    "web_scrape": 5,
    "web_search": 5,
    "tavily_search": 5,
    "shell": 20,
    "spawn_agent": 5,
    "browser_navigate": 10,
    "browser_click": 20,
    "browser_type": 20,
    "browser_screenshot": 5,
    "browser_eval_js": 10,
}


async def run_tool_loop(
    client: "OpenRouterClient",
    messages: list[dict[str, Any]],
    model: str,
    tools: list[dict[str, Any]],
    tool_registry: "ToolRegistry",
    max_iterations: int = 20,
    executor: ProcessPoolExecutor | None = None,
    track_usage: bool = False,
    tool_limits: dict[str, int | None] | None = None,
    on_event: Callable[[ToolEvent], None] | None = None,
    on_token: Callable[[str], None] | None = None,
    tool_timeout: float = 60.0,
    llm_timeout: float = 120.0,
    token_budget: int = 0,
    return_result: bool = False,
) -> str | tuple[str, dict[str, int]] | ToolLoopResult:
    """Run the model in a tool-calling loop until it produces a final text response.

    Each iteration:
      1. Call the model with the current messages and tool definitions.
      2. If the model returns tool_calls, execute each one and append results.
      3. If the model returns plain text (no tool_calls), return it.
      4. Repeat up to ``max_iterations`` times.

    Tool execution errors are caught and returned to the model as error
    strings so it can attempt recovery.

    Args:
        client: OpenRouter API client.
        messages: Conversation messages (mutated in place with tool calls/results).
        model: Model identifier for OpenRouter.
        tools: Tool JSON schemas in OpenAI function-calling format.
        tool_registry: Registry that resolves and executes tool functions.
        max_iterations: Safety cap on tool-call rounds (default 20).
        executor: Optional ProcessPoolExecutor for blocking tool functions.
        track_usage: If True, accumulate token usage across all API calls and
            return ``(response_text, usage_dict)`` instead of just the text.
        tool_limits: Per-tool call limits for this turn. Merged on top of
            ``DEFAULT_TOOL_LIMITS``; set a value to override a default, or
            ``None`` to remove a default limit (making the tool unlimited).
        tool_timeout: Wall-clock timeout in seconds for each tool execution
            (default 60s). Prevents hung tools from blocking the loop.
        llm_timeout: Wall-clock timeout in seconds for each LLM API call
            (default 120s). Prevents stuck API calls.
        token_budget: Maximum cumulative tokens for the entire loop. 0 means
            no limit. When exceeded, the loop aborts with a message.
        return_result: If True, return a :class:`ToolLoopResult` carrying the
            text, the accumulated usage, and an ``outcome`` marker that
            distinguishes real replies from loop-level failures. Takes
            precedence over ``track_usage``.

    Returns:
        The final text content from the model when ``track_usage`` is False.
        A tuple of ``(text, usage_dict)`` when ``track_usage`` is True, where
        *usage_dict* has keys ``prompt_tokens``, ``completion_tokens``, and
        ``total_tokens``. A :class:`ToolLoopResult` when ``return_result``
        is True.
    """
    merged = {**DEFAULT_TOOL_LIMITS, **(tool_limits or {})}
    effective_limits: dict[str, int] = {
        k: v for k, v in merged.items() if v is not None
    }
    call_counts: dict[str, int] = {}

    total_usage: dict[str, int] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }

    def _accumulate_usage(resp: dict[str, Any]) -> None:
        usage = resp.get("usage", {})
        for key in total_usage:
            total_usage[key] += usage.get(key, 0)

    def _finish(text: str, outcome: str) -> str | tuple[str, dict[str, int]] | ToolLoopResult:
        if return_result:
            return ToolLoopResult(text=text, outcome=outcome, usage=total_usage)
        if track_usage:
            return (text, total_usage)
        return text

    # The client's semaphore serializes LLM calls; acquire it OUTSIDE the
    # llm_timeout window so queueing behind other calls doesn't masquerade
    # as an API timeout. Fake clients in tests have no semaphore attribute.
    llm_semaphore = getattr(client, "semaphore", None)

    for iteration in range(1, max_iterations + 1):
        # Token budget circuit breaker
        if token_budget > 0 and total_usage["total_tokens"] >= token_budget:
            logger.warning(
                "Token budget exceeded (%d/%d) at iteration %d, aborting loop",
                total_usage["total_tokens"],
                token_budget,
                iteration,
            )
            return _finish(
                f"Stopped: token budget exceeded ({total_usage['total_tokens']:,}/{token_budget:,} tokens). "
                "Try a simpler approach or break the task into smaller steps.",
                OUTCOME_BUDGET_EXHAUSTED,
            )

        if on_event is not None:
            on_event(ToolEvent(kind="llm_start", iteration=iteration))

        # Stream the model response, assembling text + tool_calls from deltas
        try:
            async with llm_semaphore or contextlib.nullcontext():
                assistant_message, usage = await asyncio.wait_for(
                    _stream_and_assemble(
                        client, messages, model, tools, on_token,
                        semaphore_held=llm_semaphore is not None,
                    ),
                    timeout=llm_timeout,
                )
        except asyncio.TimeoutError:
            logger.error(
                "Iteration %d: LLM stream timed out after %.0fs",
                iteration, llm_timeout,
            )
            return _finish(
                f"LLM call timed out after {llm_timeout:.0f}s. Try again.",
                OUTCOME_LLM_TIMEOUT,
            )

        # Accumulate usage from this iteration
        for key in total_usage:
            total_usage[key] += usage.get(key, 0)
        if usage:
            logger.info(
                "Iteration %d: tokens prompt=%d completion=%d total=%d cumulative=%d",
                iteration,
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
                usage.get("total_tokens", 0),
                total_usage["total_tokens"],
            )

        if on_event is not None:
            on_event(ToolEvent(kind="llm_end", iteration=iteration))

        tool_calls = assistant_message.get("tool_calls")

        if not tool_calls:
            # No tool calls — streaming already emitted tokens; return the final text.
            content = assistant_message.get("content") or ""
            return _finish(content, OUTCOME_OK)

        # Append the assistant message (with tool_calls) to the conversation
        messages.append(assistant_message)

        # Generate a shared group_id for all spawn_agent calls in this batch
        has_spawn = any(
            tc["function"]["name"] == "spawn_agent" for tc in tool_calls
        )
        batch_group_id = uuid.uuid4().hex[:8] if has_spawn else None

        # Execute each tool call, deferring stop signals until all are done
        stop_reply: str | None = None
        for tool_call in tool_calls:
            call_id = tool_call["id"]
            function = tool_call["function"]
            name = function["name"]
            raw_args = function.get("arguments", "{}")

            # Parse arguments — models sometimes return a string
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                args = {}
                logger.warning(
                    "Iteration %d: failed to parse args for tool %s: %s",
                    iteration,
                    name,
                    raw_args,
                )

            # Inject batch group_id into spawn_agent calls
            if name == "spawn_agent" and batch_group_id is not None:
                args["group_id"] = batch_group_id

            # Enforce per-turn rate limits
            call_counts[name] = call_counts.get(name, 0) + 1
            limit = effective_limits.get(name)
            if limit is not None and call_counts[name] > limit:
                result_str = (
                    f"Rate limit: {name} called {call_counts[name]}/{limit} "
                    "times this turn. Try a different approach."
                )
                logger.warning(
                    "Iteration %d: tool %s exceeded rate limit (%d/%d)",
                    iteration,
                    name,
                    call_counts[name],
                    limit,
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": result_str,
                    }
                )
                continue

            # Fire tool_start event
            if on_event is not None:
                on_event(ToolEvent(
                    kind="tool_start",
                    tool_name=name,
                    tool_args=args,
                    iteration=iteration,
                ))

            # Execute the tool with timeout, catching any exception
            effective_timeout = TOOL_TIMEOUT_OVERRIDES.get(name, tool_timeout)
            t0 = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    tool_registry.execute(name, args, executor),
                    timeout=effective_timeout,
                )
                result_str = str(result) if not isinstance(result, str) else result
                duration_ms = (time.monotonic() - t0) * 1000
                preview = result_str[:200] + "..." if len(result_str) > 200 else result_str
                logger.info(
                    "Iteration %d: tool %s executed in %.0fms: %s",
                    iteration, name, duration_ms, preview,
                )
            except asyncio.TimeoutError:
                duration_ms = (time.monotonic() - t0) * 1000
                result_str = f"Error: tool {name} timed out after {effective_timeout:.0f}s"
                logger.error(
                    "Iteration %d: tool %s timed out after %.0fms",
                    iteration, name, duration_ms,
                )
            except Exception as exc:
                duration_ms = (time.monotonic() - t0) * 1000
                result_str = f"Error executing tool {name}: {exc}"
                logger.error(
                    "Iteration %d: tool %s failed in %.0fms: %s",
                    iteration, name, duration_ms, exc,
                    exc_info=True,
                )

            # Fire tool_end event
            if on_event is not None:
                on_event(ToolEvent(
                    kind="tool_end",
                    tool_name=name,
                    result_preview=result_str[:200],
                    iteration=iteration,
                ))

            # Append tool result to messages
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": result_str,
                }
            )

            # Check for stop signal but defer until all tool calls are done
            try:
                parsed = json.loads(result_str)
                if isinstance(parsed, dict) and parsed.get("__stop_turn__"):
                    logger.info("Tool %s requested turn stop (deferred)", name)
                    if stop_reply is None:
                        stop_reply = parsed.get("reply", result_str)
            except (json.JSONDecodeError, TypeError):
                pass

        # After all tool calls in this batch, honour the deferred stop
        if stop_reply is not None:
            logger.info("Executing deferred turn stop")
            return _finish(stop_reply, OUTCOME_OK)

    # Exhausted max_iterations — make one final call without tools to get a
    # text summary from the model, or return a fallback error.
    logger.warning(
        "Tool loop reached max iterations (%d), requesting final response",
        max_iterations,
    )
    try:
        response = await client.chat(messages, model)
        _accumulate_usage(response)
        choices = response.get("choices")
        if choices:
            content = choices[0]["message"].get("content", "")
            if content:
                # The forced summary is a legitimate model reply.
                return _finish(content, OUTCOME_OK)
    except Exception as exc:
        logger.error("Final call after max iterations failed: %s", exc)

    return _finish(
        f"Reached the maximum of {max_iterations} tool iterations "
        "without a final response.",
        OUTCOME_MAX_ITERATIONS,
    )
