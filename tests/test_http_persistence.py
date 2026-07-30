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


def _interface(
    store,
    pairing_code="000000",
    *,
    session_ttl_hours=24,
    max_http_sessions=10,
    http_pair_rate_limit_rpm=10,
):
    from interfaces.http import HTTPInterface

    bus = MagicMock()
    bus.registered_agents = ["business"]
    safety = MagicMock()
    safety.pairing.code = pairing_code
    # verify_code is a real comparison in production; mock it so tests that
    # supply the correct pairing_code pass and wrong ones fail.
    safety.pairing.verify_code = lambda text: text.strip().lower() == pairing_code.lower()
    safety.pairing.is_locked = lambda chat_id: safety.pairing._locked.get(chat_id, False)
    safety.pairing._locked = {}
    safety.pairing.unlock = MagicMock()
    # pair_directly is async as of Task 3 (core/safety.py) — a plain
    # MagicMock isn't awaitable, so it must be an AsyncMock here.
    safety.pairing.pair_directly = AsyncMock()
    settings = MagicMock()
    settings.session_ttl_hours = session_ttl_hours
    settings.max_http_sessions = max_http_sessions
    settings.http_pair_rate_limit_rpm = http_pair_rate_limit_rpm
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


class TestHTTPSessionCap:
    @pytest.mark.asyncio
    async def test_pair_rejects_when_session_cap_reached(self, store: StateStore):
        interface = _interface(store, pairing_code="secret123", max_http_sessions=2)
        client = TestClient(interface.app)

        # Fill to the cap
        r1 = client.post("/pair", json={"code": "secret123"})
        r2 = client.post("/pair", json={"code": "secret123"})
        assert r1.status_code == 200
        assert r2.status_code == 200

        # Third request should be rejected
        r3 = client.post("/pair", json={"code": "secret123"})
        assert r3.status_code == 503
        assert "Maximum number of active HTTP sessions reached" in r3.json()["detail"]

    @pytest.mark.asyncio
    async def test_pruning_allows_pair_after_expired_sessions_removed(self, store: StateStore):
        interface = _interface(store, pairing_code="secret123", max_http_sessions=1)
        client = TestClient(interface.app)

        r1 = client.post("/pair", json={"code": "secret123"})
        assert r1.status_code == 200
        token = r1.json()["token"]

        # Expire the session in memory
        interface._sessions[token] = (interface._sessions[token][0], time.time() - (25 * 3600))

        # New pair should succeed because the expired session is pruned
        r2 = client.post("/pair", json={"code": "secret123"})
        assert r2.status_code == 200


class TestHTTPPairRateLimit:
    @pytest.mark.asyncio
    async def test_pair_rate_limited_per_ip(self, store: StateStore):
        interface = _interface(store, pairing_code="secret123", http_pair_rate_limit_rpm=2)
        client = TestClient(interface.app)

        r1 = client.post("/pair", json={"code": "secret123"})
        r2 = client.post("/pair", json={"code": "secret123"})
        assert r1.status_code == 200
        assert r2.status_code == 200

        r3 = client.post("/pair", json={"code": "secret123"})
        assert r3.status_code == 429
        assert "Rate limit exceeded" in r3.json()["detail"]

    @pytest.mark.asyncio
    async def test_pair_rate_limits_wrong_code_attempts(self, store: StateStore):
        interface = _interface(store, pairing_code="secret123", http_pair_rate_limit_rpm=2)
        client = TestClient(interface.app)

        # Wrong-code attempts must consume the rate-limit bucket too.
        r1 = client.post("/pair", json={"code": "wrong"})
        r2 = client.post("/pair", json={"code": "wrong"})
        assert r1.status_code == 403
        assert r2.status_code == 403

        r3 = client.post("/pair", json={"code": "wrong"})
        assert r3.status_code == 429
        assert "Rate limit exceeded" in r3.json()["detail"]


