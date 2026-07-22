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


def _interface(store, pairing_code="000000"):
    from interfaces.http import HTTPInterface

    bus = MagicMock()
    bus.registered_agents = ["business"]
    safety = MagicMock()
    safety.pairing.code = pairing_code
    safety.pairing.is_locked = lambda chat_id: safety.pairing._locked.get(chat_id, False)
    safety.pairing._locked = {}
    safety.pairing.unlock = MagicMock()
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


class TestHTTPSessionRuntimePruning:
    @pytest.mark.asyncio
    async def test_expired_session_pruned_on_access(self, store: StateStore):
        # Create a valid session, load it, then expire it in memory
        valid_ts = time.time()
        await store.save_http_session("tok-soon", "http_abcd1234", valid_ts)
        interface = _interface(store)

        await interface.load_sessions()
        assert "tok-soon" in interface._sessions

        # Manually expire the session in memory (simulate time passing)
        interface._sessions["tok-soon"] = ("http_abcd1234", time.time() - (25 * 3600))

        # Accessing an expired session should prune it
        result = interface._is_session_valid("tok-soon")

        assert result is False
        assert "tok-soon" not in interface._sessions

    @pytest.mark.asyncio
    async def test_valid_session_not_pruned(self, store: StateStore):
        await store.save_http_session("tok-valid", "http_abcd1234", time.time())
        interface = _interface(store)

        await interface.load_sessions()
        assert interface._is_session_valid("tok-valid") is True
        assert "tok-valid" in interface._sessions


class TestAdminUnlock:
    @pytest.mark.asyncio
    async def test_unlock_endpoint_requires_valid_code(self, store: StateStore):
        interface = _interface(store, pairing_code="secret123")
        client = TestClient(interface.app)

        r = client.post("/admin/unlock", json={"code": "wrong", "chat_id": "123"})
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_unlock_unlocks_locked_chat(self, store: StateStore):
        interface = _interface(store, pairing_code="secret123")
        client = TestClient(interface.app)
        interface._safety.pairing._locked["123"] = True

        r = client.post("/admin/unlock", json={"code": "secret123", "chat_id": "123"})
        assert r.status_code == 200
        assert r.json()["status"] == "unlocked"
        interface._safety.pairing.unlock.assert_called_once_with("123")

    @pytest.mark.asyncio
    async def test_unlock_fails_for_unlocked_chat(self, store: StateStore):
        interface = _interface(store, pairing_code="secret123")
        client = TestClient(interface.app)
        interface._safety.pairing._locked["123"] = False

        r = client.post("/admin/unlock", json={"code": "secret123", "chat_id": "123"})
        assert r.status_code == 400