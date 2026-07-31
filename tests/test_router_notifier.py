"""
test_router_notifier.py
------------------------
Tests for RouterNotifier's explicit per-chat_id registration (#45): a
chat_id's delivery notifier is looked up from an explicit registry set by
whichever interface minted the chat_id, not re-derived by matching a
"cli"/"http_..." string prefix.

Run:
    python -m pytest tests/test_router_notifier.py -x -q
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.notifier import RouterNotifier


def _notifier():
    n = AsyncMock()
    return n


class TestRouterNotifierDispatch:
    @pytest.mark.asyncio
    async def test_unregistered_chat_id_falls_through_to_default(self):
        default = _notifier()
        router = RouterNotifier(default=default)

        await router.send("999888777", "hi")

        default.send.assert_awaited_once_with("999888777", "hi")

    @pytest.mark.asyncio
    async def test_registered_chat_id_dispatches_to_its_notifier(self):
        default = _notifier()
        cli = _notifier()
        router = RouterNotifier(default=default)
        router.register_chat("cli", cli)

        await router.send("cli", "hi")

        cli.send.assert_awaited_once_with("cli", "hi")
        default.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_registration_is_not_shape_based(self):
        """A chat_id that LOOKS like it should fall under a Telegram-style
        numeric ID must still dispatch to its registered notifier — proves
        dispatch depends on explicit registration, not string shape."""
        default = _notifier()
        http = _notifier()
        router = RouterNotifier(default=default)
        # A chat_id that happens to be all-digit — no longer special-cased.
        router.register_chat("123456", http)

        await router.send("123456", "hi")

        http.send.assert_awaited_once_with("123456", "hi")
        default.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unregister_chat_falls_back_to_default(self):
        default = _notifier()
        http = _notifier()
        router = RouterNotifier(default=default)
        router.register_chat("http_abcd1234", http)

        router.unregister_chat("http_abcd1234")
        await router.send("http_abcd1234", "hi")

        http.send.assert_not_awaited()
        default.send.assert_awaited_once_with("http_abcd1234", "hi")

    def test_unregister_unknown_chat_id_is_a_noop(self):
        router = RouterNotifier(default=_notifier())
        router.unregister_chat("never-registered")  # must not raise

    @pytest.mark.asyncio
    async def test_all_notifier_methods_dispatch_through_registered_chat(self):
        default = _notifier()
        cli = _notifier()
        cli.send_and_get_id = AsyncMock(return_value=42)
        router = RouterNotifier(default=default)
        router.register_chat("cli", cli)

        await router.send_media("cli", "/tmp/x.png", "cap")
        await router.send_with_buttons("cli", "text", [("Yes", "y")])
        result = await router.send_and_get_id("cli", "text")
        await router.delete_message("cli", 7)
        await router.notify_done("cli", "done")

        cli.send_media.assert_awaited_once_with("cli", "/tmp/x.png", "cap")
        cli.send_with_buttons.assert_awaited_once_with("cli", "text", [("Yes", "y")])
        assert result == 42
        cli.delete_message.assert_awaited_once_with("cli", 7)
        cli.notify_done.assert_awaited_once_with("cli", "done")
        default.send_media.assert_not_awaited()
