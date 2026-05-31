"""
main.py
-------
Startup entry point. Wires all components together and runs all three
interfaces simultaneously: Telegram, CLI, and HTTP.

What happens on startup:
  1. Load and validate config from .env
  2. Configure structured logging
  3. Initialise SQLite storage
  4. Build LLM, Memory, Safety, SkillLoader, Scheduler
  5. Create RouterNotifier (dispatches to Telegram/CLI/HTTP by chat_id)
  6. Instantiate and register agents (all receive RouterNotifier)
  7. Run health checks
  8. Print pairing code
  9. Start all three interfaces + scheduler concurrently

Run:
    python main.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from core.bus import MessageBus
from core.config import settings
from core.llm import get_llm_provider
from core.logger import configure_logging, get_logger
from core.memory import Memory
from core.notifier import CLINotifier, HTTPNotifier, RouterNotifier, TelegramNotifier
from core.protocols import Message
from core.safety import Safety
from core.scheduler import Scheduler, scheduler as _scheduler
from core.skill_loader import SkillLoader
from core.storage import Storage
from core.agent_creator import AgentCreator
from agents.business.agent import BusinessAgent
from agents.devops.agent import DevOpsAgent
from agents.echo.agent import EchoAgent
from agents.wellbeing.agent import WellbeingAgent
from interfaces.telegram import TelegramInterface
from interfaces.cli import CLIInterface
from interfaces.http import HTTPInterface

log = get_logger("main")


# ──────────────────────────────────────────────
# Bootstrap
# ──────────────────────────────────────────────


async def bootstrap():
    """Initialise all components and return wired-up objects."""

    configure_logging(level=settings.log_level, fmt=settings.log_format)
    log.info("Framework starting", event="startup")

    storage = Storage(settings.db_path)
    await storage.init()

    telegram_notifier = TelegramNotifier(token=settings.telegram_token)
    cli_notifier = CLINotifier()
    http_notifier = HTTPNotifier()

    router = RouterNotifier(default=telegram_notifier)
    router.register_prefix("cli", cli_notifier)
    router.register_prefix("http_", http_notifier)

    llm = get_llm_provider()
    await _verify_llm(llm)

    creator = AgentCreator(llm=llm, project_root=Path("."))
    memory = Memory(storage=storage, llm=llm, settings=settings)

    safety = Safety(
        notifier=router,
        allowed_ids=settings.telegram_allowed_chat_ids,
        approval_timeouts=settings.approval_timeouts,
        extra_blocked_patterns=settings.extra_blocked_patterns,
    )

    skill_loader = SkillLoader()
    _scheduler._heartbeat_minutes = settings.heartbeat_interval_minutes

    bus = MessageBus()
    _scheduler.set_bus(bus)

    agent_kwargs = dict(
        settings=settings,
        storage=storage,
        notifier=router,
        llm=llm,
        memory=memory,
        safety=safety,
        skill_loader=skill_loader,
        bus=bus,
    )

    business = BusinessAgent(**agent_kwargs)
    bus.register(business)

    devops = DevOpsAgent(**agent_kwargs)
    bus.register(devops)

    echo = EchoAgent(settings=settings, storage=storage, notifier=router)
    bus.register(echo)

    wellbeing = WellbeingAgent(settings=settings, storage=storage, notifier=router)
    bus.register(wellbeing)

    for agent in [business, devops, echo, wellbeing]:
        try:
            await agent.register_schedules(bus)
        except Exception as e:
            log.warning(
                "Failed to register schedules",
                event="schedule_reg_error",
                agent=agent.name,
                error=str(e),
            )

    health = await bus.health_check_all()
    all_healthy = True
    for agent_name, healthy in health.items():
        if healthy:
            log.info("Agent healthy", event="health_ok", agent=agent_name)
        else:
            log.warning("Agent unhealthy", event="health_fail", agent=agent_name)
            all_healthy = False

    if not all_healthy:
        log.warning("Some agents failed health checks", event="startup_degraded")

    log.info("Bootstrap complete", event="startup_complete", agents=bus.registered_agents)

    return bus, safety, creator, cli_notifier, http_notifier


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────


async def _verify_llm(llm) -> None:
    """Validate the LLM key works at startup. Exits with a clear message if not."""
    try:
        await llm.complete(
            messages=[Message(role="user", content="ping")],
            system="Reply with one word: ok",
            max_tokens=5,
        )
        log.info("LLM connectivity verified", event="llm_verified")
    except Exception as e:
        print(
            f"\n[startup] FATAL: LLM API call failed — check your API key and connectivity.\n"
            f"  Error: {e}\n"
            f"  Set KILO_API_KEY (or ANTHROPIC_API_KEY) in your .env file.\n"
        )
        sys.exit(1)


async def _run_http_safe(interface: HTTPInterface) -> None:
    """Run the HTTP interface, logging failures without crashing other interfaces."""
    try:
        await interface.run()
    except Exception as e:
        log.warning(
            "HTTP interface failed to start or crashed",
            event="http_error",
            error=str(e),
        )


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────


async def main() -> None:
    bus, safety, creator, cli_notifier, http_notifier = await bootstrap()

    print(f"\n{'=' * 52}")
    print(f"  PAIRING CODE:  {safety.pairing.code}")
    print("  Send this code to the bot on Telegram to pair.")
    print("  Or POST it to /pair on the HTTP API.")
    print(f"{'=' * 52}\n")

    telegram_interface = TelegramInterface(
        bus=bus, safety=safety, creator=creator, settings=settings
    )
    cli_interface = CLIInterface(
        bus=bus, safety=safety, creator=creator, notifier=cli_notifier
    )
    http_interface = HTTPInterface(
        bus=bus, safety=safety, creator=creator, notifier=http_notifier, settings=settings
    )

    _scheduler.start()
    try:
        await asyncio.gather(
            telegram_interface.run(),
            cli_interface.run(),
            _run_http_safe(http_interface),
        )
    except asyncio.CancelledError:
        pass
    finally:
        log.info("Shutting down", event="shutdown_start")
        _scheduler.stop()
        log.info("Shutdown complete", event="shutdown_complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutdown requested", event="shutdown")
        sys.exit(0)