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

        job = s._scheduler.get_job("business_morning_briefing")
        assert job.func is _fire_cron_job

        # Pickling job.func + job.args must round-trip without error — this
        # is exactly what a persistent jobstore needs to do on save.
        restored_func, restored_args = pickle.loads(pickle.dumps((job.func, job.args)))
        assert restored_func is _fire_cron_job
        assert restored_args == ("business", "123", "morning_briefing")

    def test_job_id_uses_agent_and_task(self):
        s = Scheduler()
        event = AgentEvent(
            type=EventType.SCHEDULED_TASK,
            agent_name="devops",
            chat_id="123",
            data={"task": "github_digest"},
        )
        s.add_cron_job(cron="0 9 * * 1-5", event=event)
        assert s._scheduler.get_job("devops_github_digest") is not None


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