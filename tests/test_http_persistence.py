"""
test_http_persistence.py
----------------------------
Tests for HTTPInterface session persistence via StateStore: write-through
on pair/delete, and rehydration (with expiry pruning) at startup.

Run:
    python -m pytest tests/test_http_persistence.py -x -q
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from core.state_store import StateStore


@pytest.fixture
async def store(tmp_path: Path) -> StateStore:
    s = StateStore(tmp_path / "state.db")
    await s.init()
    return s


def _interface(store):
    from interfaces.http import HTTPInterface

    bus = MagicMock()
    bus.registered_agents = ["business"]
    safety = MagicMock()
    safety.pairing.code = "000000"
    # pair_directly is async as of Task 3 (core/safety.py) — a plain
    # MagicMock isn't awaitable, so it must be an AsyncMock here.
    safety.pairing.pair_directly = AsyncMock()
    settings = MagicMock()
    settings.session_ttl_hours = 24
    creator = MagicMock()
    creator.is_active.return_value = False

    return HTTPInterface(
        bus=bus, safety=safety, creator=creator, notifier=MagicMock(),
        settings=settings, state_store=store,
    )


class TestHTTPSessionWriteThrough:
    @pytest.mark.asyncio
    async def test_pair_writes_through(self, store: StateStore):
        interface = _interface(store)
        client = TestClient(interface.app)
        r = client.post("/pair", json={"code": "000000"})
        token = r.json()["token"]

        sessions = await store.load_http_sessions()
        assert token in sessions

    @pytest.mark.asyncio
    async def test_delete_session_writes_through(self, store: StateStore):
        interface = _interface(store)
        client = TestClient(interface.app)
        token = client.post("/pair", json={"code": "000000"}).json()["token"]

        client.delete("/session", headers={"Authorization": f"Bearer {token}"})

        sessions = await store.load_http_sessions()
        assert token not in sessions


class TestHTTPSessionRehydration:
    @pytest.mark.asyncio
    async def test_load_sessions_restores_valid_tokens(self, store: StateStore):
        await store.save_http_session("tok-valid", "http_abcd1234", time.time())
        interface = _interface(store)

        await interface.load_sessions()

        assert interface._is_session_valid("tok-valid") is True

    @pytest.mark.asyncio
    async def test_load_sessions_drops_expired_tokens(self, store: StateStore):
        stale_ts = time.time() - (25 * 3600)  # older than 24h TTL
        await store.save_http_session("tok-stale", "http_abcd1234", stale_ts)
        interface = _interface(store)

        await interface.load_sessions()

        assert interface._is_session_valid("tok-stale") is False
        remaining = await store.load_http_sessions()
        assert "tok-stale" not in remaining