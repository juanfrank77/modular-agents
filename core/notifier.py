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
from typing import Any, AsyncIterator

from core.logger import get_logger
from core.protocols import NotificationError
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError

log = get_logger("notifier")

# Telegram message length limit
_MAX_MSG_LENGTH = 4096

# Flood-control waits longer than this are surfaced to the caller instead of blocking.
_MAX_RETRY_AFTER = 60


async def _send_with_retry(
    chat_id: str,
    coro_factory,
    max_retries: int = 2,
    log_event: str = "send_error",
) -> None:
    """Run a Telegram send coroutine, retrying on RetryAfter with backoff.

    Raises NotificationError on unrecoverable failure or if the requested flood
    wait exceeds _MAX_RETRY_AFTER seconds.
    """
    for attempt in range(max_retries + 1):
        try:
            await coro_factory()
            return
        except RetryAfter as e:
            wait = getattr(e, "retry_after", 0)
            if attempt == max_retries or wait > _MAX_RETRY_AFTER:
                log.warning(
                    "Telegram flood wait too long; giving up",
                    event="telegram_retry_after_exceeded",
                    chat_id=chat_id,
                    retry_after=wait,
                    attempt=attempt,
                )
                raise NotificationError(
                    f"Telegram flood control: wait {wait}s before retrying",
                    chat_id=chat_id,
                    cause=e,
                    retry_after=wait,
                )
            log.warning(
                "Telegram flood control, backing off",
                event="telegram_retry_after",
                chat_id=chat_id,
                retry_after=wait,
                attempt=attempt,
            )
            await asyncio.sleep(wait)
        except TelegramError as e:
            log.error(
                "Failed to deliver Telegram notification",
                event=log_event,
                chat_id=chat_id,
                error=str(e),
            )
            raise NotificationError(str(e), chat_id=chat_id, cause=e)


