"""
test_agent_model_override.py
------------------------------
Tests that BaseAgent computes a per-agent model override from settings,
and that it defaults to "" (meaning: use the provider's default_model)
when no override is configured for that agent.

Run:
    python -m pytest tests/test_agent_model_override.py -x -q
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from agents.base import BaseAgent
from core.config import settings as real_settings
from core.protocols import AgentEvent, AgentResponse


class _FakeAgent(BaseAgent):
    name = "business"
    description = "fake"
    autonomy_level = "supervised"

    async def handle(self, event: AgentEvent) -> AgentResponse:
        return AgentResponse(text="", agent_name=self.name)

    async def health_check(self) -> bool:
        return True


class TestBaseAgentModel:
    def test_defaults_to_empty_string_when_no_override(self):
        agent = _FakeAgent(settings=replace(real_settings, business_agent_model=""),
                            storage=None, notifier=None)
        assert agent.model == ""

    def test_picks_up_per_agent_override(self):
        agent = _FakeAgent(
            settings=replace(real_settings, business_agent_model="claude-opus-4.8"),
            storage=None, notifier=None,
        )
        assert agent.model == "claude-opus-4.8"

    def test_unset_agent_name_field_defaults_to_empty_string(self):
        class _NoOverrideAgent(_FakeAgent):
            name = "wellbeing"

        agent = _NoOverrideAgent(
            settings=replace(real_settings, wellbeing_agent_model=""),
            storage=None, notifier=None,
        )
        assert agent.model == ""
