"""Tests for FinanceAgent: expense logging, budgets, savings goals, reports."""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.protocols import AgentEvent, EventType


# ── Helpers ───────────────────────────────────────────────────────────────

def _make_settings(**overrides):
    s = MagicMock()
    s.quiet_hours_enabled = overrides.get("enabled", False)
    s.telegram_allowed_chat_ids = overrides.get("chat_ids", ["123"])
    s.finance_agent_autonomy = "autonomous"
    s.default_max_tokens = 512
    return s


def _make_agent(tmp_path, llm=None, **setting_overrides):
    from agents.finance.agent import FinanceAgent

    settings = _make_settings(**setting_overrides)
    storage = MagicMock()
    storage.get_or_create_session = AsyncMock(return_value="sess-1")
    storage.save_message = AsyncMock()
    notifier = MagicMock()
    notifier.send = AsyncMock()
    agent = FinanceAgent(settings=settings, storage=storage, notifier=notifier, llm=llm)
    agent._state_file = tmp_path / "state.json"
    return agent


# ── Expense logging ───────────────────────────────────────────────────────

class TestExpenses:
    async def test_log_expense_with_category(self, tmp_path):
        agent = _make_agent(tmp_path)
        msg = await agent._build_interactive_response("spent 12.50 groceries")
        assert "12.50" in msg and "groceries" in msg
        state = agent._load_state()
        assert state["expenses"][0]["amount"] == 12.50
        assert state["expenses"][0]["category"] == "groceries"
        assert state["expenses"][0]["date"] == date.today().isoformat()

    async def test_log_expense_dollar_and_on(self, tmp_path):
        agent = _make_agent(tmp_path)
        msg = await agent._build_interactive_response("spent $40 on dinner")
        assert "dinner" in msg

    async def test_log_expense_comma_decimal(self, tmp_path):
        agent = _make_agent(tmp_path)
        await agent._build_interactive_response("spent 12,50 groceries")
        assert agent._load_state()["expenses"][0]["amount"] == 12.50

    async def test_log_expense_no_category(self, tmp_path):
        agent = _make_agent(tmp_path)
        await agent._build_interactive_response("spent 8")
        assert agent._load_state()["expenses"][0]["category"] == "uncategorized"

    async def test_over_total_budget_warns(self, tmp_path):
        agent = _make_agent(tmp_path)
        await agent._build_interactive_response("set budget 100")
        msg = await agent._build_interactive_response("spent 150 rent")
        assert "over budget" in msg.lower()


# ── Budgets ───────────────────────────────────────────────────────────────

class TestBudgets:
    async def test_set_category_budget(self, tmp_path):
        agent = _make_agent(tmp_path)
        msg = await agent._build_interactive_response("set budget groceries 400")
        assert "groceries" in msg
        assert agent._load_state()["budgets"]["groceries"] == 400.0

    async def test_set_total_budget(self, tmp_path):
        agent = _make_agent(tmp_path)
        msg = await agent._build_interactive_response("set budget 2000")
        assert "Total" in msg
        assert agent._load_state()["budgets"]["total"] == 2000.0

    async def test_budget_summary(self, tmp_path):
        agent = _make_agent(tmp_path)
        await agent._build_interactive_response("set budget groceries 400")
        await agent._build_interactive_response("spent 100 groceries")
        await agent._build_interactive_response("spent 500 groceries")
        msg = await agent._build_interactive_response("budget")
        assert "groceries: 600.00 / 400.00" in msg
        assert "(over)" in msg

    async def test_budget_empty_state(self, tmp_path):
        agent = _make_agent(tmp_path)
        msg = await agent._build_interactive_response("budget")
        assert "No expenses or budgets yet" in msg


# ── Savings goals ─────────────────────────────────────────────────────────

