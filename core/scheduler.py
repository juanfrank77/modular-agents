"""
core/scheduler.py
-----------------
APScheduler wrapper for cron jobs and heartbeats.

Two ways to register a job:

  1. register_schedule() — low-level, pass an async callback directly.
     Used when you want full control over what runs.

  2. add_cron_job() — high-level, pass a cron string + AgentEvent.
     The scheduler publishes the event to the bus on schedule.
     BaseAgent iterates over SCHEDULES and calls add_cron_job() automatically.

Usage:
    from core.scheduler import Scheduler
    scheduler = Scheduler(heartbeat_minutes=30)
    scheduler.set_bus(bus)
    scheduler.start()
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Coroutine, TYPE_CHECKING

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from core.logger import get_logger
from core.protocols import AgentEvent, EventType
from core.timezone import load_timezone

if TYPE_CHECKING:
    from core.bus import MessageBus

log = get_logger("scheduler")

# Module-level registry so persisted (pickled) jobs can find the live bus
# after a restart — a bound method or closure over `bus` isn't picklable,
# but a plain function looking up a module global is.
_bus_registry: "MessageBus | None" = None


def _set_bus_registry(bus: "MessageBus") -> None:
    global _bus_registry
    _bus_registry = bus


async def _fire_cron_job(agent_name: str, chat_id: str, task: str) -> None:
    """The picklable target every add_cron_job()-registered job points at.
    
    Wrapped in error handling so one job's failure doesn't crash the scheduler.
    """
    if _bus_registry is None:
        log.warning("Cron job fired but no bus available", event="cron_no_bus", agent=agent_name)
        return
    event = AgentEvent(
        type=EventType.SCHEDULED_TASK,
        agent_name=agent_name,
        chat_id=chat_id,
        data={"task": task},
    )
    log.info("Cron job firing", event="cron_fire", agent=agent_name, task=task)
    try:
        await _bus_registry.publish(event)
    except Exception as e:
        log.error("Cron job execution failed", event="cron_job_error", agent=agent_name, task=task, error=str(e))


async def _heartbeat_tick() -> None:
    """Picklable module-level heartbeat target.

    A bound method (``self._heartbeat``) drags the entire ``Scheduler``
    instance — which owns the non-serializable ``AsyncIOScheduler`` — into
    the pickle, so SQLAlchemyJobStore crashes on ``scheduler.start()``.
    Keeping this at module level and looking up the bus via the registry
    avoids that, mirroring ``_fire_cron_job``.
    """
    if _bus_registry is None:
        return
    event = AgentEvent(
        type=EventType.HEARTBEAT_TICK,
        agent_name="",
        chat_id="",
    )
    await _bus_registry.publish_all(event)
    log.info("Heartbeat published", event="heartbeat_tick")


class Scheduler:
    def __init__(self, heartbeat_minutes: int = 30) -> None:
        self._scheduler = AsyncIOScheduler()
        self._heartbeat_minutes = heartbeat_minutes
        self._bus: "MessageBus | None" = None
        # Default to UTC; main.py calls set_timezone() once settings are loaded.
        self._timezone = load_timezone(None)

    def set_bus(self, bus: "MessageBus") -> None:
        """Set after construction to break the circular dependency with the bus."""
        self._bus = bus
        _set_bus_registry(bus)

    def set_heartbeat_minutes(self, minutes: int) -> None:
        """Set after construction — the module-level singleton is built
        before Settings is available, matching set_bus()'s pattern."""
        self._heartbeat_minutes = minutes

    def set_timezone(self, timezone_name: str) -> None:
        """Set the timezone used for cron triggers.

        Called from main.py after settings are loaded. Invalid names fall
        back to UTC via load_timezone().
        """
        self._timezone = load_timezone(timezone_name)
        log.info(
            "Scheduler timezone set",
            event="scheduler_timezone_set",
            timezone=str(self._timezone),
        )

    def configure_jobstore(self, db_path: Path) -> None:
        """Swap the default in-memory jobstore for a persistent one backed by
        SQLite. Must be called before any add_cron_job()/add_job() calls —
        jobs registered before this point live only in the old jobstore.
        Uses its own, unencrypted database file (never settings.db_path):
        SQLAlchemyJobStore's synchronous driver can't open a SQLCipher-
        encrypted file, and job data isn't sensitive."""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        if "default" in self._scheduler._jobstores:
            self._scheduler.remove_jobstore("default")
        self._scheduler.add_jobstore(
            SQLAlchemyJobStore(url=f"sqlite:///{db_path}"), alias="default"
        )
        log.info("Scheduler jobstore configured", event="jobstore_configured", db_path=str(db_path))

    # ── Low-level: pass your own callback ─────
    def register_schedule(
        self,
        agent_name: str,
        cron_expr: str,
        callback: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        """Add a cron job that calls an arbitrary async function."""
        trigger = CronTrigger.from_crontab(cron_expr, timezone=self._timezone)
        # Include agent name in ID to prevent collisions when multiple agents
        # use the same cron expression
        job_id = f"{agent_name}_schedule_{cron_expr}"
        self._scheduler.add_job(callback, trigger, id=job_id, replace_existing=True)
        log.info(
            "Schedule registered",
            event="schedule_add",
            agent=agent_name,
            cron=cron_expr,
            timezone=str(self._timezone),
        )

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
        if bus is not None:
            self.set_bus(bus)

        task = event.data.get("task", cron)
        # Use agent+task+cron as composite key to prevent collisions
        # when different agents have tasks with the same name
        job_id = f"{event.agent_name}_{task}_{cron}"
        trigger = CronTrigger.from_crontab(cron, timezone=self._timezone)
        self._scheduler.add_job(
            _fire_cron_job,
            trigger,
            id=job_id,
            replace_existing=True,
            args=[event.agent_name, event.chat_id, task],
            misfire_grace_time=3600,
        )
        log.info(
            "Cron job registered",
            event="cron_add",
            agent=event.agent_name,
            cron=cron,
            task=task,
            timezone=str(self._timezone),
        )

    # ── Lifecycle ─────────────────────────────
    def start(self) -> None:
        """Start the scheduler. Call after all jobs are registered."""
        if self._heartbeat_minutes > 0:
            self._scheduler.add_job(
                _heartbeat_tick,
                IntervalTrigger(minutes=self._heartbeat_minutes),
                id="heartbeat",
                replace_existing=True,
            )
        self._scheduler.start()
        log.info(
            "Scheduler started",
            event="scheduler_start",
            heartbeat_minutes=self._heartbeat_minutes,
        )

    def stop(self) -> None:
        """Gracefully shut down the scheduler."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        log.info("Scheduler stopped", event="scheduler_stop")


# Module-level singleton — agents import this directly to register cron jobs.
# main.py configures it (heartbeat_minutes, set_bus) before calling start().
scheduler = Scheduler()