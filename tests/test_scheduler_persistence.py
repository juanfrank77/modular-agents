"""
test_scheduler_persistence.py
---------------------------------
Tests that Scheduler.add_cron_job registers a picklable job (module-level
function + plain-string args, not a closure), and that configure_jobstore
swaps in a persistent SQLAlchemyJobStore.

Run:
    python -m pytest tests/test_scheduler_persistence.py -x -q
"""

from __future__ import annotations

import pickle
from pathlib import Path

import pytest

from core.protocols import AgentEvent, EventType
from core.scheduler import Scheduler, _fire_cron_job
from zoneinfo import ZoneInfo


class TestAddCronJobIsPicklable:
    def test_job_func_and_args_round_trip_through_pickle(self):
        s = Scheduler()
        event = AgentEvent(
            type=EventType.SCHEDULED_TASK,
            agent_name="business",
            chat_id="123",
            data={"task": "morning_briefing"},
        )
        s.add_cron_job(cron="0 8 * * 1-5", event=event)

        job = s._scheduler.get_job("business_morning_briefing_0 8 * * 1-5")
        assert job.func is _fire_cron_job

        # Pickling job.func + job.args must round-trip without error — this
        # is exactly what a persistent jobstore needs to do on save.
        restored_func, restored_args = pickle.loads(pickle.dumps((job.func, job.args)))
        assert restored_func is _fire_cron_job
        assert restored_args == ("business", "123", "morning_briefing")

    def test_job_id_uses_agent_task_and_cron(self):
        s = Scheduler()
        event = AgentEvent(
            type=EventType.SCHEDULED_TASK,
            agent_name="devops",
            chat_id="123",
            data={"task": "github_digest"},
        )
        s.add_cron_job(cron="0 9 * * 1-5", event=event)
        assert s._scheduler.get_job("devops_github_digest_0 9 * * 1-5") is not None

    def test_job_ids_are_unique_per_agent(self):
        """Two agents with same task name should not collide."""
        s = Scheduler()
        event1 = AgentEvent(
            type=EventType.SCHEDULED_TASK,
            agent_name="business",
            chat_id="123",
            data={"task": "daily_summary"},
        )
        event2 = AgentEvent(
            type=EventType.SCHEDULED_TASK,
            agent_name="wellbeing",
            chat_id="456",
            data={"task": "daily_summary"},
        )
        s.add_cron_job(cron="0 8 * * 1-5", event=event1)
        s.add_cron_job(cron="0 9 * * 1-5", event=event2)
        
        # Both jobs should exist with distinct IDs
        assert s._scheduler.get_job("business_daily_summary_0 8 * * 1-5") is not None
        assert s._scheduler.get_job("wellbeing_daily_summary_0 9 * * 1-5") is not None


class TestFireCronJobUsesRegistry:
    @pytest.mark.asyncio
    async def test_fires_through_registered_bus(self):
        from unittest.mock import AsyncMock

        s = Scheduler()
        bus = AsyncMock()
        s.set_bus(bus)

        await _fire_cron_job("business", "123", "morning_briefing")

        bus.publish.assert_awaited_once()
        published_event = bus.publish.call_args.args[0]
        assert published_event.agent_name == "business"
        assert published_event.chat_id == "123"
        assert published_event.data == {"task": "morning_briefing"}

    @pytest.mark.asyncio
    async def test_noop_when_no_bus_registered(self):
        import core.scheduler as scheduler_module

        scheduler_module._bus_registry = None
        # Must not raise even with no bus set.
        await _fire_cron_job("business", "123", "morning_briefing")

    @pytest.mark.asyncio
    async def test_error_isolation_on_publish_failure(self):
        """Cron job should log error but not crash on publish failure."""
        from unittest.mock import AsyncMock

        s = Scheduler()
        bus = AsyncMock()
        bus.publish = AsyncMock(side_effect=RuntimeError("publish failed"))
        s.set_bus(bus)

        # Should not raise
        await _fire_cron_job("business", "123", "morning_briefing")
        
        # Publish was attempted
        bus.publish.assert_awaited_once()


class TestConfigureJobstore:
    def test_swaps_default_jobstore(self, tmp_path: Path):
        s = Scheduler()
        s.configure_jobstore(tmp_path / "scheduler.db")

        from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

        assert isinstance(s._scheduler._jobstores["default"], SQLAlchemyJobStore)


class TestSetHeartbeatMinutes:
    """Public setter for the module-level singleton — main.py previously
    reached into scheduler._heartbeat_minutes directly (private attr)."""

    def test_constructor_default(self):
        s = Scheduler()
        assert s._heartbeat_minutes == 30

    def test_public_setter_updates_value(self):
        s = Scheduler()
        s.set_heartbeat_minutes(15)
        assert s._heartbeat_minutes == 15


class TestSetTimezone:
    """Scheduler timezone controls when cron expressions fire."""

    def test_constructor_defaults_to_utc(self):
        s = Scheduler()
        assert s._timezone == ZoneInfo("UTC")

    def test_set_timezone_updates_zone(self):
        s = Scheduler()
        s.set_timezone("America/Denver")
        assert s._timezone == ZoneInfo("America/Denver")

    def test_set_timezone_falls_back_to_utc_for_invalid_name(self):
        s = Scheduler()
        s.set_timezone("NotAReal/Timezone")
        assert s._timezone == ZoneInfo("UTC")

    def test_add_cron_job_uses_user_timezone(self):
        s = Scheduler()
        s.set_timezone("America/Denver")
        event = AgentEvent(
            type=EventType.SCHEDULED_TASK,
            agent_name="wellbeing",
            chat_id="123",
            data={"task": "morning_nudge"},
        )
        s.add_cron_job(cron="0 7 * * *", event=event)
        job = s._scheduler.get_job("wellbeing_morning_nudge_0 7 * * *")
        assert job.trigger.timezone == ZoneInfo("America/Denver")
