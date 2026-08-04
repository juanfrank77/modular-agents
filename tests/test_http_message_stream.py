"""
test_http_message_stream.py
----------------------------
End-to-end coverage for the POST /message/stream SSE endpoint: correct
event content and notifier queue cleanup on normal completion (#30, #33).

Early-disconnect cleanup is covered at the notifier level in
tests/test_http_notifier_modes.py (TestSSEEndpointDisconnectCleanup)
instead of here: an attempt at an endpoint-level disconnect test hung
because it synchronized a slow bus.publish() with an asyncio.Event created
on the test's own event loop, while the endpoint runs on TestClient's
separate portal loop — a test-harness bug, not a finding about whether
TestClient can propagate real disconnects (untested either way).

Run:
    python -m pytest tests/test_http_message_stream.py -x -q
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock

from core.notifier import HTTPNotifier


class _FakeResponse:
    def __init__(self, text: str, agent_name: str, success: bool = True):
        self.text = text
        self.agent_name = agent_name
        self.success = success


def _interface(bus, notifier=None):
    from interfaces.http import HTTPInterface

    safety = MagicMock()
    safety.pairing.code = "000000"
    safety.pairing.verify_code = lambda text: text.strip().lower() == "000000"
    safety.pairing.is_locked = lambda chat_id: False
    safety.pairing._failed_attempts = {}
    safety.pairing.attempts_remaining = lambda chat_id: max(
        0, 5 - safety.pairing._failed_attempts.get(chat_id, 0)
    )

    async def _verify_code_with_lockout(chat_id, text):
        if text.strip().lower() == "000000":
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
    settings.http_pair_rate_limit_rpm = 100
    settings.http_admin_rate_limit_rpm = 100
    creator = MagicMock()
    creator.is_active.return_value = False

    return HTTPInterface(
        bus=bus,
        safety=safety,
        creator=creator,
        notifier=notifier or HTTPNotifier(),
        settings=settings,
        state_store=None,
    )


def _paired_client(interface):
    client = TestClient(interface.app)
    token = client.post("/pair", json={"code": "000000"}).json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client


class TestMessageStreamNormalCompletion:
    def test_final_event_carries_agent_and_success(self):
        notifier = HTTPNotifier()
        bus = MagicMock()
        bus.registered_agents = ["business"]
        bus.publish = AsyncMock(
            return_value=_FakeResponse("hi there", "business", True)
        )
        interface = _interface(bus, notifier)
        client = _paired_client(interface)

        with client.stream(
            "POST", "/message/stream", json={"text": "hello"}
        ) as response:
            events = [
                json.loads(line[len("data: ") :])
                for line in response.iter_lines()
                if line.startswith("data: ")
            ]

        response_events = [e for e in events if e.get("type") == "response"]
        assert response_events, events
        assert response_events[0]["text"] == "hi there"
        assert response_events[0]["agent"] == "business"
        assert response_events[0]["success"] is True
        assert events[-1] == {"type": "done"}

        # Queue must not survive past stream completion.
        assert notifier._queues == {}
        assert notifier._streaming == set()


class TestMessageStreamPublishFailure:
    def test_publish_exception_still_terminates_stream(self):
        """publish_task's completion signal (notify_done + done_event.set())
        must fire from a finally, not as the last line of the try — otherwise
        a raising bus.publish() leaves stream_queue() spinning on its 0.5s
        timeout forever and the client hangs indefinitely."""
        notifier = HTTPNotifier()
        bus = MagicMock()
        bus.registered_agents = ["business"]
        bus.publish = AsyncMock(side_effect=RuntimeError("boom"))
        interface = _interface(bus, notifier)
        client = _paired_client(interface)

        with client.stream(
            "POST", "/message/stream", json={"text": "hello"}
        ) as response:
            events = [
                json.loads(line[len("data: ") :])
                for line in response.iter_lines()
                if line.startswith("data: ")
            ]

        response_events = [e for e in events if e.get("type") == "response"]
        assert response_events, events
        # The exception's own text ("boom") must NOT reach the client —
        # only a generic message. Full detail is server-side log only.
        assert "boom" not in response_events[0]["text"]
        assert response_events[0]["text"]
        assert response_events[0]["success"] is False
        assert events[-1] == {"type": "done"}

        assert notifier._queues == {}
        assert notifier._streaming == set()
