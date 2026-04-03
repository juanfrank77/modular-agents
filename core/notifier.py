"""
core/notifier.py
----------------
Telegram adapter implementing the Notifier Protocol.
Agents never import python-telegram-bot directly — they call this.
Future channels (Slack, Discord) implement the same interface.

Usage:
    from core.notifier import TelegramNotifier
    notifier = TelegramNotifier(token=settings.telegram_token)
    await notifier.send(chat_id, "Hello!")
"""

from __future__ import annotations

from pathlib import Path

from core.logger import get_logger
from core.budget import ActionType, BudgetManager
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import TelegramError

log = get_logger("notifier")

# Telegram message length limit
_MAX_MSG_LENGTH = 4096


class TelegramNotifier:
    """
    Implements the Notifier Protocol for Telegram.
    Handles long messages by splitting automatically.
    Supports budget-aware sending for proactive messages.
    """

    def __init__(self, token: str, budget_manager: BudgetManager | None = None):
        self._bot = Bot(token=token)
        self._budget = budget_manager

    def set_budget(self, budget: BudgetManager) -> None:
        """Set budget manager for proactive message limiting."""
        self._budget = budget

    async def send(
        self,
        chat_id: str,
        text: str,
        action_type: ActionType = ActionType.REACTIVE,
        agent_name: str = "",
    ) -> bool:
        """
        Send a text message. Splits automatically if over Telegram's 4096 char limit.

        Args:
            chat_id: Target chat ID
            text: Message text
            action_type: PROACTIVE or REACTIVE (affects budget check)
            agent_name: Agent name for budget tracking

        Returns:
            True if sent successfully, False if deferred due to budget
        """
        # Check budget for proactive messages
        if action_type == ActionType.PROACTIVE and self._budget and agent_name:
            if not self._budget.check_budget(agent_name, action_type):
                log.info(
                    "Proactive message deferred (budget)",
                    event="message_deferred_budget",
                    agent=agent_name,
                    chat_id=chat_id,
                )
                return False

        start_time = 0.0
        if self._budget and agent_name and action_type == ActionType.PROACTIVE:
            start_time = self._budget.record_action_start(agent_name, action_type)

        chunks = _split_message(text)
        for chunk in chunks:
            try:
                await self._bot.send_message(
                    chat_id=int(chat_id),
                    text=chunk,
                    parse_mode=ParseMode.MARKDOWN,
                )
            except TelegramError:
                try:
                    await self._bot.send_message(chat_id=int(chat_id), text=chunk)
                except TelegramError as e:
                    log.error(
                        "Failed to send message", event="send_error", error=str(e)
                    )

        if self._budget and start_time > 0 and agent_name:
            self._budget.record_action_end(agent_name, action_type, start_time)

        return True

    async def send_and_get_id(
        self,
        chat_id: str,
        text: str,
        action_type: ActionType = ActionType.REACTIVE,
        agent_name: str = "",
    ) -> int | None:
        """Send a message and return its Telegram message_id (for later editing/deletion)."""
        # Check budget for proactive messages
        if action_type == ActionType.PROACTIVE and self._budget and agent_name:
            if not self._budget.check_budget(agent_name, action_type):
                log.info(
                    "Proactive message deferred (budget)",
                    event="message_deferred_budget",
                    agent=agent_name,
                    chat_id=chat_id,
                )
                return None

        start_time = 0.0
        if self._budget and agent_name and action_type == ActionType.PROACTIVE:
            start_time = self._budget.record_action_start(agent_name, action_type)

        try:
            message = await self._bot.send_message(
                chat_id=int(chat_id),
                text=text,
            )
            result = message.message_id
        except TelegramError as e:
            log.error("Failed to send message", event="send_error", error=str(e))
            result = None

        if self._budget and start_time > 0 and agent_name:
            self._budget.record_action_end(agent_name, action_type, start_time)

        return result

    async def delete_message(self, chat_id: str, message_id: int) -> None:
        """Delete a previously sent message by its ID."""
        try:
            await self._bot.delete_message(chat_id=int(chat_id), message_id=message_id)
        except TelegramError as e:
            log.error(
                "Failed to delete message",
                event="delete_error",
                chat_id=chat_id,
                message_id=message_id,
                error=str(e),
            )

    async def send_media(self, chat_id: str, path: str, caption: str = "") -> None:
        """Send a file (photo, document, etc.) by local path."""
        file_path = Path(path)
        if not file_path.exists():
            log.error("Media file not found", event="send_media_error", path=path)
            return
        try:
            suffix = file_path.suffix.lower()
            with open(file_path, "rb") as f:
                if suffix in (".jpg", ".jpeg", ".png", ".webp"):
                    await self._bot.send_photo(
                        chat_id=int(chat_id), photo=f, caption=caption
                    )
                else:
                    await self._bot.send_document(
                        chat_id=int(chat_id), document=f, caption=caption
                    )
        except TelegramError as e:
            log.error("Failed to send media", event="send_media_error", error=str(e))

    async def send_with_buttons(
        self,
        chat_id: str,
        text: str,
        buttons: list[tuple[str, str]],
    ) -> None:
        """Send a message with inline keyboard buttons (used for approval gates)."""
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(label, callback_data=data)]
                for label, data in buttons
            ]
        )
        try:
            await self._bot.send_message(
                chat_id=int(chat_id),
                text=text,
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN,
            )
        except TelegramError as e:
            log.error(
                "Failed to send buttons", event="send_buttons_error", error=str(e)
            )


def _split_message(text: str, limit: int = _MAX_MSG_LENGTH) -> list[str]:
    """Split a long message into chunks that respect Telegram's size limit."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        # Try to split at a newline within the limit
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks
