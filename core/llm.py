"""
core/llm.py
-----------
Wraps AsyncAnthropic and AsyncOpenAI behind the LLMProvider Protocol.
Supports multiple LLM providers: Kilo, Anthropic, OpenRouter, and Ollama.

Usage:
    from core.llm import get_llm_provider
    llm = get_llm_provider()
    response = await llm.complete(messages, system="You are helpful.")
"""

from __future__ import annotations

import json
import httpx
from typing import Any

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
from core.protocols import LLMProvider, LLMResult, Message, ToolCall, ToolDef, ToolResultInput

log = get_logger("llm")


def _is_retryable(exc: BaseException) -> bool:
    """Return True for transient HTTP errors worth retrying (429, 503, 529)."""
    # Handle httpx and openai/anthropic exceptions
    status = getattr(exc, "status_code", None)
    if status is None and hasattr(exc, "response"):
        status = getattr(exc.response, "status_code", None)
    return status in (429, 503, 529)


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


def _openai_tools_kwarg(tools: list["ToolDef"] | None) -> dict[str, Any]:
    """Build the `tools=` kwarg for an OpenAI-compatible chat.completions.create call."""
    if not tools:
        return {}
    return {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]
    }


def _parse_openai_response(response: Any) -> "LLMResult":
    """Extract text/tool_calls from an OpenAI-compatible chat.completions response."""
    if not response.choices:
        return LLMResult()
    message = response.choices[0].message
    text = message.content or ""

    tool_calls: list[ToolCall] = []
    if message.tool_calls:
        for tc in message.tool_calls:
            tool_calls.append(
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    args=json.loads(tc.function.arguments or "{}"),
                )
            )

    return LLMResult(
        text=text,
        tool_calls=tool_calls,
        raw_assistant=message.tool_calls if tool_calls else None,
    )


class KiloLLM:
    """Implements LLMProvider using Kilo's OpenAI-compatible endpoint."""

    supports_tools = True

    def __init__(self, api_key: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=settings.kilo_base_url)

    @_llm_retry
    async def complete(
        self,
        messages: list[Message],
        system: str,
        model: str = "",
        max_tokens: int = 0,
        tools: list["ToolDef"] | None = None,
        tool_result: "ToolResultInput | None" = None,
        raw_assistant: Any = None,
    ) -> "LLMResult":
        model = model or settings.default_model
        max_tokens = max_tokens or settings.default_max_tokens

        api_messages = [{"role": "system", "content": system}] + [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]

        if tool_result is not None and raw_assistant is not None:
            api_messages.append({
                "role": "assistant", "content": None, "tool_calls": raw_assistant,
            })
            api_messages.append({
                "role": "tool",
                "tool_call_id": tool_result.tool_call_id,
                "content": tool_result.content,
            })

        with log.timer() as t:
            response = await self._client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=api_messages,  # type: ignore
                **_openai_tools_kwarg(tools),
            )

        result = _parse_openai_response(response)
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
        return result

    async def summarize(self, messages: list[Message]) -> str:
        """Summarize a conversation for session compaction."""
        system = (
            "You are a conversation summarizer. Condense the following conversation "
            "into a brief summary that preserves key facts, decisions, and context. "
            "Be concise but retain important details the user mentioned."
        )
        result = await self.complete(messages, system=system, max_tokens=512)
        return result.text

class AnthropicLLM:
    """Implements the LLMProvider Protocol using Anthropic's Messages API."""

    supports_tools = True

    def __init__(self, api_key: str) -> None:
        self._client = AsyncAnthropic(api_key=api_key)

    @_llm_retry
    async def complete(
        self,
        messages: list[Message],
        system: str,
        model: str = "",
        max_tokens: int = 0,
        tools: list["ToolDef"] | None = None,
        tool_result: "ToolResultInput | None" = None,
        raw_assistant: Any = None,
    ) -> "LLMResult":
        model = model or settings.default_model
        max_tokens = max_tokens or settings.default_max_tokens

        api_messages = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]

        if tool_result is not None and raw_assistant is not None:
            api_messages.append({"role": "assistant", "content": raw_assistant})
            api_messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_result.tool_call_id,
                    "content": tool_result.content,
                }],
            })

        if not api_messages:
            return LLMResult()

        extra_kwargs: dict[str, Any] = {}
        if tools:
            extra_kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]

        with log.timer() as t:
            response = await self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=api_messages,  # type: ignore
                **extra_kwargs,
            )

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, args=block.input))

        usage = response.usage
        log.info(
            "LLM call complete",
            event="llm_complete",
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            duration_ms=t.ms,
        )

        return LLMResult(
            text="".join(text_parts),
            tool_calls=tool_calls,
            raw_assistant=list(response.content) if tool_calls else None,
        )

    async def summarize(self, messages: list[Message]) -> str:
        """Summarize a conversation for session compaction."""
        system = (
            "You are a conversation summarizer. Condense the following conversation "
            "into a brief summary that preserves key facts, decisions, and context. "
            "Be concise but retain important details the user mentioned."
        )
        result = await self.complete(messages, system=system, max_tokens=512)
        return result.text

