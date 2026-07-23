"""
test_telegram_interface.py
----------------------------
Interface-level behavior tests for TelegramInterface methods that have no
coverage yet: _on_callback, _on_model, _on_planmode, and _on_command.

Run:
    python -m pytest tests/test_telegram_interface.py -x -q
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.protocols import AgentResponse


def _make_telegram(bus=None, safety=None, creator=None, settings=None):
    from interfaces.telegram import TelegramInterface

    return TelegramInterface(
        bus=bus or MagicMock(),
        safety=safety or MagicMock(),
        creator=creator or MagicMock(),
        settings=settings or MagicMock(),
    )


class TestOnCallback:
    @pytest.mark.asyncio
    async def test_approve_callback_resolves_gate_and_edits_message(self):
        safety = MagicMock()
        bus = MagicMock()
        bus.registered_agents = []
        bus.send_thinking = AsyncMock(return_value=None)
        bus.clear_thinking = AsyncMock(return_value=None)
        bus.publish = AsyncMock(return_value=AgentResponse(text="ok", agent_name=""))

        telegram = _make_telegram(bus=bus, safety=safety)

        query = AsyncMock()
        query.data = "approve:abc-123"
        update = MagicMock()
        update.callback_query = query

        await telegram._on_callback(update, MagicMock())

        query.answer.assert_awaited_once()
        query.edit_message_text.assert_awaited_once_with("Approved.")
        safety.gate.resolve.assert_called_once_with("abc-123", approved=True)

    @pytest.mark.asyncio
    async def test_deny_callback_resolves_gate_and_edits_message(self):
        safety = MagicMock()
        bus = MagicMock()
        bus.registered_agents = []
        bus.send_thinking = AsyncMock(return_value=None)
        bus.clear_thinking = AsyncMock(return_value=None)
        bus.publish = AsyncMock(return_value=AgentResponse(text="ok", agent_name=""))

        telegram = _make_telegram(bus=bus, safety=safety)

        query = AsyncMock()
        query.data = "deny:xyz-789"
        update = MagicMock()
        update.callback_query = query

        await telegram._on_callback(update, MagicMock())

        query.answer.assert_awaited_once()
        query.edit_message_text.assert_awaited_once_with("Denied.")
        safety.gate.resolve.assert_called_once_with("xyz-789", approved=False)

    @pytest.mark.asyncio
    async def test_missing_query_returns_early(self):
        telegram = _make_telegram()

        update = MagicMock()
        update.callback_query = None

        await telegram._on_callback(update, MagicMock())


class TestOnModel:
    @pytest.mark.asyncio
    async def test_shows_model_when_no_args(self):
        safety = MagicMock()
        safety.pairing.is_paired.return_value = True
        bus = MagicMock()
        bus.registered_agents = []
        bus.send_thinking = AsyncMock(return_value=None)
        bus.clear_thinking = AsyncMock(return_value=None)
        bus.publish = AsyncMock(return_value=AgentResponse(text="ok", agent_name=""))

        settings = MagicMock()
        settings.default_model = "claude-3-5-sonnet"

        telegram = _make_telegram(bus=bus, safety=safety, settings=settings)

        update = MagicMock()
        update.message.chat_id = 123
        update.message.reply_text = AsyncMock()
        update.message.text = "/model"

        context = MagicMock()
        context.args = []

        await telegram._on_model(update, context)

        update.message.reply_text.assert_awaited_once()
        assert "claude-3-5-sonnet" in update.message.reply_text.call_args.args[0]

    @pytest.mark.asyncio
    async def test_sets_model_when_args_provided(self):
        safety = MagicMock()
        safety.pairing.is_paired.return_value = True
        bus = MagicMock()
        bus.registered_agents = []
        bus.send_thinking = AsyncMock(return_value=None)
        bus.clear_thinking = AsyncMock(return_value=None)
        bus.publish = AsyncMock(return_value=AgentResponse(text="ok", agent_name=""))

        settings = MagicMock()
        settings.default_model = "old-model"

        telegram = _make_telegram(bus=bus, safety=safety, settings=settings)

        update = MagicMock()
        update.message.chat_id = 123
        update.message.reply_text = AsyncMock()
        update.message.text = "/model new-model"

        context = MagicMock()
        context.args = ["new-model"]

        await telegram._on_model(update, context)

        assert settings.default_model == "new-model"
        update.message.reply_text.assert_awaited_once()
        assert "new-model" in update.message.reply_text.call_args.args[0]

    @pytest.mark.asyncio
    async def test_blocks_when_not_paired(self):
        safety = MagicMock()
        safety.pairing.is_paired.return_value = False

        class FakeSettings:
            def __init__(self):
                self.default_model = "original-model"

        settings = FakeSettings()

        telegram = _make_telegram(safety=safety, settings=settings)

        update = MagicMock()
        update.message.chat_id = 123
        update.message.reply_text = AsyncMock()

        await telegram._on_model(update, MagicMock())

        update.message.reply_text.assert_awaited_once_with("🔒 Not paired.")
        assert settings.default_model == "original-model"


class TestOnPlanmode:
    @pytest.mark.asyncio
    async def test_toggles_all_agents_when_no_arg(self):
        safety = MagicMock()
        safety.pairing.is_paired.return_value = True
        bus = MagicMock()
        bus.send_thinking = AsyncMock(return_value=None)
        bus.clear_thinking = AsyncMock(return_value=None)
        bus.publish = AsyncMock(return_value=AgentResponse(text="ok", agent_name=""))

        business_agent = MagicMock()
        business_agent.toggle_plan_mode.return_value = True
        devops_agent = MagicMock()
        devops_agent.toggle_plan_mode.return_value = False

        bus.registered_agents = ["business", "devops"]

        def get_agent(name):
            if name == "business":
                return business_agent
            if name == "devops":
                return devops_agent
            return None

        bus.get_agent.side_effect = get_agent

        telegram = _make_telegram(bus=bus, safety=safety)

        update = MagicMock()
        update.message.chat_id = 123
        update.message.reply_text = AsyncMock()

        context = MagicMock()
        context.args = []

        await telegram._on_planmode(update, context)

        business_agent.toggle_plan_mode.assert_called_once_with("123")
        devops_agent.toggle_plan_mode.assert_called_once_with("123")
        update.message.reply_text.assert_awaited_once()
        text = update.message.reply_text.call_args.args[0]
        assert "business: Plan mode ON" in text
        assert "devops: Plan mode OFF" in text

    @pytest.mark.asyncio
    async def test_toggles_single_agent_when_arg_provided(self):
        safety = MagicMock()
        safety.pairing.is_paired.return_value = True
        bus = MagicMock()
        bus.send_thinking = AsyncMock(return_value=None)
        bus.clear_thinking = AsyncMock(return_value=None)
        bus.publish = AsyncMock(return_value=AgentResponse(text="ok", agent_name=""))

        business_agent = MagicMock()
        business_agent.toggle_plan_mode.return_value = True
        devops_agent = MagicMock()

        bus.registered_agents = ["business", "devops"]

        def get_agent(name):
            if name == "business":
                return business_agent
            if name == "devops":
                return devops_agent
            return None

        bus.get_agent.side_effect = get_agent

        telegram = _make_telegram(bus=bus, safety=safety)

        update = MagicMock()
        update.message.chat_id = 123
        update.message.reply_text = AsyncMock()

        context = MagicMock()
        context.args = ["business"]

        await telegram._on_planmode(update, context)

        business_agent.toggle_plan_mode.assert_called_once_with("123")
        devops_agent.toggle_plan_mode.assert_not_called()
        update.message.reply_text.assert_awaited_once_with("business: Plan mode ON")

    @pytest.mark.asyncio
    async def test_reports_missing_agent(self):
        safety = MagicMock()
        safety.pairing.is_paired.return_value = True
        bus = MagicMock()
        bus.send_thinking = AsyncMock(return_value=None)
        bus.clear_thinking = AsyncMock(return_value=None)
        bus.publish = AsyncMock(return_value=AgentResponse(text="ok", agent_name=""))

        bus.registered_agents = ["business", "devops"]
        bus.get_agent.return_value = None

        telegram = _make_telegram(bus=bus, safety=safety)

        update = MagicMock()
        update.message.chat_id = 123
        update.message.reply_text = AsyncMock()

        context = MagicMock()
        context.args = ["missing"]

        await telegram._on_planmode(update, context)

        update.message.reply_text.assert_awaited_once()
        text = update.message.reply_text.call_args.args[0]
        assert "No agent named 'missing'" in text
        assert "business" in text
        assert "devops" in text

    @pytest.mark.asyncio
    async def test_blocks_when_not_paired(self):
        safety = MagicMock()
        safety.pairing.is_paired.return_value = False

        telegram = _make_telegram(safety=safety)

        update = MagicMock()
        update.message.chat_id = 123
        update.message.reply_text = AsyncMock()

        await telegram._on_planmode(update, MagicMock())

        update.message.reply_text.assert_awaited_once_with("🔒 Not paired.")


class TestOnCommand:
    @pytest.mark.asyncio
    async def test_help_returns_help_text(self):
        safety = MagicMock()
        safety.pairing.is_paired.return_value = True
        bus = MagicMock()
        bus.send_thinking = AsyncMock(return_value=None)
        bus.clear_thinking = AsyncMock(return_value=None)
        bus.publish = AsyncMock(return_value=AgentResponse(text="ok", agent_name=""))
        bus.send_notification = AsyncMock()

        creator = MagicMock()
        creator.is_active.return_value = False

        telegram = _make_telegram(bus=bus, safety=safety, creator=creator)

        update = MagicMock()
        update.message.chat_id = 123
        update.message.text = "/help"

        await telegram._on_command(update, MagicMock())

        bus.send_notification.assert_awaited_once()
        body = bus.send_notification.call_args.args[1]
        assert "Available commands" in body
        assert "/newagent" in body
        assert "/planmode" in body
        assert "/help" in body

    @pytest.mark.asyncio
    async def test_newagent_delegates_to_creator(self):
        safety = MagicMock()
        safety.pairing.is_paired.return_value = True
        bus = MagicMock()
        bus.send_thinking = AsyncMock(return_value=None)
        bus.clear_thinking = AsyncMock(return_value=None)
        bus.publish = AsyncMock(return_value=AgentResponse(text="ok", agent_name=""))
        bus.send_notification = AsyncMock()

        creator = MagicMock()
        creator.is_active.return_value = False
        creator.handle = AsyncMock(return_value="wizard response")

        telegram = _make_telegram(bus=bus, safety=safety, creator=creator)

        update = MagicMock()
        update.message.chat_id = 123
        update.message.text = "/newagent"

        await telegram._on_command(update, MagicMock())

        creator.handle.assert_awaited_once_with("123", "/newagent")
        bus.send_notification.assert_awaited_once_with("123", "wizard response")

    @pytest.mark.asyncio
    async def test_newagent_when_creator_already_active(self):
        safety = MagicMock()
        safety.pairing.is_paired.return_value = True
        bus = MagicMock()
        bus.send_thinking = AsyncMock(return_value=None)
        bus.clear_thinking = AsyncMock(return_value=None)
        bus.publish = AsyncMock(return_value=AgentResponse(text="ok", agent_name=""))
        bus.send_notification = AsyncMock()

        creator = MagicMock()
        creator.is_active.return_value = True
        creator.handle = AsyncMock(return_value="continue wizard")

        telegram = _make_telegram(bus=bus, safety=safety, creator=creator)

        update = MagicMock()
        update.message.chat_id = 123
        update.message.text = "wizard input"

        await telegram._on_command(update, MagicMock())

        creator.handle.assert_awaited_once_with("123", "wizard input")
        bus.send_notification.assert_awaited_once_with("123", "continue wizard")

    @pytest.mark.asyncio
    async def test_blocks_when_not_paired(self):
        safety = MagicMock()
        safety.pairing.is_paired.return_value = False
        bus = MagicMock()
        bus.send_notification = AsyncMock()

        telegram = _make_telegram(bus=bus, safety=safety)

        update = MagicMock()
        update.message.chat_id = 123
        update.message.text = "/help"

        await telegram._on_command(update, MagicMock())

        bus.send_notification.assert_awaited_once()
        assert "pairing token" in bus.send_notification.call_args.args[1].lower()