class TestGoals:
    async def test_create_goal(self, tmp_path):
        agent = _make_agent(tmp_path)
        msg = await agent._build_interactive_response("goal vacation 5000")
        assert "vacation" in msg
        assert agent._load_state()["goals"]["vacation"]["target"] == 5000.0

    async def test_save_to_goal(self, tmp_path):
        agent = _make_agent(tmp_path)
        await agent._build_interactive_response("goal vacation 5000")
        msg = await agent._build_interactive_response("save 200 vacation")
        assert "200.00/5000.00" in msg and "4%" in msg

    async def test_save_without_name_single_goal(self, tmp_path):
        agent = _make_agent(tmp_path)
        await agent._build_interactive_response("goal vacation 1000")
        msg = await agent._build_interactive_response("save 100")
        assert "vacation" in msg

    async def test_save_without_name_multiple_goals_asks(self, tmp_path):
        agent = _make_agent(tmp_path)
        await agent._build_interactive_response("goal vacation 1000")
        await agent._build_interactive_response("goal laptop 2000")
        msg = await agent._build_interactive_response("save 100")
        assert "Which goal" in msg

    async def test_goal_reached(self, tmp_path):
        agent = _make_agent(tmp_path)
        await agent._build_interactive_response("goal bike 300")
        msg = await agent._build_interactive_response("save 300 bike")
        assert "Goal reached" in msg

    async def test_retarget_keeps_saved(self, tmp_path):
        agent = _make_agent(tmp_path)
        await agent._build_interactive_response("goal bike 300")
        await agent._build_interactive_response("save 100 bike")
        await agent._build_interactive_response("goal bike 500")
        goal = agent._load_state()["goals"]["bike"]
        assert goal["target"] == 500.0 and goal["saved"] == 100.0

    async def test_goals_summary_empty(self, tmp_path):
        agent = _make_agent(tmp_path)
        msg = await agent._build_interactive_response("goals")
        assert "No savings goals yet" in msg


# ── LLM advice fallback ───────────────────────────────────────────────────

class TestAdvice:
    async def test_no_llm_falls_back_to_help_hint(self, tmp_path):
        agent = _make_agent(tmp_path, llm=None)
        msg = await agent._build_interactive_response("how do I save more money?")
        assert "LLM" in msg

    async def test_llm_advice_grounded_in_state(self, tmp_path):
        llm = MagicMock()
        llm.complete = AsyncMock(return_value="Cut dining out by 20%.")
        agent = _make_agent(tmp_path, llm=llm)
        await agent._build_interactive_response("spent 100 dining")
        msg = await agent._build_interactive_response("where does my money go?")
        assert msg == "Cut dining out by 20%."
        system_arg = llm.complete.await_args.kwargs["system"]
        assert "dining" in system_arg  # snapshot included

    async def test_llm_error_is_graceful(self, tmp_path):
        llm = MagicMock()
        llm.complete = AsyncMock(side_effect=RuntimeError("boom"))
        agent = _make_agent(tmp_path, llm=llm)
        msg = await agent._build_interactive_response("advice please")
        assert "Couldn't reach the LLM" in msg


# ── Scheduled report ──────────────────────────────────────────────────────

class TestWeeklyReport:
    async def test_weekly_report_sends(self, tmp_path):
        agent = _make_agent(tmp_path)
        await agent._build_interactive_response("set budget 1000")
        await agent._build_interactive_response("spent 200 groceries")
        await agent._build_interactive_response("goal vacation 5000")
        event = AgentEvent(
            type=EventType.SCHEDULED_TASK,
            agent_name="finance",
            chat_id="123",
            data={"task": "finance_weekly_report"},
        )
        resp = await agent.handle(event)
        assert "Weekly finance report" in resp.text
        assert "vacation" in resp.text
        agent.notifier.send.assert_awaited()

    async def test_heartbeat_ok(self, tmp_path):
        agent = _make_agent(tmp_path)
        event = AgentEvent(type=EventType.HEARTBEAT_TICK, agent_name="finance", chat_id="")
        resp = await agent.handle(event)
        assert resp.text == "HEARTBEAT_OK"
