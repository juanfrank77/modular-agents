"""
core/scheduler.py
-----------------
APScheduler wrapper for cron jobs and heartbeats.

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
        self._bus: MessageBus | None = None

    def set_bus(self, bus: "MessageBus") -> None:
        """Set after construction to break circular dependency."""
        self._bus = bus

    def register_schedule(
        self,
        agent_name: str,
        cron_expr: str,
        callback: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        """Add a cron job for a specific agent."""
        trigger = CronTrigger.from_crontab(cron_expr)
        self._scheduler.add_job(callback, trigger, id=f"{agent_name}_{cron_expr}")
        log.info("Schedule registered", event="schedule_add",
                 agent=agent_name, cron=cron_expr)

    async def _heartbeat(self) -> None:
        """Publish a heartbeat tick to all agents."""
        if not self._bus:
            return
        event = AgentEvent(
            type=EventType.HEARTBEAT_TICK,
            agent_name="",
            chat_id="",
        )
        await self._bus.publish_all(event)
        log.info("Heartbeat published", event="heartbeat_tick")

    def start(self) -> None:
        """Start the scheduler and heartbeat."""
        if self._heartbeat_minutes > 0:
            self._scheduler.add_job(
                self._heartbeat,
                IntervalTrigger(minutes=self._heartbeat_minutes),
                id="heartbeat",
            )
        self._scheduler.start()
        log.info("Scheduler started", event="scheduler_start",
                 heartbeat_minutes=self._heartbeat_minutes)

    def stop(self) -> None:
        """Shutdown the scheduler."""
        self._scheduler.shutdown(wait=False)
        log.info("Scheduler stopped", event="scheduler_stop")
