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

import asyncio
from pathlib import Path
from typing import AsyncIterator

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

    async def notify_done(self, chat_id: str, text: str) -> None:
        pass

    async def stream_queue(
        self, chat_id: str, done_event: asyncio.Event
    ) -> AsyncIterator[tuple[str, str]]:
        return
        yield  # type: ignore[misc]  # pragma: no cover

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

    async def notify_done(self, chat_id: str, text: str) -> None:
        pass

    async def stream_queue(
        self, chat_id: str, done_event: asyncio.Event
    ) -> AsyncIterator[tuple[str, str]]:
        return
        yield  # type: ignore[misc]  # pragma: no cover


class HTTPNotifier:
    """
    Notifier that buffers messages per chat_id — used by HTTPInterface.
    The HTTP handler calls get_and_clear(chat_id) after bus.publish() returns.

    Also provides a per-chat asyncio.Queue for SSE streaming:
    callers that need real-time event delivery can await the queue
    instead of polling get_and_clear(), avoiding the race condition
    where concurrent requests for the same chat_id interleave buffer pops.
    """

    def __init__(self) -> None:
        self._buffers: dict[str, list[str]] = {}
        self._queues: dict[str, asyncio.Queue[tuple[str, str]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _get_queue(self, chat_id: str) -> asyncio.Queue[tuple[str, str]]:
        if chat_id not in self._queues:
            self._queues[chat_id] = asyncio.Queue()
        return self._queues[chat_id]

    def _get_lock(self, chat_id: str) -> asyncio.Lock:
        if chat_id not in self._locks:
            self._locks[chat_id] = asyncio.Lock()
        return self._locks[chat_id]

    async def send(self, chat_id: str, text: str) -> None:
        self._buffers.setdefault(chat_id, []).append(text)
        await self._get_queue(chat_id).put(("notification", text))

    async def send_media(self, chat_id: str, path: str, caption: str = "") -> None:
        msg = f"[media: {path}]" + (f" {caption}" if caption else "")
        await self.send(chat_id, msg)

    async def send_with_buttons(
        self,
        chat_id: str,
        text: str,
        buttons: list[tuple[str, str]],
    ):
        await self.send(chat_id, text)

    async def send_and_get_id(self, chat_id: str, text: str) -> int | None:
        await self.send(chat_id, text)
        return None

    async def delete_message(self, chat_id: str, message_id: int) -> None:
        pass

    async def notify_done(self, chat_id: str, text: str) -> None:
        """Signal end-of-stream for SSE consumers."""
        await self._get_queue(chat_id).put(("done", text))

    async def stream_queue(
        self,
        chat_id: str,
        done_event: asyncio.Event,
    ) -> AsyncIterator[tuple[str, str]]:
        """Yield (event_type, text) tuples from the queue until done_event is set
        and the queue is drained. Used by the SSE endpoint."""
        while not done_event.is_set() or not self._get_queue(chat_id).empty():
            try:
                msg_type, text = await asyncio.wait_for(
                    self._get_queue(chat_id).get(), timeout=0.5
                )
                yield msg_type, text
            except asyncio.TimeoutError:
                continue

    def get_and_clear(self, chat_id: str) -> str:
        """Return all buffered messages joined by double newline, then clear.

        Thread-safe for concurrent access from the same event loop via the
        per-chat_id lock."""
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

    async def notify_done(self, chat_id: str, text: str) -> None:
        await self._resolve(chat_id).notify_done(chat_id, text)

    def stream_queue(
        self, chat_id: str, done_event: asyncio.Event
    ) -> AsyncIterator[tuple[str, str]]:
        return self._resolve(chat_id).stream_queue(chat_id, done_event)
