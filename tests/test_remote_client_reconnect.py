from __future__ import annotations

import asyncio

import pytest

from spare_paw.cli.client import ConnectionState, RemoteClient


@pytest.mark.asyncio
async def test_initial_state_is_connected_false():
    rc = RemoteClient("http://nowhere:9999", secret="")
    assert rc.connection_state == ConnectionState.DISCONNECTED


@pytest.mark.asyncio
async def test_state_transitions_recorded(monkeypatch):
    rc = RemoteClient("http://nowhere:9999", secret="")
    states: list[ConnectionState] = []
    rc.subscribe_state(lambda s: states.append(s))
    rc._set_state(ConnectionState.RECONNECTING)
    rc._set_state(ConnectionState.CONNECTED)
    assert states == [ConnectionState.RECONNECTING, ConnectionState.CONNECTED]


def test_backoff_sequence_capped():
    rc = RemoteClient("http://nowhere:9999")
    delays = [rc._next_backoff() for _ in range(8)]
    assert delays[:5] == [1.0, 2.0, 4.0, 8.0, 16.0]
    assert all(d <= 30.0 for d in delays)


def test_backoff_resets_on_connected():
    rc = RemoteClient("http://nowhere:9999")
    rc._next_backoff()
    rc._next_backoff()
    rc._next_backoff()
    rc._set_state(ConnectionState.CONNECTED)
    assert rc._next_backoff() == 1.0


@pytest.mark.asyncio
async def test_stream_response_retries_on_drop(monkeypatch):
    """Simulate: first attempt raises ClientConnectionError, second yields an event."""
    import aiohttp
    rc = RemoteClient("http://fake", secret="")

    attempts = {"n": 0}

    class _FakeLineIter:
        def __init__(self, lines): self._lines = lines
        def __aiter__(self): return self._iter()
        async def _iter(self):
            for l in self._lines:
                yield l

    class _FakeResp:
        def __init__(self, lines): self.content = _FakeLineIter(lines); self.status = 200
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def text(self): return ""
        def raise_for_status(self): pass

    class _FakeSession:
        def get(self, *a, **kw):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise aiohttp.ClientConnectionError("drop")
            return _FakeResp([b'data: {"type": "text", "text": "ok"}\n'])

    monkeypatch.setattr(rc, "_get_session", lambda: _FakeSession())

    events: list = []
    async def _consume():
        async for ev in rc.stream_response():
            events.append(ev)

    await asyncio.wait_for(_consume(), timeout=5.0)
    assert events == [{"type": "text", "text": "ok"}]
    assert attempts["n"] == 2
