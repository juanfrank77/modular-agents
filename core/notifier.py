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

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

from core.logger import get_logger

log = get_logger("notifier")

# Telegram message length limit
_MAX_MSG_LENGTH = 4096


class TelegramNotifier:
    """
    Implements the Notifier Protocol for Telegram.
    Handles long messages by splitting them automatically.
    """

    def __init__(self, token: str):
        self._bot = Bot(token=token)

    async def send(self, chat_id: str, text: str) -> None:
        """Send a text message. Splits automatically if over Telegram's 4096 char limit."""
        chunks = _split_message(text)
        for chunk in chunks:
            try:
                await self._bot.send_message(
                    chat_id=int(chat_id),
                    text=chunk,
                    parse_mode=ParseMode.MARKDOWN,
                )
            except TelegramError:
                # Markdown parse failed — retry as plain text
                try:
                    await self._bot.send_message(chat_id=int(chat_id), text=chunk)
                except TelegramError as e:
                    log.error("Failed to send message", event="send_error", error=str(e))

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
        buttons: list[tuple[str, str]],   # [(label, callback_data), ...]
    ) -> None:
        """Send a message with inline keyboard buttons (used for approval gates)."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(label, callback_data=data)]
            for label, data in buttons
        ])
        try:
            await self._bot.send_message(
                chat_id=int(chat_id),
                text=text,
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN,
            )
        except TelegramError as e:
            log.error("Failed to send buttons", event="send_buttons_error", error=str(e))


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
