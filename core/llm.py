"""
core/llm.py
-----------
Wraps anthropic.AsyncAnthropic behind the LLMProvider Protocol.

Usage:
    from core.llm import AnthropicLLM
    llm = AnthropicLLM(api_key=settings.anthropic_api_key)
    response = await llm.complete(messages, system="You are helpful.")
"""

from __future__ import annotations

from anthropic import AsyncAnthropic

from core.config import settings
from core.logger import get_logger
from core.protocols import Message

log = get_logger("llm")


class AnthropicLLM:
    """Implements the LLMProvider Protocol using Anthropic's Messages API."""

    def __init__(self, api_key: str) -> None:
        self._client = AsyncAnthropic(api_key=api_key)

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
                messages=api_messages,
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
