# Design: LLM Retry Logic + Thinking Indicator

**Date:** 2026-04-01
**Status:** Approved

---

## Context

Two high-impact UX and reliability gaps in the current bot:

1. **LLM retry logic** — `core/llm.py` makes bare API calls with no handling for transient errors (429 rate-limit, 503/529 service unavailable). Any rate-limit from Kilo or Anthropic causes a hard failure visible to the user.

2. **Thinking indicator** — Users get no feedback while the LLM is processing. The bot appears unresponsive for several seconds. A placeholder "Thinking..." message edited or deleted on completion provides immediate feedback.

---

## Feature 1: LLM Retry Logic

### Goal
Wrap both `KiloLLM.complete()` and `AnthropicLLM.complete()` with exponential backoff on retryable HTTP errors (429, 503, 529).

### Implementation

**`requirements.txt`**
- Add `tenacity>=8.2`

**`core/config.py`** — add to `Settings` dataclass:
```python
llm_max_retries: int = 3
llm_retry_min_wait: int = 2   # seconds
llm_retry_max_wait: int = 60  # seconds
```
Load from env vars: `LLM_MAX_RETRIES`, `LLM_RETRY_MIN_WAIT`, `LLM_RETRY_MAX_WAIT`.

**`core/llm.py`** — add module-level retry predicate and decorator:
```python
def _is_retryable(exc) -> bool:
    return getattr(exc, "status_code", None) in (429, 503, 529)

_llm_retry = retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential(multiplier=1, min=settings.llm_retry_min_wait, max=settings.llm_retry_max_wait),
    stop=stop_after_attempt(settings.llm_max_retries),
    before_sleep=before_sleep_log(log, logging.WARNING),
)
```

Apply `@_llm_retry` to `KiloLLM.complete()` and `AnthropicLLM.complete()`.

Both SDKs expose `.status_code` on their error types (`openai.RateLimitError`, `openai.APIStatusError`, `anthropic.RateLimitError`, `anthropic.APIStatusError`), so one predicate works for both.

### Files Changed
- `requirements.txt`
- `core/config.py`
- `core/llm.py`

---

## Feature 2: Thinking Indicator

### Goal
Send a "⏳ Thinking..." placeholder Telegram message immediately when a user message arrives, then delete it once the agent's response has been sent.

### Implementation

**`core/notifier.py`** — add two methods to `TelegramNotifier`:
- `send_and_get_id(chat_id, text) -> int | None` — sends a message, returns `message.message_id`
- `delete_message(chat_id, message_id)` — calls `bot.delete_message()`

**`core/bus.py`** — add two thin delegation methods on `MessageBus` (delegates to internal notifier):
- `send_thinking(chat_id) -> int | None` — sends "⏳ Thinking..." via `send_and_get_id`
- `clear_thinking(chat_id, message_id)` — calls `notifier.delete_message()`

**`main.py`** — in `on_message`, wrap `bus.publish`:
```python
thinking_id = await bus.send_thinking(chat_id)
response = await bus.publish(event)
if thinking_id:
    await bus.clear_thinking(chat_id, thinking_id)
```

No changes to agents, `AgentEvent`, `Safety`, or the bus routing logic.

### Files Changed
- `core/notifier.py`
- `core/bus.py`
- `main.py`

---

## Verification

1. Start the bot and send a message — "⏳ Thinking..." should appear, then disappear when the response arrives.
2. Simulate a 429 by temporarily setting an invalid API key — bot should retry with backoff (visible in logs as `WARNING` from tenacity) rather than crashing.
3. Check `LOG_FORMAT=pretty` output shows retry attempts with wait times.
