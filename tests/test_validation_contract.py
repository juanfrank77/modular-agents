"""
test_validation_contract.py
----------------------------
Tests for BaseAgent._run_validation_contract().
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.echo.agent import EchoAgent
from core.protocols import AgentEvent, EventType, LLMResult


def _make_agent(llm_response_text="1. PASS - all good"):
    settings = MagicMock()
    settings.telegram_allowed_chat_ids = []
    notifier = MagicMock()
    notifier.send = AsyncMock()
    llm = MagicMock()
    llm.complete = AsyncMock(return_value=LLMResult(text=llm_response_text))
    storage = MagicMock()
    return EchoAgent(settings=settings, storage=storage, notifier=notifier, llm=llm)


def _make_event():
    return AgentEvent(
        type=EventType.USER_MESSAGE,
        agent_name="echo",
        chat_id="123",
        text="validate this",
    )


class TestValidationContractParsing:
    @pytest.mark.asyncio
    async def test_parse_assertions(self):
        agent = _make_agent()
        event = _make_event()
        contract = """
        Assertions:
        - [ ] All new endpoints return 200 on smoke test
        - [ ] `pytest` passes with zero failures
        """
        response = await agent._run_validation_contract(event, contract)
        assert response.agent_name == "echo"
        assert "PASS" in response.text or "FAIL" in response.text

    @pytest.mark.asyncio
    async def test_no_assertions_returns_failure(self):
        agent = _make_agent()
        event = _make_event()
        contract = "No assertions here."
        response = await agent._run_validation_contract(event, contract)
        assert response.success is False
        assert "No validation assertions found" in response.text

    @pytest.mark.asyncio
    async def test_llm_not_available_returns_failure(self):
        settings = MagicMock()
        settings.telegram_allowed_chat_ids = []
        notifier = MagicMock()
        notifier.send = AsyncMock()
        storage = MagicMock()
        agent = EchoAgent(
            settings=settings,
            storage=storage,
            notifier=notifier,
            llm=None,
        )
        event = _make_event()
        contract = "- [ ] Something"
        response = await agent._run_validation_contract(event, contract)
        assert response.success is False
        assert "LLM not available for validation" in response.text

    @pytest.mark.asyncio
    async def test_llm_exception_returns_failure(self):
        agent = _make_agent()
        agent.llm.complete = AsyncMock(side_effect=RuntimeError("LLM down"))
        event = _make_event()
        contract = "- [ ] Something"
        response = await agent._run_validation_contract(event, contract)
        assert response.success is False
        assert "Validation failed" in response.text

    @pytest.mark.asyncio
    async def test_all_pass_returns_success(self):
        agent = _make_agent(
            llm_response_text="1. PASS - all endpoints return 200\n2. PASS - tests pass"
        )
        event = _make_event()
        contract = """
        - [ ] All new endpoints return 200 on smoke test
        - [ ] `pytest` passes with zero failures
        """
        response = await agent._run_validation_contract(event, contract)
        assert response.success is True

    @pytest.mark.asyncio
    async def test_any_fail_returns_failure(self):
        agent = _make_agent(
            llm_response_text="1. PASS - all endpoints return 200\n2. FAIL - tests failed"
        )
        event = _make_event()
        contract = """
        - [ ] All new endpoints return 200 on smoke test
        - [ ] `pytest` passes with zero failures
        """
        response = await agent._run_validation_contract(event, contract)
        assert response.success is False

    @pytest.mark.asyncio
    async def test_response_data_contains_counts(self):
        agent = _make_agent(
            llm_response_text="1. PASS - all endpoints return 200\n2. PASS - tests pass"
        )
        event = _make_event()
        contract = """
        - [ ] All new endpoints return 200 on smoke test
        - [ ] `pytest` passes with zero failures
        """
        response = await agent._run_validation_contract(event, contract)
        assert "assertions_count" in response.data
        assert "passed" in response.data
        assert response.data["assertions_count"] == 2
        assert response.data["passed"] is True


class TestValidationContractVerdicts:
    @pytest.mark.asyncio
    async def test_fail_word_inside_pass_reason_does_not_flip(self):
        agent = _make_agent(
            llm_response_text="1. PASS - no FAIL found in output"
        )
        event = _make_event()
        contract = "- [ ] Something works"
        response = await agent._run_validation_contract(event, contract)
        assert response.success is True
        assert response.data["passed"] is True

    @pytest.mark.asyncio
    async def test_unparseable_llm_output_returns_failure(self):
        agent = _make_agent(
            llm_response_text="Everything looks fine, I guess. No verdicts here."
        )
        event = _make_event()
        contract = "- [ ] Something works"
        response = await agent._run_validation_contract(event, contract)
        assert response.success is False
        assert response.data["passed"] is False

    @pytest.mark.asyncio
    async def test_partial_coverage_returns_failure(self):
        agent = _make_agent(llm_response_text="1. PASS - ok")
        event = _make_event()
        contract = """
        - [ ] All new endpoints return 200 on smoke test
        - [ ] `pytest` passes with zero failures
        """
        response = await agent._run_validation_contract(event, contract)
        assert response.success is False
        assert response.data["passed"] is False
