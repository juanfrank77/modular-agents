"""
test_telegram_voice.py
----------------------
Tests for the voice-message path in interfaces/telegram.py:
- a voice handler is registered exactly once (mirrors the historical
  duplicate-handler bug for text)
- ffmpeg conversion invoked with expected args
- transcript delivered to the bus with the `[voice] ` prefix and correct
  chat_id
- TranscriptionError -> friendly reply, no crash
- voice message consumes one rate-limit unit
- unpaired chat is not transcribed

faster-whisper and ffmpeg are mocked/faked throughout.

Run:
    python -m pytest tests/test_telegram_voice.py -x -q
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from telegram.ext import MessageHandler

import interfaces.telegram as tg_module
from core.transcription import TranscriptionError
from interfaces.telegram import TelegramInterface

CHAT_ID = 12345


def _make_interface(rate_limit_msg=None):
    safety = MagicMock()
    safety.pairing.is_paired.return_value = True
    safety.rate_limiter.check.return_value = rate_limit_msg
    iface = TelegramInterface(
        bus=MagicMock(), safety=safety, creator=None, settings=MagicMock()
    )
    iface._bus.send_thinking = AsyncMock(return_value=None)
    iface._bus.publish = AsyncMock(return_value=None)
    iface._bus.send_notification = AsyncMock(return_value=None)
    iface._bus.clear_thinking = AsyncMock(return_value=None)
    iface._bus.registered_agents = ["projects"]
    return iface


def _make_update():
    update = MagicMock()
    update.message.voice = MagicMock(file_unique_id="abc123")
    update.message.text = None
    update.message.chat_id = CHAT_ID
    update.message.reply_text = AsyncMock()
    file = MagicMock()
    file.download_to_drive = AsyncMock(return_value="/tmp/voice_abc123.ogg")
    update.message.voice.get_file = AsyncMock(return_value=file)
    return update


class TestRegistration:
    def test_voice_handler_is_registered_exactly_once(self):
        iface = _make_interface()
        fake_app = MagicMock()
        iface._register_handlers(fake_app)
        voice_calls = [
            call
            for call in fake_app.add_handler.call_args_list
            if isinstance(call.args[0], MessageHandler)
            and call.args[0].callback == iface._on_voice
        ]
        assert len(voice_calls) == 1

    def test_voice_handler_filters_on_voice(self):
        iface = _make_interface()
        fake_app = MagicMock()
        iface._register_handlers(fake_app)
        voice_calls = [
            call
            for call in fake_app.add_handler.call_args_list
            if isinstance(call.args[0], MessageHandler)
            and call.args[0].callback == iface._on_voice
        ]
        assert voice_calls, "voice handler not registered"
        assert voice_calls[0].args[0].filters.name == "filters.VOICE"


class TestVoicePipeline:
    def test_transcript_published_with_voice_prefix(self):
        iface = _make_interface()
        update = _make_update()

        with patch.object(
            tg_module, "transcribe", return_value="hello from voice"
        ), patch.object(tg_module.subprocess, "run") as run:
            run.return_value = MagicMock(returncode=0)
            asyncio.run(iface._on_voice(update, MagicMock()))

        assert iface._bus.publish.await_count == 1
        event = iface._bus.publish.await_args.args[0]
        assert event.chat_id == str(CHAT_ID)
        assert event.text == "[voice] hello from voice"

    def test_ffmpeg_invoked_with_expected_args(self):
        iface = _make_interface()
        update = _make_update()

        with patch.object(
            tg_module, "transcribe", return_value="hi"
        ), patch.object(tg_module.subprocess, "run") as run:
            run.return_value = MagicMock(returncode=0)
            asyncio.run(iface._on_voice(update, MagicMock()))

        args = run.call_args.args[0]
        assert args[0] == "ffmpeg" or args[0].endswith("ffmpeg")
        assert any("16000" in str(a) for a in args)

    def test_rate_limit_consumed_before_transcription(self):
        iface = _make_interface(rate_limit_msg="slow down")
        update = _make_update()

        with patch.object(tg_module, "transcribe") as tr:
            asyncio.run(iface._on_voice(update, MagicMock()))
        tr.assert_not_called()

        iface._bus.publish.assert_not_awaited()
        iface._bus.send_notification.assert_awaited_once()
        assert "slow down" in iface._bus.send_notification.await_args.args[1]

    def test_unpaired_chat_gets_pairing_prompt_not_transcription(self):
        iface = _make_interface()
        iface._safety.pairing.is_paired.return_value = False
        iface._safety.pairing.is_locked.return_value = False
        iface._safety.pairing.attempts_remaining.return_value = 3
        update = _make_update()

        with patch.object(tg_module, "transcribe") as tr:
            asyncio.run(iface._on_voice(update, MagicMock()))
        tr.assert_not_called()
        iface._bus.publish.assert_not_awaited()
        assert "pairing token" in iface._bus.send_notification.await_args.args[1]


class TestErrorPaths:
    def test_transcription_error_replies_friendly_no_crash(self):
        iface = _make_interface()
        update = _make_update()

        with patch.object(
            tg_module, "transcribe", side_effect=TranscriptionError("boom")
        ):
            asyncio.run(iface._on_voice(update, MagicMock()))

        iface._bus.publish.assert_not_awaited()
        iface._bus.send_notification.assert_awaited_once()
        assert "transcri" in iface._bus.send_notification.await_args.args[1].lower()

    def test_ffmpeg_failure_replies_friendly_no_crash(self):
        iface = _make_interface()
        update = _make_update()

        with patch.object(
            tg_module.subprocess, "run", side_effect=FileNotFoundError("no ffmpeg")
        ):
            asyncio.run(iface._on_voice(update, MagicMock()))

        iface._bus.publish.assert_not_awaited()
        iface._bus.send_notification.assert_awaited_once()
