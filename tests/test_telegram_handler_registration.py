"""
test_telegram_handler_registration.py
----------------------------------------
Tests for TelegramInterface._register_handlers — specifically that
text messages register exactly once. A prior bug registered the same
_on_message callback twice (once in the default group for all chats,
once in group=1 restricted to private chats) — since python-telegram-
-bot evaluates handler groups independently, any private-chat text
message was processed twice: double rate-limit consumption, double
agent dispatch, two replies sent for one message.

Run:
    python -m pytest tests/test_telegram_handler_registration.py -x -q
"""

from __future__ import annotations

from unittest.mock import MagicMock

from interfaces.telegram import TelegramInterface


def _make_interface() -> TelegramInterface:
    return TelegramInterface(
        bus=MagicMock(), safety=MagicMock(), creator=MagicMock(), settings=MagicMock()
    )


class TestRegisterHandlers:
    def test_on_message_is_registered_exactly_once(self):
        telegram = _make_interface()
        fake_app = MagicMock()

        telegram._register_handlers(fake_app)

        on_message_calls = [
            call
            for call in fake_app.add_handler.call_args_list
            if getattr(call.args[0], "callback", None) == telegram._on_message
        ]
        assert len(on_message_calls) == 1

    def test_on_message_handler_is_not_registered_in_a_non_default_group(self):
        telegram = _make_interface()
        fake_app = MagicMock()

        telegram._register_handlers(fake_app)

        for call in fake_app.add_handler.call_args_list:
            if getattr(call.args[0], "callback", None) == telegram._on_message:
                # No `group=` kwarg and no positional group arg means PTB's
                # default group (0) applies.
                assert "group" not in call.kwargs
                assert len(call.args) == 1

    def test_all_expected_handlers_are_registered(self):
        telegram = _make_interface()
        fake_app = MagicMock()

        telegram._register_handlers(fake_app)

        assert fake_app.add_handler.call_count == 5
