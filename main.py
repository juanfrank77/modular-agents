"""
main.py
-------
Startup entry point. Wires all components together and
runs the Telegram bot via long-polling.

What happens on startup:
  1. Load and validate config from .env
  2. Configure structured logging
  3. Initialise SQLite storage
  4. Build LLM, Memory, Safety, SkillLoader, Scheduler
  5. Build the message bus
  6. Instantiate and register agents
  7. Run health checks
  8. Print pairing code
  9. Start the Telegram listener + scheduler

Run:
    python main.py
"""

from __future__ import annotations

import asyncio
import sys

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.bus import MessageBus
from core.config import settings
from core.llm import AnthropicLLM
from core.logger import configure_logging, get_logger
from core.memory import Memory
from core.notifier import TelegramNotifier
from core.protocols import AgentEvent, EventType
from core.safety import Safety
from core.scheduler import Scheduler
from core.skill_loader import SkillLoader
from core.storage import Storage
from agents.business.agent import BusinessAgent
from agents.echo.agent import EchoAgent

log = get_logger("main")


# ──────────────────────────────────────────────
# Bootstrap
# ──────────────────────────────────────────────

async def bootstrap() -> tuple[MessageBus, TelegramNotifier, Safety, Scheduler]:
    """Initialise all components and return the wired-up bus, notifier, safety, and scheduler."""

    # 1. Logging
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    log.info("Framework starting", event="startup")

    # 2. Storage
    storage = Storage(settings.db_path)
    await storage.init()

    # 3. Notifier
    notifier = TelegramNotifier(token=settings.telegram_token)

    # 4. LLM
    llm = AnthropicLLM(api_key=settings.anthropic_api_key)

    # 5. Memory
    memory = Memory(storage=storage, llm=llm, settings=settings)

    # 6. Safety
    safety = Safety(notifier=notifier, allowed_ids=settings.telegram_allowed_chat_ids)

    # 7. Skill loader
    skill_loader = SkillLoader()

    # 8. Scheduler
    scheduler = Scheduler(heartbeat_minutes=settings.heartbeat_interval_minutes)

    # 9. Message bus
    bus = MessageBus()
    scheduler.set_bus(bus)

    # 10. Instantiate and register agents
    business = BusinessAgent(
        settings=settings,
        storage=storage,
        notifier=notifier,
        llm=llm,
        memory=memory,
        safety=safety,
        skill_loader=skill_loader,
    )
    bus.register(business)

    # Keep echo agent as fallback
    echo = EchoAgent(settings=settings, storage=storage, notifier=notifier)
    bus.register(echo)

    # 11. Health checks
    health = await bus.health_check_all()
    for agent_name, healthy in health.items():
        if healthy:
            log.info("Agent healthy", event="health_ok", agent=agent_name)
        else:
            log.warning("Agent unhealthy", event="health_fail", agent=agent_name)

    log.info(
        "Bootstrap complete",
        event="startup_complete",
        agents=bus.registered_agents,
    )
    return bus, notifier, safety, scheduler


# ──────────────────────────────────────────────
# Telegram handlers
# ──────────────────────────────────────────────

def make_message_handler(bus: MessageBus, safety: Safety):
    async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.text:
            return

        chat_id = str(update.message.chat_id)
        text = update.message.text

        # Pairing check: if not paired, try to pair with the code
        if not safety.pairing.is_paired(chat_id):
            if safety.pairing.try_pair(chat_id, text):
                await bus._agents.get("business", next(iter(bus._agents.values()))).notifier.send(
                    chat_id, "Paired successfully. You can now use the bot."
                )
            else:
                await bus._agents.get("business", next(iter(bus._agents.values()))).notifier.send(
                    chat_id, "Send the pairing code to get started."
                )
            return

        log.info(
            "Inbound message",
            event="inbound",
            chat_id=chat_id,
            length=len(text),
        )

        event = AgentEvent(
            type=EventType.USER_MESSAGE,
            agent_name="",      # bus resolves the agent
            chat_id=chat_id,
            text=text,
        )

        response = await bus.publish(event)
        if response and not response.success:
            log.warning("Agent returned unsuccessful response",
                        event="response_error", agent=response.agent_name)

    return on_message


def make_callback_handler(safety: Safety):
    async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.data:
            return

        await query.answer()

        data = query.data
        if data.startswith("approve:"):
            approval_id = data.split(":", 1)[1]
            safety.gate.resolve(approval_id, approved=True)
            await query.edit_message_text("Approved.")
        elif data.startswith("deny:"):
            approval_id = data.split(":", 1)[1]
            safety.gate.resolve(approval_id, approved=False)
            await query.edit_message_text("Denied.")

    return on_callback


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

async def main() -> None:
    bus, notifier, safety, scheduler = await bootstrap()

    # Print pairing code to console
    print(f"\n{'='*50}")
    print(f"  PAIRING CODE: {safety.pairing.code}")
    print(f"{'='*50}\n")

    # Start scheduler
    scheduler.start()

    app = (
        ApplicationBuilder()
        .token(settings.telegram_token)
        .build()
    )

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, make_message_handler(bus, safety))
    )
    app.add_handler(CallbackQueryHandler(make_callback_handler(safety)))

    log.info("Telegram bot starting", event="bot_start", mode="polling")

    try:
        await app.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        scheduler.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutdown requested", event="shutdown")
        sys.exit(0)
