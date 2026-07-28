from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.echo.agent import EchoAgent


def _make_agent(allowed_chat_ids: list[str]):
    settings = MagicMock()
    settings.echo_agent_model = ""
    settings.telegram_allowed_chat_ids = allowed_chat_ids
    notifier = MagicMock()
    notifier.send = AsyncMock()
    agent = EchoAgent(
        settings=settings,
        storage=MagicMock(),
        notifier=notifier,
        bus=None,
    )
    agent.SCHEDULES = [("test_task", "0 9 * * *")]
    return agent


@pytest.mark.asyncio
class TestRegisterSchedules:
    async def test_skips_registration_when_no_chat_ids_configured(self):
        agent = _make_agent(allowed_chat_ids=[])

        with patch("core.scheduler.scheduler") as mock_scheduler:
            await agent.register_schedules(bus=MagicMock())

            mock_scheduler.add_cron_job.assert_not_called()

    async def test_registers_when_chat_ids_configured(self):
        agent = _make_agent(allowed_chat_ids=["12345"])

        with patch("core.scheduler.scheduler") as mock_scheduler:
            await agent.register_schedules(bus=MagicMock())

            mock_scheduler.add_cron_job.assert_called_once()
            _, kwargs = mock_scheduler.add_cron_job.call_args
            assert kwargs["event"].chat_id == "12345"

    async def test_no_schedules_defined_is_a_noop_regardless_of_chat_ids(self):
        agent = _make_agent(allowed_chat_ids=[])
        agent.SCHEDULES = []

        with patch("core.scheduler.scheduler") as mock_scheduler:
            await agent.register_schedules(bus=MagicMock())

            mock_scheduler.add_cron_job.assert_not_called()