"""Tests for cmd_talk and cmd_voice."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from spare_paw import context as ctx
from spare_paw.core.commands import cmd_talk, cmd_voice
from spare_paw.db import close_db, init_db


@pytest_asyncio.fixture(autouse=True)
async def _fresh_db(tmp_path, monkeypatch):
    db_file = tmp_path / "spare-paw.db"
    monkeypatch.setattr("spare_paw.db.DB_PATH", db_file)
    monkeypatch.setattr("spare_paw.db.DB_DIR", tmp_path)
    monkeypatch.setattr("spare_paw.db._connection", None)
    await init_db()
    yield
    await close_db()


def _app_state(tts_enabled=True, tts_voice="nova"):
    cfg = MagicMock()
    values = {
        "voice.tts_enabled": tts_enabled,
        "voice.tts_voice": tts_voice,
    }
    cfg.get = lambda k, default=None: values.get(k, default)
    return SimpleNamespace(config=cfg)


@pytest.mark.asyncio
async def test_cmd_talk_no_args_reads_current_state_default_off():
    convo = await ctx.new_conversation()
    result = await cmd_talk(_app_state(), convo, [])
    assert "off" in result.lower()


@pytest.mark.asyncio
async def test_cmd_talk_on_sets_flag():
    convo = await ctx.new_conversation()
    result = await cmd_talk(_app_state(), convo, ["on"])
    assert "on" in result.lower()
    meta = await ctx.get_conversation_meta(convo)
    assert meta.get("talk_mode") is True


@pytest.mark.asyncio
async def test_cmd_talk_off_clears_flag():
    convo = await ctx.new_conversation()
    await ctx.set_conversation_meta(convo, "talk_mode", True)
    result = await cmd_talk(_app_state(), convo, ["off"])
    assert "off" in result.lower()
    meta = await ctx.get_conversation_meta(convo)
    assert meta.get("talk_mode") is False


@pytest.mark.asyncio
async def test_cmd_talk_on_refused_when_tts_disabled():
    convo = await ctx.new_conversation()
    result = await cmd_talk(_app_state(tts_enabled=False), convo, ["on"])
    assert "disabled" in result.lower()
    meta = await ctx.get_conversation_meta(convo)
    assert meta.get("talk_mode") is not True


@pytest.mark.asyncio
async def test_cmd_talk_unknown_subcommand_returns_usage():
    convo = await ctx.new_conversation()
    result = await cmd_talk(_app_state(), convo, ["maybe"])
    assert "usage" in result.lower() or "unknown" in result.lower()


@pytest.mark.asyncio
async def test_cmd_voice_no_args_reads_current_from_config():
    convo = await ctx.new_conversation()
    result = await cmd_voice(_app_state(tts_voice="nova"), convo, [])
    assert "nova" in result
    assert "config" in result.lower()


@pytest.mark.asyncio
async def test_cmd_voice_reads_per_convo_override():
    convo = await ctx.new_conversation()
    await ctx.set_conversation_meta(convo, "voice", "shimmer")
    result = await cmd_voice(_app_state(), convo, [])
    assert "shimmer" in result


@pytest.mark.asyncio
async def test_cmd_voice_set_valid_voice():
    convo = await ctx.new_conversation()
    result = await cmd_voice(_app_state(), convo, ["shimmer"])
    assert "shimmer" in result.lower()
    meta = await ctx.get_conversation_meta(convo)
    assert meta.get("voice") == "shimmer"


@pytest.mark.asyncio
async def test_cmd_voice_rejects_unknown_voice():
    convo = await ctx.new_conversation()
    result = await cmd_voice(_app_state(), convo, ["xyz"])
    assert "unknown" in result.lower()
    meta = await ctx.get_conversation_meta(convo)
    assert "voice" not in meta


@pytest.mark.asyncio
async def test_cmd_voice_list_returns_known_voices():
    from spare_paw.router.tts import KNOWN_VOICES
    convo = await ctx.new_conversation()
    result = await cmd_voice(_app_state(), convo, ["list"])
    for v in KNOWN_VOICES:
        assert v in result
