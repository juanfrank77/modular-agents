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
from pathlib import Path

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.bus import MessageBus
from core.config import settings
from core.llm import get_llm_provider
from core.logger import configure_logging, get_logger
from core.memory import Memory
from core.notifier import TelegramNotifier
from core.protocols import AgentEvent, EventType
from core.safety import Safety
from core.scheduler import Scheduler, scheduler as _scheduler
from core.skill_loader import SkillLoader
from core.storage import Storage
from core.agent_creator import AgentCreator
from agents.business.agent import BusinessAgent
from agents.devops.agent import DevOpsAgent
from agents.echo.agent import EchoAgent

log = get_logger("main")


# ──────────────────────────────────────────────
# Bootstrap
# ──────────────────────────────────────────────

async def bootstrap() -> tuple[
    MessageBus, TelegramNotifier, Safety, Scheduler, "AgentCreator"
]:
    """Initialise all components and return the wired-up bus, notifier, safety, scheduler, and creator."""

    # 1. Logging
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    log.info("Framework starting", event="startup")

    # 2. Storage
    storage = Storage(settings.db_path)
    await storage.init()

    # 3. Notifier
    notifier = TelegramNotifier(token=settings.telegram_token)

    # 4. LLM
    llm = get_llm_provider()

    # 4b. Verify LLM key works before proceeding
    await _verify_llm(llm)

    # 5. Agent creator
    creator = AgentCreator(llm=llm, project_root=Path("."))

    # 6. Memory
    memory = Memory(storage=storage, llm=llm, settings=settings)

    # 9. Safety
    safety = Safety(
        notifier=notifier,
        allowed_ids=settings.telegram_allowed_chat_ids,
        approval_timeouts=settings.approval_timeouts,
    )

    # 10. Skill loader
    skill_loader = SkillLoader()

    # 13. Scheduler — configure the module-level singleton so agents can import it
    _scheduler._heartbeat_minutes = settings.heartbeat_interval_minutes

    # 14. Message bus
    bus = MessageBus()
    _scheduler.set_bus(bus)

    # 17. Instantiate and register agents
    business = BusinessAgent(
        settings=settings,
        storage=storage,
        notifier=notifier,
        llm=llm,
        memory=memory,
        safety=safety,
        skill_loader=skill_loader,
        bus=bus
    )
    bus.register(business)

    devops = DevOpsAgent(
        settings=settings,
        storage=storage,
        notifier=notifier,
        llm=llm,
        memory=memory,
        safety=safety,
        skill_loader=skill_loader,
        bus=bus
    )
    bus.register(devops)

    # Keep echo agent as fallback
    echo = EchoAgent(settings=settings, storage=storage, notifier=notifier)
    bus.register(echo)

    # 18. Register each agent's scheduled jobs
    # Must happen AFTER scheduler.set_bus() and agent registration
    for agent in [business, devops, echo]:
        try:
            await agent.register_schedules(bus)
        except Exception as e:
            log.warning(
                "Failed to register schedules for agent",
                event="schedule_reg_error",
                agent=agent.name,
                error=str(e),
            )

    # 19. Health checks
    health = await bus.health_check_all()
    all_healthy = True
    for agent_name, healthy in health.items():
        if healthy:
            log.info("Agent healthy", event="health_ok", agent=agent_name)
        else:
            log.warning("Agent unhealthy", event="health_fail", agent=agent_name)
            all_healthy = False

    if not all_healthy:
        log.warning(
            "Some agents failed health checks — check logs before continuing",
            event="startup_degraded",
        )

    log.info(
        "Bootstrap complete",
        event="startup_complete",
        agents=bus.registered_agents,
    )
    return bus, notifier, safety, _scheduler, creator

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────
async def _send_to_chat_bus(bus: MessageBus, chat_id: str, text: str) -> None:
    """
    Send a plain message to a chat via the first registered agent's notifier.
    Used internally for pairing messages — avoids accessing bus._agents directly.
    """
    await bus.send_notification(chat_id, text)


async def _verify_llm(llm: "LLMProvider") -> None:
    """Make a minimal API call at startup to validate the LLM key works.
    Exits with a clear message if the key is invalid or the service is unreachable."""
    from core.protocols import Message
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

# ──────────────────────────────────────────────
# Telegram handlers
# ──────────────────────────────────────────────

def make_message_handler(bus: MessageBus, safety: Safety, creator: AgentCreator):
    async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.text:
            return

        chat_id = str(update.message.chat_id)
        text = update.message.text

        # Pairing flow: unpaired chats must send the pairing code first
        if not safety.pairing.is_paired(chat_id):
            if safety.pairing.try_pair(chat_id, text):
                await bus.send_notification(
                    chat_id, "✅ Paired. You can now use the bot."
                )
                log.info("Chat paired via message", event="paired", chat_id=chat_id)
            else:
                await bus.send_notification(
                    chat_id,
                    "🔒 Send the pairing code shown in the console to get started.",
                )
            return

        # If user is mid-wizard, route to creator instead of agents
        if creator and creator.is_active(chat_id):
            response = await creator.handle(chat_id, text)
            await bus.send_notification(chat_id, response)
            return

        log.info("Inbound message", event="inbound", chat_id=chat_id, length=len(text))

        event = AgentEvent(
            type=EventType.USER_MESSAGE,
            agent_name="",  # bus resolves the agent
            chat_id=chat_id,
            text=text,
        )

        thinking_id = await bus.send_thinking(chat_id)
        response = await bus.publish(event)
        if thinking_id:
            await bus.clear_thinking(chat_id, thinking_id)
        if response and not response.success:
            log.warning(
                "Agent returned unsuccessful response",
                event="response_error",
                agent=response.agent_name,
            )

    return on_message


