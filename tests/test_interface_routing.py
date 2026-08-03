"""
test_interface_routing.py
----------------------------
Tests that all three interfaces (cli, telegram, http) route an explicit
'@agent' tag identically, via core.routing.parse_agent_tag.

Run:
    python -m pytest tests/test_interface_routing.py -x -q
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.protocols import AgentResponse


class TestCLIInterfaceTagParsing:
    def test_make_event_strips_known_tag(self):
        from interfaces.cli import CLIInterface

        bus = MagicMock()
        bus.registered_agents = ["business", "devops"]
        cli = CLIInterface(
            bus=bus, safety=MagicMock(), creator=MagicMock(), notifier=MagicMock()
        )

        event = cli._make_event("@devops restart it")
        assert event.agent_name == "devops"
        assert event.text == "restart it"

    def test_make_event_no_tag(self):
        from interfaces.cli import CLIInterface

        bus = MagicMock()
        bus.registered_agents = ["business", "devops"]
        cli = CLIInterface(
            bus=bus, safety=MagicMock(), creator=MagicMock(), notifier=MagicMock()
        )

        event = cli._make_event("hello there")
        assert event.agent_name == ""
        assert event.text == "hello there"


class TestTelegramInterfaceTagParsing:
    @pytest.mark.asyncio
    async def test_on_message_strips_known_tag(self):
        from interfaces.telegram import TelegramInterface

        bus = MagicMock()
        bus.registered_agents = ["business", "devops"]
        bus.send_thinking = AsyncMock(return_value=None)
        bus.clear_thinking = AsyncMock(return_value=None)
        bus.publish = AsyncMock(
            return_value=AgentResponse(text="ok", agent_name="devops")
        )

        safety = MagicMock()
        safety.pairing.is_paired.return_value = True
        safety.rate_limiter.check.return_value = None

        creator = MagicMock()
        creator.is_active.return_value = False

        settings = MagicMock()
        telegram = TelegramInterface(
            bus=bus, safety=safety, creator=creator, settings=settings
        )

        update = MagicMock()
        update.message.chat_id = 123
        update.message.text = "@devops restart it"

        await telegram._on_message(update, MagicMock())

        bus.publish.assert_awaited_once()
        published_event = bus.publish.call_args.args[0]
        assert published_event.agent_name == "devops"
        assert published_event.text == "restart it"

    @pytest.mark.asyncio
    async def test_on_message_no_tag(self):
        from interfaces.telegram import TelegramInterface

        bus = MagicMock()
        bus.registered_agents = ["business", "devops"]
        bus.send_thinking = AsyncMock(return_value=None)
        bus.clear_thinking = AsyncMock(return_value=None)
        bus.publish = AsyncMock(
            return_value=AgentResponse(text="ok", agent_name="business")
        )

        safety = MagicMock()
        safety.pairing.is_paired.return_value = True
        safety.rate_limiter.check.return_value = None

        creator = MagicMock()
        creator.is_active.return_value = False

        telegram = TelegramInterface(
            bus=bus, safety=safety, creator=creator, settings=MagicMock()
        )

        update = MagicMock()
        update.message.chat_id = 123
        update.message.text = "hello there"

        await telegram._on_message(update, MagicMock())

        published_event = bus.publish.call_args.args[0]
        assert published_event.agent_name == ""
        assert published_event.text == "hello there"


class TestHTTPInterfaceTagParsing:
    def _client(self, bus):
        from interfaces.http import HTTPInterface
        from fastapi.testclient import TestClient

        safety = MagicMock()
        safety.pairing.code = "000000"
        # verify_code is a real comparison in production; mirror it here so
        # the /pair exchange in _client() succeeds with the expected code.
        safety.pairing.verify_code = lambda text: text.strip().lower() == "000000"
        safety.pairing._failed_attempts = {}

        async def _verify_code_with_lockout(chat_id, text):
            if text.strip().lower() == "000000":
                safety.pairing._failed_attempts.pop(chat_id, None)
                return True
            safety.pairing._failed_attempts[chat_id] = (
                safety.pairing._failed_attempts.get(chat_id, 0) + 1
            )
            return False

        safety.pairing.verify_code_with_lockout = _verify_code_with_lockout
        safety.pairing.is_locked = lambda chat_id: safety.pairing._failed_attempts.get(chat_id, 0) >= 5
        safety.pairing.attempts_remaining = lambda chat_id: max(
            0, 5 - safety.pairing._failed_attempts.get(chat_id, 0)
        )
        # pair_directly is async as of Task 3 (core/safety.py) — a plain
        # MagicMock isn't awaitable, so it must be an AsyncMock here.
        safety.pairing.pair_directly = AsyncMock()
        safety.rate_limiter.check.return_value = None

        settings = MagicMock()
        settings.session_ttl_hours = 24
        settings.max_http_sessions = 10
        settings.http_pair_rate_limit_rpm = 10

        creator = MagicMock()
        creator.is_active.return_value = False

        notifier = MagicMock()
        notifier.get_and_clear = AsyncMock(return_value="")

        interface = HTTPInterface(
            bus=bus, safety=safety, creator=creator, notifier=notifier, settings=settings
        )
        client = TestClient(interface.app)
        r = client.post("/pair", json={"code": "000000"})
        token = r.json()["token"]
        return client, {"Authorization": f"Bearer {token}"}

    def test_message_strips_known_tag_when_agent_field_empty(self):
        bus = MagicMock()
        bus.registered_agents = ["business", "devops"]
        bus.publish = AsyncMock(
            return_value=AgentResponse(text="ok", agent_name="devops")
        )

        client, headers = self._client(bus)
        r = client.post(
            "/message", json={"text": "@devops restart it"}, headers=headers
        )
        assert r.status_code == 200
        published_event = bus.publish.call_args.args[0]
        assert published_event.agent_name == "devops"
        assert published_event.text == "restart it"

    def test_message_explicit_agent_field_wins_over_text(self):
        bus = MagicMock()
        bus.registered_agents = ["business", "devops"]
        bus.publish = AsyncMock(
            return_value=AgentResponse(text="ok", agent_name="business")
        )

        client, headers = self._client(bus)
        r = client.post(
            "/message",
            json={"text": "@devops restart it", "agent": "business"},
            headers=headers,
        )
        assert r.status_code == 200
        published_event = bus.publish.call_args.args[0]
        assert published_event.agent_name == "business"
        assert published_event.text == "@devops restart it"