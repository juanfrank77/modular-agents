"""
test_notifier_telegram.py
--------------------------
Tests for TelegramNotifier delivery behavior: raising NotificationError on
unrecoverable failures, retrying on RetryAfter, and falling back to plain text
when Markdown parsing fails.

Run:
    python -m pytest tests/test_notifier_telegram.py -x -q
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError

from core.notifier import TelegramNotifier, _send_with_retry
from core.protocols import NotificationError


def _notifier_with_mock_bot():
    """Return a TelegramNotifier whose underlying Bot has been replaced by an AsyncMock.

    Telegram's Bot class is immutable, so we swap the instance attribute directly.
    """
    notifier = TelegramNotifier(token="fake-token")
    notifier._bot = AsyncMock()
    return notifier


class TestSendWithRetry:
    @pytest.mark.asyncio
    async def test_success_does_not_retry(self):
        coro = AsyncMock()
        await _send_with_retry("123", coro)
        assert coro.await_count == 1

    @pytest.mark.asyncio
    async def test_retry_after_sleeps_and_retries(self):
        coro = AsyncMock(side_effect=[RetryAfter(0.1), None])
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await _send_with_retry("123", coro)
        assert coro.await_count == 2
        mock_sleep.assert_awaited_once_with(0.1)

    @pytest.mark.asyncio
    async def test_retry_after_exceeding_cap_raises_notification_error(self):
        coro = AsyncMock(side_effect=RetryAfter(120))
        with pytest.raises(NotificationError) as exc_info:
            await _send_with_retry("123", coro)
        assert exc_info.value.chat_id == "123"
        assert exc_info.value.retry_after == 120
        assert coro.await_count == 1

    @pytest.mark.asyncio
    async def test_retry_after_exhausted_retries_raises_notification_error(self):
        coro = AsyncMock(side_effect=[RetryAfter(0.1), RetryAfter(0.1), RetryAfter(0.1)])
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(NotificationError) as exc_info:
                await _send_with_retry("123", coro, max_retries=2)
        assert exc_info.value.chat_id == "123"
        assert coro.await_count == 3

    @pytest.mark.asyncio
    async def test_telegram_error_raises_notification_error(self):
        coro = AsyncMock(side_effect=TelegramError("network down"))
        with pytest.raises(NotificationError) as exc_info:
            await _send_with_retry("123", coro)
        assert exc_info.value.chat_id == "123"
        assert "network down" in str(exc_info.value)
        assert coro.await_count == 1


class TestTelegramNotifierSend:
    @pytest.fixture
    def notifier(self):
        return _notifier_with_mock_bot()

    @pytest.mark.asyncio
    async def test_send_raises_notification_error_on_telegram_error(self, notifier):
        notifier._bot.send_message.side_effect = TelegramError("blocked")
        with pytest.raises(NotificationError):
            await notifier.send("123", "hello")

    @pytest.mark.asyncio
    async def test_send_falls_back_to_plain_text_on_markdown_error(self, notifier):
        # First call (Markdown) fails; second call (plain) succeeds.
        notifier._bot.send_message.side_effect = [
            TelegramError("bad markdown"),
            MagicMock(),
        ]
        await notifier.send("123", "**bold**")
        assert notifier._bot.send_message.await_count == 2
        assert notifier._bot.send_message.await_args_list[0].kwargs.get("parse_mode") is not None
        assert "parse_mode" not in notifier._bot.send_message.await_args_list[1].kwargs

    @pytest.mark.asyncio
    async def test_send_success(self, notifier):
        notifier._bot.send_message.return_value = MagicMock()
        await notifier.send("123", "hello")
        notifier._bot.send_message.assert_awaited_once_with(
            chat_id=123,
            text="hello",
            parse_mode=ParseMode.MARKDOWN,
        )

    @pytest.mark.asyncio
    async def test_send_retry_after_then_success(self, notifier):
        notifier._bot.send_message.side_effect = [RetryAfter(0.05), MagicMock()]
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await notifier.send("123", "hello")
        assert notifier._bot.send_message.await_count == 2
        mock_sleep.assert_awaited_once_with(0.05)


class TestTelegramNotifierSendWithButtons:
    @pytest.fixture
    def notifier(self):
        return _notifier_with_mock_bot()

    @pytest.mark.asyncio
    async def test_send_with_buttons_raises_notification_error(self, notifier):
        notifier._bot.send_message.side_effect = TelegramError("blocked")
        with pytest.raises(NotificationError):
            await notifier.send_with_buttons(
                "123", "approve me?", [("Yes", "approve:1")]
            )

    @pytest.mark.asyncio
    async def test_send_with_buttons_retry_after(self, notifier):
        notifier._bot.send_message.side_effect = [RetryAfter(0.05), MagicMock()]
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await notifier.send_with_buttons(
                "123", "approve me?", [("Yes", "approve:1")]
            )
        assert notifier._bot.send_message.await_count == 2
        mock_sleep.assert_awaited_once_with(0.05)


class TestTelegramNotifierSendMedia:
    @pytest.fixture
    def notifier(self):
        return _notifier_with_mock_bot()

    @pytest.mark.asyncio
    async def test_send_media_raises_when_file_missing(self, notifier):
        with pytest.raises(NotificationError) as exc_info:
            await notifier.send_media("123", "/nonexistent/file.txt")
        assert "not found" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_send_media_raises_on_telegram_error(self, notifier, tmp_path):
        path = tmp_path / "photo.jpg"
        path.write_text("not a real photo")
        notifier._bot.send_photo.side_effect = TelegramError("blocked")
        with pytest.raises(NotificationError):
            await notifier.send_media("123", str(path))

    @pytest.mark.asyncio
    async def test_send_media_sends_document_for_non_image(self, notifier, tmp_path):
        path = tmp_path / "notes.txt"
        path.write_text("notes")
        notifier._bot.send_document.return_value = MagicMock()
        await notifier.send_media("123", str(path))
        notifier._bot.send_document.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_media_sends_photo_for_image(self, notifier, tmp_path):
        path = tmp_path / "photo.png"
        path.write_text("not a real photo")
        notifier._bot.send_photo.return_value = MagicMock()
        await notifier.send_media("123", str(path))
        notifier._bot.send_photo.assert_awaited_once()


class TestTelegramNotifierSendAndGetId:
    @pytest.fixture
    def notifier(self):
        return _notifier_with_mock_bot()

    @pytest.mark.asyncio
    async def test_send_and_get_id_returns_message_id(self, notifier):
        notifier._bot.send_message.return_value = MagicMock(message_id=42)
        message_id = await notifier.send_and_get_id("123", "hello")
        assert message_id == 42

    @pytest.mark.asyncio
    async def test_send_and_get_id_raises_notification_error(self, notifier):
        notifier._bot.send_message.side_effect = TelegramError("blocked")
        with pytest.raises(NotificationError):
            await notifier.send_and_get_id("123", "hello")


class TestTelegramNotifierDeleteMessage:
    @pytest.fixture
    def notifier(self):
        return _notifier_with_mock_bot()

    @pytest.mark.asyncio
    async def test_delete_message_raises_notification_error(self, notifier):
        notifier._bot.delete_message.side_effect = TelegramError("blocked")
        with pytest.raises(NotificationError):
            await notifier.delete_message("123", 42)

    @pytest.mark.asyncio
    async def test_delete_message_success(self, notifier):
        notifier._bot.delete_message.return_value = MagicMock()
        await notifier.delete_message("123", 42)
        notifier._bot.delete_message.assert_awaited_once_with(chat_id=123, message_id=42)


class TestTelegramNotifierSendMultiChunk:
    @pytest.fixture
    def notifier(self):
        return _notifier_with_mock_bot()

    @pytest.mark.asyncio
    async def test_partial_chunk_failure_raises(self, notifier):
        # First chunk succeeds (Markdown), second chunk fails Markdown and also
        # fails plain-text fallback — the caller should get a NotificationError
        # instead of a silently partial delivery.
        notifier._bot.send_message.side_effect = [
            MagicMock(),
            TelegramError("blocked markdown"),
            TelegramError("blocked plain"),
        ]
        with pytest.raises(NotificationError):
            await notifier.send("123", "x" * 5000)
        # First chunk: Markdown success, second chunk: Markdown failure + plain fallback.
        assert notifier._bot.send_message.await_count == 3
