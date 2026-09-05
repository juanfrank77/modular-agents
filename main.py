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
  5. Create RouterNotifier (dispatches by explicit chat_id registration —
     CLI's fixed chat_id here, HTTP's per-session via callbacks — anything
     unregistered falls through to Telegram)
  6. Instantiate and register agents (all receive RouterNotifier)
  7. Run health checks
  8. Print pairing code
  9. Start all three interfaces + scheduler concurrently

Run:
    python main.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from core.bus import MessageBus
from core.config import settings
from core.llm import LLMProviderNotConfiguredError, get_llm_provider
from core.logger import configure_logging, get_logger
from core.memory import Memory
from core.notifier import CLINotifier, HTTPNotifier, RouterNotifier, TelegramNotifier
from core.protocols import Message
from core.safety import Safety
from core.scheduler import scheduler as _scheduler
from core.skill_loader import SkillLoader
from core.storage import Storage
from core.state_store import StateStore
from core.agent_creator import AgentCreator
from interfaces.telegram import TelegramInterface
from interfaces.cli import CLIInterface, CLI_CHAT_ID
from interfaces.http import HTTPInterface

log = get_logger("main")


# ──────────────────────────────────────────────
# Bootstrap
# ──────────────────────────────────────────────


async def bootstrap():
    """Initialise all components and return wired-up objects."""

    configure_logging(
        level=settings.log_level,
        fmt=settings.log_format,
        redact_content=settings.log_redact_content,
        content_max_len=settings.log_content_max_length,
    )
    log.info("Framework starting", event="startup")

    if not settings.db_encryption_key:
        log.warning(
            "Database encryption is disabled (DB_ENCRYPTION_KEY unset) — "
            "messages and history are stored in plaintext",
            event="db_unencrypted",
        )

    # Security check: TELEGRAM_ALLOWED_CHAT_IDS required in production
    env = os.getenv("ENV", "development").lower()
    if env == "production":
        if not settings.telegram_allowed_chat_ids:
            print(
                "\n[config] FATAL: TELEGRAM_ALLOWED_CHAT_IDS is required in production.\n"
                "  Set your chat ID in .env (find it via @userinfobot on Telegram).\n"
                "  Leaving it empty in production would allow unrestricted bot access.\n"
            )
            sys.exit(1)

    storage = Storage(settings.db_path, settings.db_encryption_key)
    await storage.init()

    state_store = StateStore(settings.db_path, settings.db_encryption_key)
    await state_store.init()

    telegram_notifier = TelegramNotifier(token=settings.telegram_token)
    cli_notifier = CLINotifier()
    http_notifier = HTTPNotifier()

    router = RouterNotifier(default=telegram_notifier)
    # "cli" is a single, fixed chat_id known at startup — register it once,
    # unconditionally (this is delivery-channel selection, not a trust
    # decision, so it doesn't need to wait on CLIInterface's pairing step).
    # HTTP chat_ids are minted per-session at runtime, so HTTPInterface
    # registers/unregisters them itself via the on_chat_paired/
    # on_chat_revoked callbacks wired below (#45 — explicit registration
    # replaces the old chat_id-prefix-matching convention).
    router.register_chat(CLI_CHAT_ID, cli_notifier)

    try:
        llm = get_llm_provider()
    except LLMProviderNotConfiguredError as e:
        print(f"\n[config] FATAL: {e}\n")
        sys.exit(1)
    await _verify_llm(llm)

    creator = AgentCreator(llm=llm, project_root=Path("."), notifier=router)
    memory = Memory(storage=storage, llm=llm, settings=settings)

    safety = Safety(
        notifier=router,
        allowed_ids=settings.telegram_allowed_chat_ids,
        approval_timeouts=settings.approval_timeouts,
        extra_blocked_patterns=settings.extra_blocked_patterns,
        rate_limit_rpm=settings.rate_limit_rpm,
        state_store=state_store,
        pairing_max_failed_attempts=settings.pairing_max_failed_attempts,
        approval_default_timeout=settings.approval_default_timeout,
    )
    await safety.pairing.load()

    skill_loader = SkillLoader(min_score=settings.skill_min_score)
    _scheduler.set_heartbeat_minutes(settings.heartbeat_interval_minutes)
    _scheduler.set_timezone(settings.user_timezone)
    _scheduler.configure_jobstore(settings.scheduler_db_path)

    bus = MessageBus(llm=llm, classifier_model=settings.classifier_model, state_store=state_store)
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

    # Auto-discover agents instead of manual registration
    from core.agent_discovery import discover_agents
    discovered_agents, failed = await discover_agents(**agent_kwargs)

    # Register discovered agents
    for agent in discovered_agents:
        bus.register(agent)

    for agent in discovered_agents:
        try:
            await agent.register_schedules(bus)
        except Exception as e:
            log.warning(
                "Failed to register schedules",
                event="schedule_reg_error",
                agent=agent.name,
                error=str(e),
            )

    await bus.load_chat_agent_map()
    await bus.load_chat_model_map()
    # safety.gate.notify_orphaned() is deliberately NOT called here: it can
    # target an HTTP chat_id, but HTTPInterface (and the router registration
    # its load_sessions() performs) doesn't exist yet at this point in
    # bootstrap() — main() calls it after that rehydration completes, so
    # RouterNotifier can actually resolve those chat_ids instead of falling
    # through to the Telegram default notifier (which would crash trying
    # int("http_...")).

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

    return bus, safety, creator, cli_notifier, http_notifier, router, state_store, llm, storage


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
    bus, safety, creator, cli_notifier, http_notifier, router, state_store, llm, storage = await bootstrap()

    print(f"\n{'=' * 52}")
    print(f"  PAIRING TOKEN:  {safety.pairing.code}")
    print("  Send this token to the bot on Telegram to pair.")
    print("  Or POST it to /pair on the HTTP API.")
    print(f"{'=' * 52}\n")

    telegram_interface = TelegramInterface(
        bus=bus, safety=safety, creator=creator, settings=settings
    )
    cli_interface = CLIInterface(
        bus=bus, safety=safety, creator=creator, notifier=cli_notifier
    )
    http_interface = HTTPInterface(
        bus=bus, safety=safety, creator=creator, notifier=http_notifier, settings=settings,
        state_store=state_store,
        on_chat_paired=lambda chat_id: router.register_chat(chat_id, http_notifier),
        on_chat_revoked=router.unregister_chat,
    )
    await http_interface.load_sessions()
    # Runs after HTTP session rehydration (see bootstrap()'s comment) so any
    # orphaned approval targeting an HTTP chat_id can actually be delivered.
    await safety.gate.notify_orphaned()

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
        close = getattr(llm, "close", None)
        if close is not None:
            await close()
        if storage is not None:
            await storage.close()
        log.info("Shutdown complete", event="shutdown_complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutdown requested", event="shutdown")
        sys.exit(0)