def make_callback_handler(safety: Safety):
    async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline button presses (approval gate responses)."""
        query = update.callback_query
        if not query or not query.data:
            return

        await query.answer()

        data = query.data
        if data.startswith("approve:"):
            approval_id = data.split(":", 1)[1]
            safety.gate.resolve(approval_id, approved=True)
            await query.edit_message_text("Approved.")
            log.info("Action approved", event="approval", approval_id=approval_id)
        elif data.startswith("deny:"):
            approval_id = data.split(":", 1)[1]
            safety.gate.resolve(approval_id, approved=False)
            await query.edit_message_text("Denied.")
            log.info("Action denied", event="denial", approval_id=approval_id)

    return on_callback


def make_model_handler(safety: Safety):
    async def on_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        chat_id = str(update.message.chat_id)
        if not safety.pairing.is_paired(chat_id):
            await update.message.reply_text("🔒 Not paired.")
            return

        if context.args:
            new_model = context.args[0].strip()
            old_model = settings.default_model
            settings.default_model = new_model
            log.info(
                "Model changed",
                event="model_change",
                chat_id=chat_id,
                old=old_model,
                new=new_model,
            )
            await update.message.reply_text(
                f"Model set to `{new_model}`.", parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                f"Current model: `{settings.default_model}`.\n"
                "Usage: `/model <model-id>`",
                parse_mode="Markdown",
            )

    return on_model


def make_planmode_handler(bus: MessageBus, safety: Safety):
    async def on_planmode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        chat_id = str(update.message.chat_id)
        if not safety.pairing.is_paired(chat_id):
            await update.message.reply_text("🔒 Not paired.")
            return

        args = context.args or []
        agent_name = args[0].lower() if args else None

        toggled = []
        for name, agent in bus._agents.items():
            if agent_name is None or name == agent_name:
                agent.plan_mode = not agent.plan_mode
                state = "ON" if agent.plan_mode else "OFF"
                toggled.append(f"{name}: Plan mode {state}")

        if toggled:
            await update.message.reply_text("\n".join(toggled))
        else:
            await update.message.reply_text(
                f"No agent named '{agent_name}'. "
                f"Available: {', '.join(bus.registered_agents)}"
            )

    return on_planmode


def make_command_handler(bus: MessageBus, safety: Safety, creator: AgentCreator):
    async def on_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return

        chat_id = str(update.message.chat_id)
        command = update.message.text or ""

        # Pairing check
        if not safety.pairing.is_paired(chat_id):
            await bus.send_notification(
                chat_id,
                "🔒 Send the pairing code shown in the server console to get started.",
            )
            return

        # /new-agent — start or continue the wizard
        if command.startswith("/new-agent") or creator.is_active(chat_id):
            log.info("Agent creator command", event="new_agent_cmd", chat_id=chat_id)
            response = await creator.handle(chat_id, command)
            await bus.send_notification(chat_id, response)
            return

        # /help
        if command.startswith("/help"):
            await bus.send_notification(
                chat_id,
                (
                    "*Available commands*\n\n"
                    "/new-agent — create a new agent interactively\n"
                    "/planmode [agent] — toggle plan mode for one or all agents\n"
                    "/help — show this message\n\n"
                    "Or just send a message to talk to your agents."
                ),
            )
            return

    return on_command

# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

async def main() -> None:
    bus, notifier, safety, scheduler, creator = await bootstrap()
    assert creator is not None

    # Print pairing code prominently — this is how new chats authenticate
    print(f"\n{'=' * 52}")
    print(f"  PAIRING CODE:  {safety.pairing.code}")
    print("  Send this code to the bot on Telegram to pair.")
    print(f"{'=' * 52}\n")

    app = ApplicationBuilder().token(settings.telegram_token).build()

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            make_message_handler(bus, safety, creator),
        )
    )
    app.add_handler(CallbackQueryHandler(make_callback_handler(safety)))
    app.add_handler(CommandHandler("model", make_model_handler(safety)))
    app.add_handler(CommandHandler("planmode", make_planmode_handler(bus, safety)))
    app.add_handler(
        CommandHandler(
            ["new-agent", "help"], make_command_handler(bus, safety, creator)
        )
    )

    # Also handle /new-agent continuation messages (non-command text during wizard)
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            make_message_handler(bus, safety, creator),
        ),
        group=1,
    )

    log.info("Telegram bot starting", event="bot_start", mode="polling")

    async with app:
        await app.start()
        assert app.updater is not None, "updater required"
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        scheduler.start()
        log.info("Bot is running. Press Ctrl+C to stop.", event="running")

        try:
            # Block until cancelled (KeyboardInterrupt → asyncio.run cancels the task)
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
        finally:
            log.info("Shutting down", event="shutdown_start")
            scheduler.stop()
            await app.updater.stop()
            await app.stop()
            log.info("Shutdown complete", event="shutdown_complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutdown requested", event="shutdown")
        sys.exit(0)
