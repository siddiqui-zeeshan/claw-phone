"""Platform-agnostic message processor.

Handles message processing, agent callbacks, and the message queue.
No Telegram imports — all platform-specific behavior is delegated
to the MessageBackend.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from spare_paw import context as ctx_module
from spare_paw.config import resolve_model
from spare_paw.context import compact_with_retry
from spare_paw.core import voice as voice_module
from spare_paw.core.planner import create_plan
from spare_paw.core.prompt import build_system_prompt
from spare_paw.core.vision import describe_media
from spare_paw.core.voice import VoiceTranscriptionError
from spare_paw.core.voice_out import VoiceRenderError, render_voice_note
from spare_paw.router.tool_loop import coerce_loop_result, run_tool_loop

if TYPE_CHECKING:
    from spare_paw.backend import IncomingMessage, MessageBackend

logger = logging.getLogger(__name__)

# Module-level queue — initialized by start_queue_processor
_message_queue: asyncio.Queue | None = None
_queue_task: asyncio.Task | None = None


def split_text(text: str, max_length: int) -> list[str]:
    """Split text into chunks of at most *max_length* characters.

    Prefers splitting at newlines, falling back to hard cuts.
    """
    if not text:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break

        cut_at = text.rfind("\n", 0, max_length)
        if cut_at <= 0:
            cut_at = max_length

        chunks.append(text[:cut_at])
        text = text[cut_at:].lstrip("\n")

    return chunks


async def process_message(
    app_state: Any,
    msg: IncomingMessage,
    backend: MessageBackend,
) -> None:
    """Process a single user message end-to-end.

    1. Extract text from message (voice transcription if needed)
    2. Handle image (base64 encode)
    3. Assemble context window
    4. Inject cron context if present
    5. Run tool loop
    6. Ingest response + send via backend
    7. Background LCM compaction
    """
    ctx = ctx_module
    t0 = time.perf_counter()

    # 1. Determine text content
    text = msg.text
    media_description = None

    if msg.voice_bytes:
        try:
            config_data = getattr(app_state.config, "data", app_state.config)
            text = await voice_module.transcribe(msg.voice_bytes, config_data)
        except VoiceTranscriptionError:
            logger.exception("Voice transcription failed")
            try:
                await backend.send_text(
                    "⚠️ I couldn't transcribe that voice note — mind typing it "
                    "or sending it again?"
                )
            except Exception:
                logger.exception("Failed to send transcription-failure notice")
            return

    # 2. Vision preprocessing for images/videos
    media_bytes = msg.image_bytes or msg.video_bytes
    if media_bytes:
        media_mime = msg.video_mime if msg.video_bytes else msg.image_mime
        user_text = msg.text or msg.caption
        vision_model = resolve_model(app_state.config, "vision")
        media_description = await describe_media(
            client=app_state.router_client,
            media_bytes=media_bytes,
            media_mime=media_mime,
            user_text=user_text,
            model=vision_model,
        )
        if not text:
            text = msg.caption or "Sent media"

    if not text:
        return

    # Store current request for subagent dialogue channels
    app_state.current_request = text

    # 2. Get or create conversation
    conversation_id = await ctx.get_or_create_conversation()

    # 3. Ingest user message
    await ctx.ingest(conversation_id, "user", text)

    # 4. Assemble context
    voice_mode = bool((await ctx.get_conversation_meta(conversation_id)).get("talk_mode"))
    system_prompt = await build_system_prompt(app_state.config, voice_mode=voice_mode)
    messages = await ctx.assemble(conversation_id, system_prompt)

    # 5. Inject media description
    if media_description:
        messages.append({
            "role": "user",
            "content": f"[Media analysis]: {media_description}",
        })

    # 6. Cron context injection
    if msg.cron_context:
        messages.append({
            "role": "user",
            "content": (
                f"[Context: The user is replying to a cron job result. "
                f"Original cron output:\n{msg.cron_context}]"
            ),
        })

    # 7. Planning phase (only for /plan messages)
    if msg.plan:
        on_event_plan = getattr(backend, "on_tool_event", None)
        if on_event_plan is not None:
            from spare_paw.router.tool_loop import ToolEvent
            on_event_plan(ToolEvent(kind="plan_start"))

        plan_text = await create_plan(messages, app_state.config, app_state.router_client)

        if on_event_plan is not None:
            on_event_plan(ToolEvent(kind="plan_end"))

        if plan_text:
            messages.append({
                "role": "system",
                "content": f"[Plan]\n{plan_text}\n\n"
                "Follow this plan step by step. Use parallel agent spawning "
                "where the plan indicates steps are independent.",
            })

    # 8. Run tool loop (duck-type callbacks from the backend)
    on_event = getattr(backend, "on_tool_event", None)
    on_token = getattr(backend, "on_token", None)

    model = resolve_model(app_state.config, "main_agent")
    tool_schemas = app_state.tool_registry.get_schemas()
    max_iterations = app_state.config.get("agent.max_tool_iterations", 20)

    loop_result = coerce_loop_result(await run_tool_loop(
        client=app_state.router_client,
        messages=messages,
        model=model,
        tools=tool_schemas,
        tool_registry=app_state.tool_registry,
        max_iterations=max_iterations,
        executor=app_state.executor,
        return_result=True,
        on_event=on_event,
        on_token=on_token,
    ))
    response_text = loop_result.text

    if not loop_result.ok:
        # Loop-level failure — tell the user honestly, but never record the
        # diagnostic as an assistant reply in conversation history.
        logger.error("Turn failed (outcome=%s): %s", loop_result.outcome, response_text)
        try:
            await backend.send_text(f"⚠️ I hit a problem with that: {response_text}")
        except Exception:
            logger.exception("Failed to send turn-failure notice")
        return

    # 9. Ingest assistant response
    await ctx.ingest(conversation_id, "assistant", response_text)

    # 10. LCM compaction in background
    summary_model = resolve_model(app_state.config, "summary")
    asyncio.create_task(
        compact_with_retry(conversation_id, app_state.router_client, summary_model),
        name="lcm-compact",
    )

    # 11. Send response via backend — voice or text based on conversation state
    elapsed = time.perf_counter() - t0
    logger.info("Message processed in %.1fs (%d chars response)", elapsed, len(response_text))

    if not response_text:
        return

    meta = await ctx.get_conversation_meta(conversation_id)
    should_voice = (
        bool(meta.get("talk_mode")) or (msg.voice_bytes is not None)
    )
    tts_enabled = app_state.config.get("voice.tts_enabled", True)
    max_chars = app_state.config.get("voice.tts_max_chars", 2000)

    if should_voice and tts_enabled and len(response_text) <= max_chars:
        voice_name = meta.get("voice") or app_state.config.get("voice.tts_voice", "nova")
        try:
            ogg = await render_voice_note(response_text, voice_name, app_state.config)
            await backend.send_voice(ogg)
            if meta.get("voice_error_notified"):
                await ctx.set_conversation_meta(conversation_id, "voice_error_notified", False)
            return
        except VoiceRenderError as exc:
            logger.warning("TTS failed, falling back to text: %s", exc)
            if not meta.get("voice_error_notified"):
                try:
                    await backend.send_text(
                        "🔇 Voice generation failed. Falling back to text."
                    )
                except Exception:
                    logger.exception("Failed to send TTS-failure notice")
                await ctx.set_conversation_meta(
                    conversation_id, "voice_error_notified", True,
                )
            await backend.send_text(response_text)
            return

    if should_voice and tts_enabled and len(response_text) > max_chars:
        await backend.send_text(
            "⚠️ reply too long for voice\n\n" + response_text
        )
        return

    await backend.send_text(response_text)


async def process_agent_callback(
    app_state: Any,
    synthetic_text: str,
    backend: MessageBackend,
) -> None:
    """Process a synthetic agent callback by feeding results to the main LLM.

    The main LLM synthesizes a coherent response from the agent results
    and sends it to the user via the backend.
    """
    ctx = ctx_module

    try:
        conversation_id = await ctx.get_or_create_conversation()

        augmented_text = (
            f"{synthetic_text}\n\n"
            "[INSTRUCTIONS] The above are structured results from background agents you spawned.\n"
            "- If all agents returned status 'complete': present the FULL findings to the user "
            "— include all details, data, links, and comparisons. Do NOT summarize into a single sentence.\n"
            "- If any agent returned NEEDS_INFO: ask the user the agent's question before presenting partial results.\n"
            "- If any agent FAILED: report the failure honestly, then try a different approach or ask the user.\n"
            "Format the response clearly."
        )
        await ctx.ingest(conversation_id, "user", augmented_text)

        system_prompt = await build_system_prompt(app_state.config)
        messages = await ctx.assemble(conversation_id, system_prompt)

        model = resolve_model(app_state.config, "main_agent")
        # The synthesis turn must not spawn more agents — completed agents
        # triggering new spawns would allow unbounded agent chains.
        tool_schemas = [
            s for s in app_state.tool_registry.get_schemas()
            if s.get("function", {}).get("name") != "spawn_agent"
        ]
        max_iterations = app_state.config.get("agent.max_tool_iterations", 20)

        loop_result = coerce_loop_result(await run_tool_loop(
            client=app_state.router_client,
            messages=messages,
            model=model,
            tools=tool_schemas,
            tool_registry=app_state.tool_registry,
            max_iterations=max_iterations,
            executor=app_state.executor,
            return_result=True,
        ))
        response_text = loop_result.text

        if not loop_result.ok:
            logger.error(
                "Agent-callback synthesis failed (outcome=%s)", loop_result.outcome,
            )
            await backend.send_text(
                "⚠️ Your background agents finished, but I hit a problem "
                f"writing up the results: {response_text}\n"
                "The raw results are saved — ask me to look them up."
            )
            return

        await ctx.ingest(conversation_id, "assistant", response_text)
        await backend.send_text(response_text)

    except Exception:
        logger.exception("Failed to handle agent callback")
        try:
            await backend.send_text(
                "Agent results received but I failed to process them. "
                "Use /search to find the raw results."
            )
        except Exception:
            logger.exception("Failed to send agent callback error")


# ---------------------------------------------------------------------------
# Queue management
# ---------------------------------------------------------------------------


async def enqueue(item: IncomingMessage | tuple) -> None:
    """Put a message or agent callback on the processing queue."""
    if _message_queue is not None:
        await _message_queue.put(item)
    else:
        logger.error("enqueue() called but queue not initialized — item DROPPED: %s", type(item))


def queue_healthy() -> bool:
    """True while the queue consumer task is alive (or not started yet).

    The gateway heartbeat gates the systemd watchdog ping on this, so a dead
    consumer no longer looks like a healthy process.
    """
    return _queue_task is None or not _queue_task.done()


def start_queue_processor(app_state: Any, backend: MessageBackend) -> None:
    """Start the background task that drains the message queue."""
    global _message_queue, _queue_task
    _message_queue = asyncio.Queue()
    _queue_task = asyncio.create_task(_process_queue(app_state, backend))

    # Share the queue with the subagent module for callbacks and start watchdog
    from spare_paw.tools import subagent as subagent_mod
    subagent_mod._message_queue = _message_queue
    subagent_mod.start_watchdog()

    logger.info("Message queue processor started")


async def stop_queue_processor(drain_timeout: float = 8.0) -> None:
    """Drain outstanding queue items (bounded), then cancel the consumer."""
    global _queue_task
    if _message_queue is not None:
        try:
            await asyncio.wait_for(_message_queue.join(), timeout=drain_timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "Queue not drained after %.0fs — cancelling with %d item(s) pending",
                drain_timeout, _message_queue.qsize(),
            )
    if _queue_task is not None and not _queue_task.done():
        _queue_task.cancel()
        try:
            await _queue_task
        except asyncio.CancelledError:
            pass
    logger.info("Message queue processor stopped")


async def _mark_callback_delivered(callback_id: int) -> None:
    """Flag a persisted agent callback as delivered (best-effort)."""
    try:
        from spare_paw.db import get_db, is_initialized
        if not is_initialized():
            return
        db = await get_db()
        await db.execute(
            "UPDATE agent_callbacks SET delivered = 1 WHERE id = ?", (callback_id,)
        )
        await db.commit()
    except Exception:
        logger.exception("Failed to mark callback %s delivered", callback_id)


async def recover_orphans(app_state: Any, backend: MessageBackend) -> None:
    """Startup reconciliation after a restart.

    Marks agents that were running when the process died as interrupted (and
    tells the user which ones), then re-enqueues any persisted callbacks that
    were never processed.
    """
    try:
        from spare_paw.db import get_db, is_initialized
        if not is_initialized():
            return
        db = await get_db()

        async with db.execute(
            "SELECT id, name FROM agents WHERE status IN ('starting', 'running')"
        ) as cur:
            orphans = await cur.fetchall()
        if orphans:
            now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
            await db.execute(
                "UPDATE agents SET status = 'interrupted', "
                "error = 'Interrupted by restart', finished_at = ? "
                "WHERE status IN ('starting', 'running')",
                (now,),
            )
            await db.commit()
            names = ", ".join(row["name"] or row["id"] for row in orphans)
            logger.warning("Recovered %d orphaned agent(s): %s", len(orphans), names)
            try:
                await backend.send_text(
                    f"⚠️ A restart interrupted {len(orphans)} background "
                    f"agent(s): {names}. Ask me again if you still need them."
                )
            except Exception:
                logger.exception("Failed to send orphan-recovery notice")

        async with db.execute(
            "SELECT id, payload FROM agent_callbacks WHERE delivered = 0"
        ) as cur:
            pending = await cur.fetchall()
        for row in pending:
            logger.info("Re-enqueueing undelivered agent callback %d", row["id"])
            await enqueue(("agent_callback", row["payload"], row["id"]))
    except Exception:
        logger.exception("Orphan recovery failed")


async def _handle_queue_item(
    app_state: Any, item: Any, backend: MessageBackend
) -> None:
    """Process one queue item with a wall-clock ceiling and error reporting."""
    from spare_paw.backend import IncomingMessage as _IncomingMessage

    try:
        timeout_s = float(app_state.config.get("agent.message_timeout_seconds", 600) or 600)
    except (TypeError, ValueError):
        timeout_s = 600.0

    try:
        if isinstance(item, tuple) and len(item) >= 2 and item[0] == "agent_callback":
            await asyncio.wait_for(
                process_agent_callback(app_state, item[1], backend),
                timeout=timeout_s,
            )
            if len(item) > 2 and item[2] is not None:
                await _mark_callback_delivered(item[2])
        elif isinstance(item, _IncomingMessage):
            # Start typing indicator
            await backend.send_typing()
            await asyncio.wait_for(
                process_message(app_state, item, backend),
                timeout=timeout_s,
            )
        else:
            logger.warning("Unknown item type in queue: %s", type(item))
    except asyncio.TimeoutError:
        logger.error("Queue item timed out after %.0fs: %s", timeout_s, type(item))
        try:
            await backend.send_text(
                "⚠️ That took too long and I had to stop. Try again, or break "
                "it into smaller steps."
            )
        except Exception:
            logger.exception("Failed to send timeout notice")
    except Exception:
        logger.exception("Unhandled error processing queue item")
        try:
            await backend.send_text(
                "An internal error occurred. Please try again."
            )
        except Exception:
            logger.exception("Failed to send error reply")


async def _process_queue(app_state: Any, backend: MessageBackend) -> None:
    """Drain the message queue, processing one message at a time."""
    assert _message_queue is not None

    while True:
        try:
            item = await _message_queue.get()
            try:
                await _handle_queue_item(app_state, item, backend)
            finally:
                _message_queue.task_done()
        except asyncio.CancelledError:
            logger.info("Message queue processor cancelled")
            break
        except Exception:
            logger.exception("Fatal error in queue processor loop")
            await asyncio.sleep(1)
