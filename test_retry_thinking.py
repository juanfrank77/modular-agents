"""
test_retry_thinking.py
----------------------
TDD tests for two features:
  1. LLM retry logic — backoff on 429/503/529
  2. Thinking indicator — send placeholder, delete after response

Run:
    python test_retry_thinking.py
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from unittest.mock import AsyncMock, MagicMock, patch

GREEN = "\033[32m"
RED   = "\033[31m"
RESET = "\033[0m"

passed = 0
failed = 0


def ok(name: str) -> None:
    global passed
    passed += 1
    print(f"  {GREEN}PASS{RESET}  {name}")


def fail(name: str, reason: str = "") -> None:
    global failed
    failed += 1
    print(f"  {RED}FAIL{RESET}  {name}" + (f"\n         {reason}" if reason else ""))


def run(name: str, coro):
    try:
        asyncio.run(coro)
        ok(name)
    except AssertionError as e:
        fail(name, str(e))
    except Exception as e:
        fail(name, traceback.format_exc().strip().splitlines()[-1])


# ─────────────────────────────────────────────────────────
# Helper: build a minimal fake OpenAI-style response
# ─────────────────────────────────────────────────────────

def _openai_response(text: str):
    choice = MagicMock()
    choice.message.content = text
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage.prompt_tokens = 10
    resp.usage.completion_tokens = 5
    return resp


def _anthropic_response(text: str):
    content_block = MagicMock()
    content_block.text = text
    resp = MagicMock()
    resp.content = [content_block]
    resp.usage.input_tokens = 10
    resp.usage.output_tokens = 5
    return resp


# ─────────────────────────────────────────────────────────
# Feature 1: LLM retry logic
# ─────────────────────────────────────────────────────────

print("\nLLM retry logic")
print("─" * 40)


async def test_kilo_retries_on_429():
    """KiloLLM.complete() retries once after a 429 RateLimitError."""
    import openai
    from core.llm import KiloLLM

    call_count = 0

    async def mock_create(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            mock_resp = MagicMock()
            mock_resp.status_code = 429
            raise openai.RateLimitError(
                "rate limited",
                response=mock_resp,
                body={"error": {"message": "rate limited"}},
            )
        return _openai_response("hello")

    llm = KiloLLM.__new__(KiloLLM)
    llm._client = MagicMock()
    llm._client.chat.completions.create = mock_create

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await llm.complete(messages=[], system="test")

    assert call_count == 2, f"expected 2 calls, got {call_count}"
    assert result == "hello"


run("KiloLLM retries on 429", test_kilo_retries_on_429())


async def test_kilo_does_not_retry_on_400():
    """KiloLLM.complete() does not retry on non-retryable errors (e.g. 400)."""
    import openai
    from core.llm import KiloLLM

    call_count = 0

    async def mock_create(**kwargs):
        nonlocal call_count
        call_count += 1
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        raise openai.BadRequestError(
            "bad request",
            response=mock_resp,
            body={"error": {"message": "bad request"}},
        )

    llm = KiloLLM.__new__(KiloLLM)
    llm._client = MagicMock()
    llm._client.chat.completions.create = mock_create

    try:
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await llm.complete(messages=[], system="test")
        assert False, "expected exception to propagate"
    except openai.BadRequestError:
        pass

    assert call_count == 1, f"should not retry 400, got {call_count} calls"


run("KiloLLM does not retry on 400", test_kilo_does_not_retry_on_400())


async def test_anthropic_retries_on_429():
    """AnthropicLLM.complete() retries once after a 429 RateLimitError."""
    import anthropic
    from core.llm import AnthropicLLM
    from core.protocols import Message

    call_count = 0

    async def mock_create(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            mock_resp = MagicMock()
            mock_resp.status_code = 429
            raise anthropic.RateLimitError(
                message="rate limited",
                response=mock_resp,
                body={"error": {"message": "rate limited"}},
            )
        return _anthropic_response("world")

    llm = AnthropicLLM.__new__(AnthropicLLM)
    llm._client = MagicMock()
    llm._client.messages.create = mock_create

    msgs = [Message(role="user", content="hi")]
    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await llm.complete(messages=msgs, system="test")

    assert call_count == 2, f"expected 2 calls, got {call_count}"
    assert result == "world"


run("AnthropicLLM retries on 429", test_anthropic_retries_on_429())


async def test_anthropic_retries_on_529():
    """AnthropicLLM.complete() retries on Anthropic-specific 529 overload error."""
    import anthropic
    from core.llm import AnthropicLLM
    from core.protocols import Message

    call_count = 0

    async def mock_create(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            mock_resp = MagicMock()
            mock_resp.status_code = 529
            raise anthropic.APIStatusError(
                message="overloaded",
                response=mock_resp,
                body={"error": {"message": "overloaded"}},
            )
        return _anthropic_response("ok")

    llm = AnthropicLLM.__new__(AnthropicLLM)
    llm._client = MagicMock()
    llm._client.messages.create = mock_create

    msgs = [Message(role="user", content="hi")]
    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await llm.complete(messages=msgs, system="test")

    assert call_count == 2, f"expected 2 calls, got {call_count}"


run("AnthropicLLM retries on 529", test_anthropic_retries_on_529())


# ─────────────────────────────────────────────────────────
# Feature 2: Thinking indicator
# ─────────────────────────────────────────────────────────

print("\nThinking indicator")
print("─" * 40)


async def test_send_and_get_id_returns_message_id():
    """TelegramNotifier.send_and_get_id() returns the Telegram message ID."""
    from core.notifier import TelegramNotifier

    notifier = TelegramNotifier.__new__(TelegramNotifier)
    notifier._bot = MagicMock()

    fake_msg = MagicMock()
    fake_msg.message_id = 42
    notifier._bot.send_message = AsyncMock(return_value=fake_msg)

    msg_id = await notifier.send_and_get_id("123", "⏳ Thinking...")

    assert msg_id == 42, f"expected 42, got {msg_id}"
    notifier._bot.send_message.assert_awaited_once()


run("send_and_get_id returns message ID", test_send_and_get_id_returns_message_id())


async def test_delete_message_calls_bot():
    """TelegramNotifier.delete_message() calls bot.delete_message with correct args."""
    from core.notifier import TelegramNotifier

    notifier = TelegramNotifier.__new__(TelegramNotifier)
    notifier._bot = MagicMock()
    notifier._bot.delete_message = AsyncMock()

    await notifier.delete_message("123", 99)

    notifier._bot.delete_message.assert_awaited_once_with(chat_id=123, message_id=99)


run("delete_message calls bot.delete_message", test_delete_message_calls_bot())


async def test_bus_send_thinking_returns_id():
    """MessageBus.send_thinking() delegates to notifier and returns message ID."""
    from core.bus import MessageBus

    bus = MessageBus()
    mock_notifier = MagicMock()
    mock_notifier.send_and_get_id = AsyncMock(return_value=77)

    mock_agent = MagicMock()
    mock_agent.name = "test_agent"
    mock_agent.notifier = mock_notifier
    bus.register(mock_agent)

    msg_id = await bus.send_thinking("chat_1")

    assert msg_id == 77, f"expected 77, got {msg_id}"
    mock_notifier.send_and_get_id.assert_awaited_once_with("chat_1", "⏳ Thinking...")


run("bus.send_thinking returns notifier message ID", test_bus_send_thinking_returns_id())


async def test_bus_clear_thinking_deletes_message():
    """MessageBus.clear_thinking() delegates delete to the notifier."""
    from core.bus import MessageBus

    bus = MessageBus()
    mock_notifier = MagicMock()
    mock_notifier.delete_message = AsyncMock()

    mock_agent = MagicMock()
    mock_agent.name = "test_agent"
    mock_agent.notifier = mock_notifier
    bus.register(mock_agent)

    await bus.clear_thinking("chat_1", 77)

    mock_notifier.delete_message.assert_awaited_once_with("chat_1", 77)


run("bus.clear_thinking deletes the thinking message", test_bus_clear_thinking_deletes_message())


# ─────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────

print()
total = passed + failed
color = GREEN if failed == 0 else RED
print(f"{color}{passed}/{total} passed{RESET}")
sys.exit(0 if failed == 0 else 1)