class TestHTTPModelEndpoints:
    @pytest.mark.asyncio
    async def test_get_model_returns_override_and_default(self, store: StateStore):
        interface = _interface(store, pairing_code="secret123")
        interface._bus.get_chat_model.return_value = "override-model"
        interface._settings.default_model = "default-model"
        client = TestClient(interface.app)

        token = client.post("/pair", json={"code": "secret123"}).json()["token"]
        r = client.get("/model", headers={"Authorization": f"Bearer {token}"})

        assert r.status_code == 200
        data = r.json()
        assert data["override"] == "override-model"
        assert data["default"] == "default-model"

    @pytest.mark.asyncio
    async def test_post_model_sets_override(self, store: StateStore):
        interface = _interface(store, pairing_code="secret123")
        interface._bus.set_chat_model = AsyncMock()
        client = TestClient(interface.app)

        token = client.post("/pair", json={"code": "secret123"}).json()["token"]
        r = client.post(
            "/model",
            json={"model": "new-model"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert r.status_code == 200
        assert r.json()["status"] == "set"
        interface._bus.set_chat_model.assert_awaited_once_with("http_" + token[:8], "new-model")

    @pytest.mark.asyncio
    async def test_post_model_rejects_empty_model(self, store: StateStore):
        interface = _interface(store, pairing_code="secret123")
        client = TestClient(interface.app)

        token = client.post("/pair", json={"code": "secret123"}).json()["token"]
        r = client.post(
            "/model",
            json={"model": "  "},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_delete_model_clears_override(self, store: StateStore):
        interface = _interface(store, pairing_code="secret123")
        interface._bus.clear_chat_model = AsyncMock()
        client = TestClient(interface.app)

        token = client.post("/pair", json={"code": "secret123"}).json()["token"]
        r = client.delete("/model", headers={"Authorization": f"Bearer {token}"})

        assert r.status_code == 200
        assert r.json()["status"] == "cleared"
        interface._bus.clear_chat_model.assert_awaited_once_with("http_" + token[:8])

    @pytest.mark.asyncio
    async def test_model_endpoints_require_auth(self, store: StateStore):
        interface = _interface(store, pairing_code="secret123")
        client = TestClient(interface.app)

        assert client.get("/model").status_code == 401
        assert client.post("/model", json={"model": "x"}).status_code == 401
        assert client.delete("/model").status_code == 401


class TestHTTPAdminSessionManagement:
    @pytest.mark.asyncio
    async def test_admin_list_sessions_requires_code(self, store: StateStore):
        interface = _interface(store, pairing_code="secret123")
        client = TestClient(interface.app)

        r = client.get("/admin/sessions", params={"code": "wrong"})
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_list_sessions_returns_active_sessions(self, store: StateStore):
        interface = _interface(store, pairing_code="secret123")
        client = TestClient(interface.app)

        r_pair = client.post("/pair", json={"code": "secret123"})
        token = r_pair.json()["token"]

        r = client.get("/admin/sessions", params={"code": "secret123"})
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 1
        assert data["sessions"][0]["token_prefix"] == token[:8]

    @pytest.mark.asyncio
    async def test_admin_revoke_session_requires_valid_token(self, store: StateStore):
        interface = _interface(store, pairing_code="secret123")
        client = TestClient(interface.app)

        r = client.delete("/admin/sessions/not-a-token", params={"code": "secret123"})
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_admin_revoke_session_removes_session(self, store: StateStore):
        interface = _interface(store, pairing_code="secret123")
        client = TestClient(interface.app)

        token = client.post("/pair", json={"code": "secret123"}).json()["token"]

        r = client.delete(f"/admin/sessions/{token}", params={"code": "secret123"})
        assert r.status_code == 200
        assert r.json()["token_prefix"] == token[:8]

        sessions = await store.load_http_sessions()
        assert token not in sessions

    @pytest.mark.asyncio
    async def test_admin_revoke_all_sessions_clears_everything(self, store: StateStore):
        interface = _interface(store, pairing_code="secret123")
        client = TestClient(interface.app)

        client.post("/pair", json={"code": "secret123"})
        client.post("/pair", json={"code": "secret123"})

        r = client.delete("/admin/sessions", params={"code": "secret123"})
        assert r.status_code == 200
        assert r.json()["count"] == 2

        sessions = await store.load_http_sessions()
        assert sessions == {}