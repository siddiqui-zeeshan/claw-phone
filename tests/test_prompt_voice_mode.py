"""Tests for voice-mode hint injection in build_system_prompt."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from spare_paw.core.prompt import VOICE_MODE_HINT, build_system_prompt


def _fake_config():
    cfg = MagicMock()
    cfg.get = lambda k, default=None: {
        "agent.system_prompt": "base prompt",
    }.get(k, default)
    return cfg


@pytest.mark.asyncio
async def test_build_system_prompt_omits_voice_hint_by_default():
    result = await build_system_prompt(_fake_config())
    assert VOICE_MODE_HINT not in result


@pytest.mark.asyncio
async def test_build_system_prompt_includes_voice_hint_when_enabled():
    result = await build_system_prompt(_fake_config(), voice_mode=True)
    assert VOICE_MODE_HINT in result


@pytest.mark.asyncio
async def test_voice_mode_hint_appears_exactly_once():
    result = await build_system_prompt(_fake_config(), voice_mode=True)
    assert result.count(VOICE_MODE_HINT) == 1


@pytest.mark.asyncio
async def test_voice_mode_hint_mentions_key_constraints():
    assert "voice" in VOICE_MODE_HINT.lower()
    assert "markdown" in VOICE_MODE_HINT.lower()
    assert "code" in VOICE_MODE_HINT.lower()
