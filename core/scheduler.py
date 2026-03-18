"""
core/scheduler.py
-----------------
APScheduler wrapper for cron jobs and heartbeats.

Two ways to register a job:

  1. register_schedule() — low-level, pass an async callback directly.
     Used when you want full control over what runs.

  2. add_cron_job() — high-level, pass a cron string + AgentEvent.
     The scheduler publishes the event to the bus on schedule.
     This is what agents call from register_schedules().

Usage:
    from core.scheduler import Scheduler
    scheduler = Scheduler(heartbeat_minutes=30)
    scheduler.set_bus(bus)
    scheduler.start()
"""

from __future__ import annotations

from typing import Any, Callable, Coroutine, TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from core.logger import get_logger
from core.protocols import AgentEvent, EventType

if TYPE_CHECKING:
    from core.bus import MessageBus

log = get_logger("scheduler")


class Scheduler:
    def __init__(self, heartbeat_minutes: int = 30) -> None:
        self._scheduler = AsyncIOScheduler()
        self._heartbeat_minutes = heartbeat_minutes
        self._bus: "MessageBus | None" = None

    def set_bus(self, bus: "MessageBus") -> None:
        """Set after construction to break the circular dependency with the bus."""
        self._bus = bus

    # ── Low-level: pass your own callback ─────
    def register_schedule(
        self,
        agent_name: str,
        cron_expr: str,
        callback: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        """Add a cron job that calls an arbitrary async function."""
        trigger = CronTrigger.from_crontab(cron_expr)
        job_id = f"{agent_name}_{cron_expr}"
        self._scheduler.add_job(callback, trigger, id=job_id, replace_existing=True)
        log.info("Schedule registered", event="schedule_add",
                 agent=agent_name, cron=cron_expr)

    # ── High-level: publish an AgentEvent on schedule ──
    def add_cron_job(
        self,
        cron: str,
        event: AgentEvent,
        bus: "MessageBus | None" = None,
    ) -> None:
        """
        Add a cron job that publishes an AgentEvent to the bus on schedule.
        This is the interface agents call from register_schedules().

        Args:
            cron:  Standard cron expression, e.g. "0 7 * * 1-5"
            event: The AgentEvent to publish when the job fires
            bus:   Optional bus override. Falls back to self._bus.
        """
        effective_bus = bus or self._bus

        async def _fire() -> None:
            b = effective_bus or self._bus
            if not b:
                log.warning("Cron job fired but no bus available",
                            event="cron_no_bus", agent=event.agent_name)
                return
            log.info("Cron job firing", event="cron_fire",
                     agent=event.agent_name, task=event.data.get("task", ""))
            await b.publish(event)

        job_id = f"{event.agent_name}_{event.data.get('task', cron)}"
        trigger = CronTrigger.from_crontab(cron)
        self._scheduler.add_job(_fire, trigger, id=job_id, replace_existing=True)
        log.info("Cron job registered", event="cron_add",
                 agent=event.agent_name, cron=cron,
                 task=event.data.get("task", ""))

    # ── Heartbeat ─────────────────────────────
    async def _heartbeat(self) -> None:
        """Publish a heartbeat tick to all registered agents."""
        if not self._bus:
            return
        event = AgentEvent(
            type=EventType.HEARTBEAT_TICK,
            agent_name="",
            chat_id="",
        )
        await self._bus.publish_all(event)
        log.info("Heartbeat published", event="heartbeat_tick")

    # ── Lifecycle ─────────────────────────────
    def start(self) -> None:
        """Start the scheduler. Call after all jobs are registered."""
        if self._heartbeat_minutes > 0:
            self._scheduler.add_job(
                self._heartbeat,
                IntervalTrigger(minutes=self._heartbeat_minutes),
                id="heartbeat",
                replace_existing=True,
            )
        self._scheduler.start()
        log.info("Scheduler started", event="scheduler_start",
                 heartbeat_minutes=self._heartbeat_minutes)

    def stop(self) -> None:
        """Gracefully shut down the scheduler."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        log.info("Scheduler stopped", event="scheduler_stop")


# Module-level singleton — agents import this directly to register cron jobs.
# main.py configures it (heartbeat_minutes, set_bus) before calling start().
scheduler = Scheduler()
