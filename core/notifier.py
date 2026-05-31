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
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import TelegramError

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
                    log.error(
                        "Failed to send message", event="send_error", error=str(e)
                    )

    async def send_and_get_id(self, chat_id: str, text: str) -> int | None:
        """Send a message and return its Telegram message_id (for later editing/deletion)."""
        try:
            message = await self._bot.send_message(
                chat_id=int(chat_id),
                text=text,
            )
            return message.message_id
        except TelegramError as e:
            log.error("Failed to send message", event="send_error", error=str(e))
            return None

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


class CLINotifier:
    """Notifier that prints to stdout — used by CLIInterface."""

    async def send(self, chat_id: str, text: str) -> None:
        print(f"\n{text}\n> ", end="", flush=True)

    async def send_media(self, chat_id: str, path: str, caption: str = "") -> None:
        msg = f"[media: {path}]" + (f" {caption}" if caption else "")
        await self.send(chat_id, msg)

    async def send_with_buttons(
        self,
        chat_id: str,
        text: str,
        buttons: list[tuple[str, str]],
    ) -> None:
        await self.send(chat_id, text)

    async def send_and_get_id(self, chat_id: str, text: str) -> int | None:
        await self.send(chat_id, text)
        return None

    async def delete_message(self, chat_id: str, message_id: int) -> None:
        pass


class HTTPNotifier:
    """
    Notifier that buffers messages per chat_id — used by HTTPInterface.
    The HTTP handler calls get_and_clear(chat_id) after bus.publish() returns.
    """

    def __init__(self) -> None:
        self._buffers: dict[str, list[str]] = {}

    async def send(self, chat_id: str, text: str) -> None:
        self._buffers.setdefault(chat_id, []).append(text)

    async def send_media(self, chat_id: str, path: str, caption: str = "") -> None:
        msg = f"[media: {path}]" + (f" {caption}" if caption else "")
        await self.send(chat_id, msg)

    async def send_with_buttons(
        self,
        chat_id: str,
        text: str,
        buttons: list[tuple[str, str]],
    ) -> None:
        await self.send(chat_id, text)

    async def send_and_get_id(self, chat_id: str, text: str) -> int | None:
        await self.send(chat_id, text)
        return None

    async def delete_message(self, chat_id: str, message_id: int) -> None:
        pass

    def get_and_clear(self, chat_id: str) -> str:
        """Return all buffered messages joined by double newline, then clear."""
        messages = self._buffers.pop(chat_id, [])
        return "\n\n".join(messages)


class RouterNotifier:
    """
    Dispatches all Notifier calls to the correct backing notifier
    based on chat_id prefix.

    Usage:
        router = RouterNotifier(default=telegram_notifier)
        router.register_prefix("cli", cli_notifier)
        router.register_prefix("http_", http_notifier)
    """

    def __init__(self, default: "TelegramNotifier") -> None:
        self._default = default
        self._prefixes: list[tuple[str, object]] = []  # (prefix, notifier), checked in order

    def register_prefix(self, prefix: str, notifier: object) -> None:
        self._prefixes.append((prefix, notifier))

    def _resolve(self, chat_id: str) -> object:
        for prefix, notifier in self._prefixes:
            if chat_id.startswith(prefix):
                return notifier
        return self._default

    async def send(self, chat_id: str, text: str) -> None:
        await self._resolve(chat_id).send(chat_id, text)

    async def send_media(self, chat_id: str, path: str, caption: str = "") -> None:
        await self._resolve(chat_id).send_media(chat_id, path, caption)

    async def send_with_buttons(
        self,
        chat_id: str,
        text: str,
        buttons: list[tuple[str, str]],
    ) -> None:
        await self._resolve(chat_id).send_with_buttons(chat_id, text, buttons)

    async def send_and_get_id(self, chat_id: str, text: str) -> int | None:
        return await self._resolve(chat_id).send_and_get_id(chat_id, text)

    async def delete_message(self, chat_id: str, message_id: int) -> None:
        await self._resolve(chat_id).delete_message(chat_id, message_id)