class TelegramNotifier:
    """
    Implements the Notifier Protocol for Telegram.
    Handles long messages by splitting them automatically.
    """

    def __init__(self, token: str):
        self._bot = Bot(token=token)

    async def send(self, chat_id: str, text: str) -> None:
        """Send a text message. Splits automatically if over Telegram's 4096 char limit.

        Raises NotificationError if any chunk cannot be delivered.
        """
        chunks = _split_message(text)
        for chunk in chunks:

            async def _markdown():
                await self._bot.send_message(
                    chat_id=int(chat_id),
                    text=chunk,
                    parse_mode=ParseMode.MARKDOWN,
                )

            async def _plain():
                await self._bot.send_message(
                    chat_id=int(chat_id),
                    text=chunk,
                )

            try:
                await _send_with_retry(
                    chat_id, _markdown, log_event="send_error"
                )
            except NotificationError:
                # Markdown parse may be the culprit; retry as plain text once.
                await _send_with_retry(
                    chat_id, _plain, log_event="send_plain_error"
                )

    async def send_and_get_id(self, chat_id: str, text: str) -> int | None:
        """Send a message and return its Telegram message_id (for later editing/deletion).

        Raises NotificationError on delivery failure.
        """
        message = None

        async def _send():
            nonlocal message
            message = await self._bot.send_message(
                chat_id=int(chat_id),
                text=text,
            )

        await _send_with_retry(chat_id, _send, log_event="send_error")
        return message.message_id if message else None

    async def delete_message(self, chat_id: str, message_id: int) -> None:
        """Delete a previously sent message by its ID.

        Raises NotificationError on delivery failure.
        """

        async def _delete():
            await self._bot.delete_message(
                chat_id=int(chat_id), message_id=message_id
            )

        await _send_with_retry(
            chat_id, _delete, log_event="delete_error"
        )

    async def send_media(self, chat_id: str, path: str, caption: str = "") -> None:
        """Send a file (photo, document, etc.) by local path.

        Raises NotificationError if the file is missing or cannot be delivered.
        """
        file_path = Path(path)
        if not file_path.exists():
            raise NotificationError(
                f"Media file not found: {path}", chat_id=chat_id
            )

        suffix = file_path.suffix.lower()

        async def _photo():
            with open(file_path, "rb") as f:
                await self._bot.send_photo(
                    chat_id=int(chat_id), photo=f, caption=caption
                )

        async def _document():
            with open(file_path, "rb") as f:
                await self._bot.send_document(
                    chat_id=int(chat_id), document=f, caption=caption
                )

        await _send_with_retry(
            chat_id,
            _photo if suffix in (".jpg", ".jpeg", ".png", ".webp") else _document,
            log_event="send_media_error",
        )

    async def send_with_buttons(
        self,
        chat_id: str,
        text: str,
        buttons: list[tuple[str, str]],
    ) -> None:
        """Send a message with inline keyboard buttons (used for approval gates).

        Raises NotificationError on delivery failure.
        """
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(label, callback_data=data)]
                for label, data in buttons
            ]
        )

        async def _send():
            await self._bot.send_message(
                chat_id=int(chat_id),
                text=text,
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN,
            )

        await _send_with_retry(chat_id, _send, log_event="send_buttons_error")

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
        self._streaming: set[str] = set()

    def _get_queue(self, chat_id: str) -> asyncio.Queue[tuple[str, str]]:
        if chat_id not in self._queues:
            self._queues[chat_id] = asyncio.Queue()
        return self._queues[chat_id]

    def _get_lock(self, chat_id: str) -> asyncio.Lock:
        if chat_id not in self._locks:
            self._locks[chat_id] = asyncio.Lock()
        return self._locks[chat_id]

    def start_stream(self, chat_id: str) -> None:
        """Mark chat_id as SSE-mode: send()/notify_done() route to _queues.

        Call before creating the queue-consumer task; pair with end_stream()
        in a finally block on the *caller's* generator frame. Cleanup can't
        live inside stream_queue()'s own finally: when a client disconnects,
        ASGI closes the endpoint generator via GeneratorExit, and `async for`
        does not propagate that close into the inner generator it's
        iterating — stream_queue() would be left suspended, its finally
        never run, leaking both _streaming and _queues (#30).
        """
        self._streaming.add(chat_id)
        self._get_queue(chat_id)

    def end_stream(self, chat_id: str) -> None:
        """Undo start_stream(): stop SSE-mode and drop the queue entry."""
        self._streaming.discard(chat_id)
        self._queues.pop(chat_id, None)

    async def send(self, chat_id: str, text: str) -> None:
        """Write to whichever structure the chat's active consumer reads.

        Polling clients (/message) read _buffers via get_and_clear(); SSE
        clients (/message/stream) read _queues via stream_queue(). Writing to
        both unconditionally left the unread structure growing forever
        (#33), so route to the active one only.

        Looks the queue up with a plain dict .get() rather than
        _get_queue(), which would silently recreate — and thus leak — a
        queue entry that end_stream() already dropped. Falls back to
        buffering so a message never vanishes if the two ever end up out of
        sync.
        """
        queue = self._queues.get(chat_id) if chat_id in self._streaming else None
        if queue is not None:
            await queue.put(("notification", text))
        else:
            self._buffers.setdefault(chat_id, []).append(text)

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
        """Signal end-of-stream for SSE consumers.

        Same _get_queue()-avoidance as send(): a stream that's already
        ended has nothing listening, so recreating its queue here would
        just leak it again.
        """
        queue = self._queues.get(chat_id) if chat_id in self._streaming else None
        if queue is not None:
            await queue.put(("done", text))

    async def stream_queue(
        self,
        chat_id: str,
        done_event: asyncio.Event,
    ) -> AsyncIterator[tuple[str, str]]:
        """Yield (event_type, text) tuples from the queue until done_event is set
        and the queue is drained. Used by the SSE endpoint.

        Callers must have already called start_stream(chat_id), and must
        call end_stream(chat_id) in a finally block of their own — see
        start_stream()'s docstring for why cleanup can't live here.
        """
        queue = self._get_queue(chat_id)
        while not done_event.is_set() or not queue.empty():
            try:
                msg_type, text = await asyncio.wait_for(queue.get(), timeout=0.5)
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

    def _resolve(self, chat_id: str) -> Any:
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
