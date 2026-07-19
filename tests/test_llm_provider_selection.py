"""
test_llm_provider_selection.py
-------------------------------
Tests for core.llm.get_llm_provider() — provider priority, the explicit
LLM_PROVIDER override, and the sys.exit(1)-free failure path.

Run:
    python -m pytest tests/test_llm_provider_selection.py -x -q
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from core.config import settings as real_settings
from core.llm import (
    AnthropicLLM,
    KiloLLM,
    LLMProviderNotConfiguredError,
    OllamaLLM,
    OpenRouterLLM,
    get_llm_provider,
)


def _settings(**overrides):
    base = replace(
        real_settings,
        kilo_api_key="",
        openrouter_api_key="",
        ollama_base_url="",
        anthropic_api_key="",
        llm_provider="",
    )
    return replace(base, **overrides)


class TestProviderPriority:
    def test_kilo_wins_when_multiple_configured(self, monkeypatch):
        monkeypatch.setattr(
            "core.llm.settings",
            _settings(kilo_api_key="k", anthropic_api_key="a"),
        )
        assert isinstance(get_llm_provider(), KiloLLM)

    def test_falls_back_to_openrouter(self, monkeypatch):
        monkeypatch.setattr(
            "core.llm.settings", _settings(openrouter_api_key="o")
        )
        assert isinstance(get_llm_provider(), OpenRouterLLM)

    def test_falls_back_to_ollama(self, monkeypatch):
        monkeypatch.setattr(
            "core.llm.settings", _settings(ollama_base_url="http://localhost:11434")
        )
        assert isinstance(get_llm_provider(), OllamaLLM)

    def test_falls_back_to_anthropic(self, monkeypatch):
        monkeypatch.setattr(
            "core.llm.settings", _settings(anthropic_api_key="a")
        )
        assert isinstance(get_llm_provider(), AnthropicLLM)

    def test_raises_when_nothing_configured(self, monkeypatch):
        monkeypatch.setattr("core.llm.settings", _settings())
        with pytest.raises(LLMProviderNotConfiguredError):
            get_llm_provider()


class TestLLMProviderOverride:
    def test_explicit_override_wins_over_priority(self, monkeypatch):
        monkeypatch.setattr(
            "core.llm.settings",
            _settings(kilo_api_key="k", anthropic_api_key="a", llm_provider="anthropic"),
        )
        assert isinstance(get_llm_provider(), AnthropicLLM)

    def test_override_with_missing_credentials_raises(self, monkeypatch):
        monkeypatch.setattr(
            "core.llm.settings", _settings(llm_provider="kilo")
        )
        with pytest.raises(LLMProviderNotConfiguredError):
            get_llm_provider()

    def test_unknown_override_raises(self, monkeypatch):
        monkeypatch.setattr(
            "core.llm.settings",
            _settings(anthropic_api_key="a", llm_provider="bogus"),
        )
        with pytest.raises(LLMProviderNotConfiguredError):
            get_llm_provider()