class OpenRouterLLM:
    """Implements LLMProvider using OpenRouter's OpenAI-compatible endpoint."""

    supports_tools = True

    def __init__(self, api_key: str) -> None:
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
        )

    @_llm_retry
    async def complete(
        self,
        messages: list[Message],
        system: str,
        model: str = "",
        max_tokens: int = 0,
        tools: list["ToolDef"] | None = None,
        tool_result: "ToolResultInput | None" = None,
        raw_assistant: Any = None,
    ) -> "LLMResult":
        model = model or settings.default_model
        max_tokens = max_tokens or settings.default_max_tokens

        api_messages = [{"role": "system", "content": system}] + [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]

        if tool_result is not None and raw_assistant is not None:
            api_messages.append({
                "role": "assistant", "content": None, "tool_calls": raw_assistant,
            })
            api_messages.append({
                "role": "tool",
                "tool_call_id": tool_result.tool_call_id,
                "content": tool_result.content,
            })

        with log.timer() as t:
            response = await self._client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=api_messages,
                extra_headers={
                    "HTTP-Referer": "https://github.com/juanfrank77/modular-agents",
                    "X-Title": "Modular Agents",
                },
                **_openai_tools_kwarg(tools),
            )

        result = _parse_openai_response(response)
        usage = response.usage
        log.info(
            "LLM call complete",
            event="llm_complete",
            provider="openrouter",
            model=model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            duration_ms=t.ms,
        )
        return result

    async def summarize(self, messages: list[Message]) -> str:
        """Summarize a conversation for session compaction."""
        system = (
            "You are a conversation summarizer. Condense the following conversation "
            "into a brief summary that preserves key facts, decisions, and context. "
            "Be concise but retain important details the user mentioned."
        )
        result = await self.complete(messages, system=system, max_tokens=512)
        return result.text

# ──────────────────────────────────────────────
# Retry decorator for OpenAI-compatible APIs
# ──
# Note: Ollama uses httpx directly and may not raise retryable HTTP errors
# in the same way. Users can wrap calls in their own retry logic if needed.
# ──────────────────────────────────────────────

_ollama_no_retry = retry(
    retry=retry_if_exception(lambda exc: False),  # never retry for Ollama (local)
    reraise=True,
)


class OllamaLLM:
    """Implements LLMProvider using Ollama's local API."""

    supports_tools = False

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=120.0)

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    @_ollama_no_retry
    async def complete(
        self,
        messages: list[Message],
        system: str,
        model: str = "",
        max_tokens: int = 0,
        tools: list["ToolDef"] | None = None,
        tool_result: "ToolResultInput | None" = None,
        raw_assistant: Any = None,
    ) -> "LLMResult":
        model = model or settings.default_model or "llama3"
        max_tokens = max_tokens or settings.default_max_tokens

        ollama_messages = [
            {"role": "system", "content": system}
        ] + [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]

        with log.timer() as t:
            response = await self._client.post(
                "/api/chat",
                json={
                    "model": model,
                    "messages": ollama_messages,
                    "stream": False,
                    "options": {"num_predict": max_tokens},
                },
            )
            response.raise_for_status()
            data = response.json()

        text = data.get("message", {}).get("content", "")
        log.info(
            "LLM call complete",
            event="llm_complete",
            provider="ollama",
            model=model,
            duration_ms=t.ms,
        )
        return LLMResult(text=text)

    async def summarize(self, messages: list[Message]) -> str:
        """Summarize a conversation for session compaction."""
        system = (
            "You are a conversation summarizer. Condense the following conversation "
            "into a brief summary that preserves key facts, decisions, and context. "
            "Be concise but retain important details the user mentioned."
        )
        result = await self.complete(messages, system=system, max_tokens=512)
        return result.text


def get_llm_provider() -> LLMProvider:
    """Return the first available LLM provider based on configuration priority."""
    if settings.kilo_api_key:
        log.info("LLM provider selected", event="llm_provider", provider="kilo")
        return KiloLLM(settings.kilo_api_key)
    if settings.openrouter_api_key:
        log.info("LLM provider selected", event="llm_provider", provider="openrouter")
        return OpenRouterLLM(settings.openrouter_api_key)
    if settings.ollama_base_url:
        log.info("LLM provider selected", event="llm_provider", provider="ollama")
        return OllamaLLM(settings.ollama_base_url)
    if not settings.anthropic_api_key:
        print(
            "[config] FATAL: No LLM provider configured. Set at least one of:\n"
            "  - KILO_API_KEY\n"
            "  - OPENROUTER_API_KEY\n"
            "  - OLLAMA_BASE_URL (for local models)\n"
            "  - ANTHROPIC_API_KEY (fallback)\n"
        )
        import sys
        sys.exit(1)
    log.info("LLM provider selected", event="llm_provider", provider="anthropic")
    return AnthropicLLM(settings.anthropic_api_key)