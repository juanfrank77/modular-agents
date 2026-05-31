"""
interfaces/telegram.py
----------------------
Telegram interface adapter. Wraps all python-telegram-bot logic.
Receives bus + safety + creator from bootstrap(), registers handlers,
and runs long-polling. No agents or core components are constructed here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.logger import get_logger
from core.protocols import AgentEvent, EventType

if TYPE_CHECKING:
    from core.bus import MessageBus
    from core.config import Settings
    from core.safety import Safety
    from core.agent_creator import AgentCreator

log = get_logger("telegram_interface")


class TelegramInterface:
    def __init__(
        self,
        bus: "MessageBus",
        safety: "Safety",
        creator: "AgentCreator",
        settings: "Settings",
    ) -> None:
        self._bus = bus
        self._safety = safety
        self._creator = creator
        self._settings = settings

    async def run(self) -> None:
        import asyncio

        app = ApplicationBuilder().token(self._settings.telegram_token).build()

        app.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self._on_message,
            )
        )
        app.add_handler(CallbackQueryHandler(self._on_callback))
        app.add_handler(CommandHandler("model", self._on_model))
        app.add_handler(CommandHandler("planmode", self._on_planmode))
        app.add_handler(
            CommandHandler(["newagent", "help"], self._on_command)
        )
        app.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
                self._on_message,
            ),
            group=1,
        )

        log.info("Telegram bot starting", event="bot_start", mode="polling")

        async with app:
            await app.start()
            assert app.updater is not None
            await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
            log.info("Telegram bot running", event="running")
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                pass
            finally:
                await app.updater.stop()
                await app.stop()

    async def _on_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not update.message or not update.message.text:
            return

        chat_id = str(update.message.chat_id)
        text = update.message.text

        if not self._safety.pairing.is_paired(chat_id):
            if self._safety.pairing.try_pair(chat_id, text):
                await self._bus.send_notification(chat_id, "✅ Paired. You can now use the bot.")
                log.info("Chat paired via message", event="paired", chat_id=chat_id)
            else:
                await self._bus.send_notification(
                    chat_id,
                    "🔒 Send the pairing code shown in the console to get started.",
                )
            return

        if self._creator and self._creator.is_active(chat_id):
            response = await self._creator.handle(chat_id, text)
            await self._bus.send_notification(chat_id, response)
            return

        log.info("Inbound message", event="inbound", chat_id=chat_id, length=len(text))

        event = AgentEvent(
            type=EventType.USER_MESSAGE,
            agent_name="",
            chat_id=chat_id,
            text=text,
        )

        thinking_id = await self._bus.send_thinking(chat_id)
        response = await self._bus.publish(event)
        if thinking_id:
            await self._bus.clear_thinking(chat_id, thinking_id)
        if response and not response.success:
            log.warning(
                "Agent returned unsuccessful response",
                event="response_error",
                agent=response.agent_name,
            )

    async def _on_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        if not query or not query.data:
            return

        await query.answer()

        data = query.data
        if data.startswith("approve:"):
            approval_id = data.split(":", 1)[1]
            self._safety.gate.resolve(approval_id, approved=True)
            await query.edit_message_text("Approved.")
            log.info("Action approved", event="approval", approval_id=approval_id)
        elif data.startswith("deny:"):
            approval_id = data.split(":", 1)[1]
            self._safety.gate.resolve(approval_id, approved=False)
            await query.edit_message_text("Denied.")
            log.info("Action denied", event="denial", approval_id=approval_id)

    async def _on_model(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not update.message:
            return
        chat_id = str(update.message.chat_id)
        if not self._safety.pairing.is_paired(chat_id):
            await update.message.reply_text("🔒 Not paired.")
            return

        if context.args:
            new_model = context.args[0].strip()
            old_model = self._settings.default_model
            self._settings.default_model = new_model
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
                f"Current model: `{self._settings.default_model}`.\nUsage: `/model <model-id>`",
                parse_mode="Markdown",
            )

    async def _on_planmode(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not update.message:
            return
        chat_id = str(update.message.chat_id)
        if not self._safety.pairing.is_paired(chat_id):
            await update.message.reply_text("🔒 Not paired.")
            return

        args = context.args or []
        agent_name = args[0].lower() if args else None

        toggled = []
        for name in self._bus.registered_agents:
            if agent_name is None or name == agent_name:
                agent = self._bus.get_agent(name)
                if agent:
                    new_state = agent.toggle_plan_mode(chat_id)
                    state = "ON" if new_state else "OFF"
                    toggled.append(f"{name}: Plan mode {state}")

        if toggled:
            await update.message.reply_text("\n".join(toggled))
        else:
            await update.message.reply_text(
                f"No agent named '{agent_name}'. "
                f"Available: {', '.join(self._bus.registered_agents)}"
            )

    async def _on_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not update.message:
            return

        chat_id = str(update.message.chat_id)
        command = update.message.text or ""

        if not self._safety.pairing.is_paired(chat_id):
            await self._bus.send_notification(
                chat_id,
                "🔒 Send the pairing code shown in the server console to get started.",
            )
            return

        if command.startswith("/newagent") or self._creator.is_active(chat_id):
            log.info("Agent creator command", event="new_agent_cmd", chat_id=chat_id)
            response = await self._creator.handle(chat_id, command)
            await self._bus.send_notification(chat_id, response)
            return

        if command.startswith("/help"):
            await self._bus.send_notification(
                chat_id,
                (
                    "*Available commands*\n\n"
                    "/newagent — create a new agent interactively\n"
                    "/planmode [agent] — toggle plan mode for one or all agents\n"
                    "/help — show this message\n\n"
                    "Or just send a message to talk to your agents."
                ),
            )
            return