"""
test_http_message.py
--------------------
End-to-end coverage for POST /message and the per-IP rate-limit client-host
resolution behind reverse proxies.

Run:
    python3 -m pytest tests/test_http_message.py -x -q
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from core.protocols import AgentResponse


class _FakeResponse(AgentResponse):
    def __init__(self, text: str, agent_name: str, success: bool = True):
        super().__init__(text=text, agent_name=agent_name, success=success)


def _interface(
    *,
    pairing_code="000000",
    http_trusted_proxies_count=0,
    http_pair_rate_limit_rpm=100,
):
    from interfaces.http import HTTPInterface

    bus = MagicMock()
    bus.registered_agents = ["business"]
    safety = MagicMock()
    safety.pairing.code = pairing_code
    safety.pairing.verify_code = (
        lambda text: text.strip().lower() == pairing_code.lower()
    )
    safety.pairing._failed_attempts = {}
    safety.pairing.is_locked = (
        lambda chat_id: safety.pairing._failed_attempts.get(chat_id, 0) >= 5
    )
    safety.pairing.attempts_remaining = lambda chat_id: max(
        0, 5 - safety.pairing._failed_attempts.get(chat_id, 0)
    )

    async def _verify_code_with_lockout(chat_id, text):
        if safety.pairing.is_locked(chat_id):
            return False
        if text.strip().lower() == pairing_code.lower():
            safety.pairing._failed_attempts.pop(chat_id, None)
            return True
        safety.pairing._failed_attempts[chat_id] = (
            safety.pairing._failed_attempts.get(chat_id, 0) + 1
        )
        return False

    safety.pairing.verify_code_with_lockout = _verify_code_with_lockout
    safety.pairing.pair_directly = AsyncMock()
    safety.rate_limiter.check = MagicMock(return_value=None)

    settings = MagicMock()
    settings.session_ttl_hours = 24
    settings.max_http_sessions = 10
    settings.http_pair_rate_limit_rpm = http_pair_rate_limit_rpm
    settings.http_trusted_proxies_count = http_trusted_proxies_count

    creator = MagicMock()
    creator.is_active.return_value = False

    return HTTPInterface(
        bus=bus,
        safety=safety,
        creator=creator,
        notifier=MagicMock(),
        settings=settings,
        state_store=None,
    )


def _paired_client(interface):
    client = TestClient(interface.app)
    token = client.post(
        "/pair", json={"code": interface._safety.pairing.code}
    ).json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client


class TestHTTPMessageResponse:
    def test_message_returns_agent_text(self):
        interface = _interface()
        interface._bus.publish = AsyncMock(
            return_value=_FakeResponse("agent reply", "business")
        )
        interface._notifier.get_and_clear = AsyncMock(return_value="")
        client = _paired_client(interface)

        r = client.post("/message", json={"text": "hello"})
        assert r.status_code == 200
        data = r.json()
        assert data["response"] == "agent reply"
        assert data["agent"] == "business"
        assert data["success"] is True

    def test_message_empty_text_not_overridden_by_extra(self):
        interface = _interface()
        interface._bus.publish = AsyncMock(
            return_value=_FakeResponse("", "business")
        )
        interface._notifier.get_and_clear = AsyncMock(
            return_value="extra notification"
        )
        client = _paired_client(interface)

        r = client.post("/message", json={"text": "hello"})
        assert r.status_code == 200
        data = r.json()
        assert data["response"] == ""
        assert data["agent"] == "business"
        assert data["success"] is True

    def test_message_falls_back_to_extra_when_no_response(self):
        interface = _interface()
        interface._bus.publish = AsyncMock(return_value=None)
        interface._notifier.get_and_clear = AsyncMock(
            return_value="extra notification"
        )
        client = _paired_client(interface)

        r = client.post("/message", json={"text": "hello"})
        assert r.status_code == 200
        assert r.json()["response"] == "extra notification"

    def test_message_appends_extra_when_response_text_present(self):
        interface = _interface()
        interface._bus.publish = AsyncMock(
            return_value=_FakeResponse("agent reply", "business")
        )
        interface._notifier.get_and_clear = AsyncMock(
            return_value="extra notification"
        )
        client = _paired_client(interface)

        r = client.post("/message", json={"text": "hello"})
        assert r.status_code == 200
        assert r.json()["response"] == "agent reply\n\nextra notification"


class TestHTTPPairClientHostResolution:
    def test_pair_ignores_x_forwarded_for_without_trusted_proxies(self):
        interface = _interface(
            http_pair_rate_limit_rpm=1, http_trusted_proxies_count=0
        )
        client = TestClient(interface.app)
        headers = {"X-Forwarded-For": "1.2.3.4, 5.6.7.8"}

        # Two requests from the same TestClient socket share one bucket.
        r1 = client.post(
            "/pair",
            json={"code": interface._safety.pairing.code},
            headers=headers,
        )
        r2 = client.post(
            "/pair",
            json={"code": interface._safety.pairing.code},
            headers=headers,
        )
        assert r1.status_code == 200
        assert r2.status_code == 429

    def test_pair_uses_x_forwarded_for_with_one_trusted_proxy(self):
        interface = _interface(
            http_pair_rate_limit_rpm=1, http_trusted_proxies_count=1
        )
        client = TestClient(interface.app)
        headers = {"X-Forwarded-For": "1.2.3.4, 5.6.7.8"}

        r1 = client.post(
            "/pair",
            json={"code": interface._safety.pairing.code},
            headers=headers,
        )
        # Same real client IP: rate limited.
        r2 = client.post(
            "/pair",
            json={"code": interface._safety.pairing.code},
            headers=headers,
        )
        assert r1.status_code == 200
        assert r2.status_code == 429

        # Different real client IP: fresh bucket.
        headers2 = {"X-Forwarded-For": "9.9.9.9, 5.6.7.8"}
        r3 = client.post(
            "/pair",
            json={"code": interface._safety.pairing.code},
            headers=headers2,
        )
        assert r3.status_code == 200

    def test_pair_falls_back_to_direct_ip_when_x_forwarded_for_too_short(self):
        interface = _interface(
            http_pair_rate_limit_rpm=1, http_trusted_proxies_count=1
        )
        client = TestClient(interface.app)
        headers = {"X-Forwarded-For": "1.2.3.4"}

        r1 = client.post(
            "/pair",
            json={"code": interface._safety.pairing.code},
            headers=headers,
        )
        r2 = client.post(
            "/pair",
            json={"code": interface._safety.pairing.code},
            headers=headers,
        )
        assert r1.status_code == 200
        assert r2.status_code == 429
