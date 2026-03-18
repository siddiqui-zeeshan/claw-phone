"""Tests for claw_phone.db — schema init, FTS5 triggers, close."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio

import claw_phone.db as db_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
async def _isolate_db(tmp_path):
    """Redirect DB_DIR / DB_PATH to a temp directory and reset the singleton."""
    tmp_db_dir = tmp_path / ".claw-phone"
    tmp_db_path = tmp_db_dir / "claw.db"
    tmp_db_dir.mkdir()

    with (
        patch.object(db_mod, "DB_DIR", tmp_db_dir),
        patch.object(db_mod, "DB_PATH", tmp_db_path),
    ):
        # Ensure we start with a fresh singleton
        db_mod._connection = None
        yield
        # Teardown: close whatever was opened
        await db_mod.close_db()


# ---------------------------------------------------------------------------
# init_db / schema
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_init_db_creates_tables():
    await db_mod.init_db()
    conn = await db_mod.get_db()

    # Query sqlite_master for our expected tables
    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ) as cur:
        tables = {row[0] for row in await cur.fetchall()}

    assert "messages" in tables
    assert "conversations" in tables
    assert "cron_jobs" in tables
    assert "messages_fts" in tables


@pytest.mark.asyncio
async def test_schema_version_is_set():
    await db_mod.init_db()
    conn = await db_mod.get_db()

    async with conn.execute("PRAGMA user_version") as cur:
        row = await cur.fetchone()

    assert row[0] == db_mod.CURRENT_SCHEMA_VERSION


@pytest.mark.asyncio
async def test_init_db_is_idempotent():
    """Calling init_db twice should not raise or reset data."""
    await db_mod.init_db()
    conn = await db_mod.get_db()
    # Insert a row
    msg_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO messages (id, conversation_id, role, content, token_count, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (msg_id, "conv1", "user", "hello", 1, datetime.now(timezone.utc).isoformat()),
    )
    await conn.commit()

    # Re-init should not wipe data (version already == 1)
    await db_mod.init_db()
    async with conn.execute("SELECT id FROM messages WHERE id = ?", (msg_id,)) as cur:
        row = await cur.fetchone()
    assert row is not None


# ---------------------------------------------------------------------------
# FTS5 triggers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fts_insert_trigger():
    """Inserting a message should auto-populate the FTS index via trigger."""
    await db_mod.init_db()
    conn = await db_mod.get_db()

    msg_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO messages (id, conversation_id, role, content, token_count, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (msg_id, "conv1", "user", "The quick brown fox jumps", 5,
         datetime.now(timezone.utc).isoformat()),
    )
    await conn.commit()

    # FTS match query
    async with conn.execute(
        "SELECT content FROM messages_fts WHERE messages_fts MATCH 'quick brown'"
    ) as cur:
        rows = await cur.fetchall()

    assert len(rows) == 1
    assert "quick brown fox" in rows[0][0]


@pytest.mark.asyncio
async def test_fts_delete_trigger():
    """Deleting a message should remove it from the FTS index."""
    await db_mod.init_db()
    conn = await db_mod.get_db()

    msg_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO messages (id, conversation_id, role, content, token_count, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (msg_id, "conv1", "user", "uniqueword12345", 1,
         datetime.now(timezone.utc).isoformat()),
    )
    await conn.commit()

    # Delete the message
    await conn.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
    await conn.commit()

    async with conn.execute(
        "SELECT content FROM messages_fts WHERE messages_fts MATCH 'uniqueword12345'"
    ) as cur:
        rows = await cur.fetchall()

    assert len(rows) == 0


@pytest.mark.asyncio
async def test_fts_update_trigger():
    """Updating message content should update the FTS index."""
    await db_mod.init_db()
    conn = await db_mod.get_db()

    msg_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO messages (id, conversation_id, role, content, token_count, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (msg_id, "conv1", "user", "oldcontent999", 1,
         datetime.now(timezone.utc).isoformat()),
    )
    await conn.commit()

    # Update content
    await conn.execute(
        "UPDATE messages SET content = ? WHERE id = ?",
        ("newcontent888", msg_id),
    )
    await conn.commit()

    # Old content gone
    async with conn.execute(
        "SELECT content FROM messages_fts WHERE messages_fts MATCH 'oldcontent999'"
    ) as cur:
        assert len(await cur.fetchall()) == 0

    # New content present
    async with conn.execute(
        "SELECT content FROM messages_fts WHERE messages_fts MATCH 'newcontent888'"
    ) as cur:
        rows = await cur.fetchall()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# close_db
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_close_db():
    await db_mod.init_db()
    assert db_mod._connection is not None

    await db_mod.close_db()
    assert db_mod._connection is None


@pytest.mark.asyncio
async def test_close_db_when_not_open():
    """close_db should be a no-op when there is no connection."""
    db_mod._connection = None
    await db_mod.close_db()  # should not raise
    assert db_mod._connection is None
