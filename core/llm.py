"""
core/llm.py
-----------
Wraps AsyncAnthropic and AsyncOpenAI behind the LLMProvider Protocol.

Usage:
    from core.llm import KiloLLM
    llm = KiloLLM(api_key=settings.kilo_api_key)
    response = await llm.complete(messages, system="You are helpful.")
"""

from __future__ import annotations

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from core.config import settings
from core.logger import get_logger
from core.protocols import LLMProvider, Message

log = get_logger("llm")


def _is_retryable(exc: BaseException) -> bool:
    """Return True for transient HTTP errors worth retrying (429, 503, 529)."""
    return getattr(exc, "status_code", None) in (429, 503, 529)


def _log_retry(retry_state) -> None:
    exc = retry_state.outcome.exception()
    log.warning(
        "LLM call failed, retrying",
        event="llm_retry",
        attempt=retry_state.attempt_number,
        error=str(exc),
    )


_llm_retry = retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential(
        multiplier=1,
        min=settings.llm_retry_min_wait,
        max=settings.llm_retry_max_wait,
    ),
    stop=stop_after_attempt(settings.llm_max_retries),
    before_sleep=_log_retry,
    reraise=True,
)

class KiloLLM:
    """Implements LLMProvider using Kilo's OpenAI-compatible endpoint."""

    def __init__(self, api_key: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=settings.kilo_base_url)

    @_llm_retry
    async def complete(
        self,
        messages: list[Message],
        system: str,
        model: str = "",
        max_tokens: int = 0,
    ) -> str:
        model = model or settings.default_model
        max_tokens = max_tokens or settings.default_max_tokens

        api_messages = [{"role": "system", "content": system}] + [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]

        with log.timer() as t:
            response = await self._client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=api_messages,  # type: ignore
            )

        text = response.choices[0].message.content or "" if response.choices else ""
        usage = response.usage
        log.info(
            "LLM call complete",
            event="llm_complete",
            provider="kilo",
            model=model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            duration_ms=t.ms,
        )
        return text

    async def summarize(self, messages: list[Message]) -> str:
        """Summarize a conversation for session compaction."""
        system = (
            "You are a conversation summarizer. Condense the following conversation "
            "into a brief summary that preserves key facts, decisions, and context. "
            "Be concise but retain important details the user mentioned."
        )
        return await self.complete(messages, system=system, max_tokens=512)

class AnthropicLLM:
    """Implements the LLMProvider Protocol using Anthropic's Messages API."""

    def __init__(self, api_key: str) -> None:
        self._client = AsyncAnthropic(api_key=api_key)

    @_llm_retry
    async def complete(
        self,
        messages: list[Message],
        system: str,
        model: str = "",
        max_tokens: int = 0,
    ) -> str:
        model = model or settings.default_model
        max_tokens = max_tokens or settings.default_max_tokens

        # Filter to only user/assistant roles for the API
        api_messages = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]

        if not api_messages:
            return ""

        with log.timer() as t:
            response = await self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=api_messages,  # type: ignore
            )

        # Extract text from response
        text = response.content[0].text if response.content else ""

        # Log token usage
        usage = response.usage
        log.info(
            "LLM call complete",
            event="llm_complete",
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            duration_ms=t.ms,
        )

        return text

    async def summarize(self, messages: list[Message]) -> str:
        """Summarize a conversation for session compaction."""
        system = (
            "You are a conversation summarizer. Condense the following conversation "
            "into a brief summary that preserves key facts, decisions, and context. "
            "Be concise but retain important details the user mentioned."
        )
        return await self.complete(messages, system=system, max_tokens=512)

def get_llm_provider() -> LLMProvider:
    if settings.kilo_api_key:
        return KiloLLM(settings.kilo_api_key)
    return AnthropicLLM(settings.anthropic_api_key)