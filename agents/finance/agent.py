"""
agents/finance/agent.py
-----------------------
FinanceAgent — personal finance assistant: expense logging, monthly
budgets, savings goals, and a weekly spending report. General finance
questions fall through to the LLM (when configured) with the
finance-advice skill as context.

One cron schedule:
  Weekly report (Sunday): 0 18 * * 0

Interactive handling:
  "spent 12.50 groceries"        — log an expense with a category
  "set budget groceries 400"     — set a monthly budget for a category
  "set budget 2000"              — set the total monthly budget
  "budget"                       — spending vs budget for this month
  "goal vacation 5000"           — create a savings goal
  "save 200 vacation"            — add money toward a goal
  "goals"                        — progress on all savings goals
  anything else                  — LLM-backed budgeting advice (optional)

State is persisted to agents/finance/state.json (JSON, next to this file).
No bank connections — all data is user-entered via chat.
Autonomy level = autonomous (only touches its own state file).
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from agents.base import BaseAgent
from core.logger import get_logger
from core.protocols import AgentEvent, AgentResponse, EventType, Message

log = get_logger("finance")

_STATE_FILE = Path(__file__).parent / "state.json"
_SKILLS_DIR = Path(__file__).parent / "skills"

_SKILL_ADVICE = "finance-advice"

_AMOUNT = r"\$?(\d+(?:[.,]\d{1,2})?)"


class FinanceAgent(BaseAgent):
    name = "finance"
    description = (
        "Personal finance assistant: logs expenses, tracks monthly budgets "
        "and savings goals, sends a weekly spending report, and gives "
        "plain-language budgeting advice."
    )
    autonomy_level = "autonomous"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.autonomy_level = getattr(
            self.settings, "finance_agent_autonomy", "autonomous"
        )
        self._state_file = _STATE_FILE

    # ── Skill loader access ──────────────────────────────────────────────────

    def _load_skill(self, skill_name: str) -> str:
        path = _SKILLS_DIR / f"{skill_name}.md"
        try:
            return path.read_text()
        except Exception:
            return ""

    # ── State ────────────────────────────────────────────────────────────────

    def _load_state(self) -> dict:
        try:
            return json.loads(self._state_file.read_text())
        except Exception:
            return {}

    def _save_state(self, state: dict) -> None:
        self._state_file.write_text(json.dumps(state, indent=2))

    @staticmethod
    def _parse_amount(raw: str) -> float:
        return float(raw.replace(",", "."))

    def _month_expenses(self, state: dict, when: date | None = None) -> list[dict]:
        """Expenses logged in the month of `when` (default: current month)."""
        when = when or date.today()
        prefix = when.strftime("%Y-%m")
        return [
            e for e in state.get("expenses", [])
            if str(e.get("date", "")).startswith(prefix)
        ]

    # ── Event dispatch ───────────────────────────────────────────────────────

    async def handle(self, event: AgentEvent) -> AgentResponse:
        if event.type == EventType.AGENT_MESSAGE:
            return await self._handle_agent_message(event)

        if event.type == EventType.HEARTBEAT_TICK:
            log.info("Heartbeat tick", event="heartbeat")
            return AgentResponse(text="HEARTBEAT_OK", agent_name=self.name)

        if event.type == EventType.SCHEDULED_TASK:
            task = (event.data or {}).get("task", "")
            if task == "finance_weekly_report":
                return await self._do_weekly_report(event)
            log.warning("Unknown finance task", event="unknown_task", task=task)
            return AgentResponse(text="", agent_name=self.name)

        return await self._handle_interactive(event)

    # ── Weekly report ────────────────────────────────────────────────────────

    async def _do_weekly_report(self, event: AgentEvent) -> AgentResponse:
        if not self.should_notify("finance-report"):
            return AgentResponse(text="", agent_name=self.name)
        state = self._load_state()
        msg = self._build_budget_summary(state, header="Weekly finance report:")

        goals = state.get("goals", {})
        if goals:
            msg += "\n" + self._build_goals_summary(goals)

        await self._send_to_all_chats(msg)
        log.info("Weekly finance report sent", event="finance_weekly_report")
        return AgentResponse(text=msg, agent_name=self.name)

    # ── Interactive user messages ────────────────────────────────────────────

    async def _handle_interactive(self, event: AgentEvent) -> AgentResponse:
        if not self._is_authorized(event.chat_id):
            return AgentResponse(text="Unauthorized.", agent_name=self.name, success=False)

        text = event.text.strip()

        session_id = await self.storage.get_or_create_session(event.chat_id, self.name)
        await self.storage.save_message(session_id, "user", event.text, self.name)

        response_text = await self._build_interactive_response(text)

        if response_text:
            await self.notifier.send(event.chat_id, response_text)
            await self.storage.save_message(session_id, "assistant", response_text, self.name)

        return AgentResponse(text=response_text, agent_name=self.name)

    async def _build_interactive_response(self, text: str) -> str:
        state = self._load_state()
        lower = text.lower()

        # "spent 12.50 groceries" / "spent $12 on groceries"
        m = re.match(rf"^spent\s+{_AMOUNT}(?:\s+(?:on\s+)?(.+))?$", lower)
        if m:
            return self._log_expense(state, m)

        # "set budget groceries 400" / "set budget 2000"
        m = re.match(rf"^set\s+budget\s+(?:([a-z][\w-]*)\s+)?{_AMOUNT}$", lower)
        if m:
            return self._set_budget(state, m)

        # "budget" / "budget status" / "spending"
        if lower in ("budget", "budget status", "spending", "spending status"):
            return self._build_budget_summary(state, header="This month:")

        # "goal vacation 5000" / "new goal vacation 5000"
        m = re.match(rf"^(?:new\s+)?goal\s+([a-z][\w-]*)\s+{_AMOUNT}$", lower)
        if m:
            return self._create_goal(state, m)

        # "save 200 vacation" / "save 200 to vacation"
        m = re.match(rf"^save\s+{_AMOUNT}(?:\s+(?:to\s+|for\s+)?([a-z][\w-]*))?$", lower)
        if m:
            return self._add_savings(state, m)

        if lower in ("goals", "savings", "goal status"):
            goals = state.get("goals", {})
            if not goals:
                return "No savings goals yet. Create one with 'goal vacation 5000'."
            return self._build_goals_summary(goals)

        if any(kw in lower for kw in ["what can you", "what do you", "help", "capabilities"]):
            return (
                "I'm your finance assistant. Commands:\n"
                "- 'spent 12.50 groceries' — log an expense\n"
                "- 'set budget groceries 400' — monthly budget per category\n"
                "- 'set budget 2000' — total monthly budget\n"
                "- 'budget' — spending vs budget this month\n"
                "- 'goal vacation 5000' — create a savings goal\n"
                "- 'save 200 vacation' — add toward a goal\n"
                "- 'goals' — savings progress\n"
                "Anything else: ask me a budgeting question."
            )

        # Fall through: LLM-backed advice grounded in the user's numbers
        return await self._llm_advice(text, state)

    # ── Command implementations ──────────────────────────────────────────────

    def _log_expense(self, state: dict, m: re.Match) -> str:
        amount = self._parse_amount(m.group(1))
        category = (m.group(2) or "uncategorized").strip().split()[0]
        state.setdefault("expenses", []).append(
            {"date": date.today().isoformat(), "amount": amount, "category": category}
        )
        self._save_state(state)

        month_total = sum(e["amount"] for e in self._month_expenses(state))
        cat_total = sum(
            e["amount"]
            for e in self._month_expenses(state)
            if e.get("category") == category
        )
        parts = [f"Logged {amount:.2f} on {category}."]
        budget = state.get("budgets", {}).get(category)
        if budget:
            parts.append(f"{category}: {cat_total:.2f}/{budget:.2f} this month.")
        total_budget = state.get("budgets", {}).get("total")
        if total_budget:
            parts.append(f"Total: {month_total:.2f}/{total_budget:.2f}.")
            if month_total > total_budget:
                parts.append("You're over budget.")
        return " ".join(parts)

    def _set_budget(self, state: dict, m: re.Match) -> str:
        category = (m.group(1) or "total").strip()
        amount = self._parse_amount(m.group(2))
        state.setdefault("budgets", {})[category] = amount
        self._save_state(state)
        label = "Total monthly budget" if category == "total" else f"Monthly budget for {category}"
        return f"{label} set to {amount:.2f}."

    def _create_goal(self, state: dict, m: re.Match) -> str:
        name = m.group(1)
        target = self._parse_amount(m.group(2))
        goals = state.setdefault("goals", {})
        existing = goals.get(name, {})
        goals[name] = {"target": target, "saved": existing.get("saved", 0.0)}
        self._save_state(state)
        return f"Goal '{name}' set: {target:.2f}. Add to it with 'save 100 {name}'."

    def _add_savings(self, state: dict, m: re.Match) -> str:
        amount = self._parse_amount(m.group(1))
        goals = state.setdefault("goals", {})
        name = m.group(2)
        if not name:
            if len(goals) == 1:
                name = next(iter(goals))
            else:
                return "Which goal? Say 'save 200 <goal-name>'."
        if name not in goals:
            return f"No goal named '{name}'. Create it with 'goal {name} <target>'."
        goal = goals[name]
        goal["saved"] = round(goal.get("saved", 0.0) + amount, 2)
        self._save_state(state)
        pct = goal["saved"] / goal["target"] * 100 if goal["target"] else 100
        if goal["saved"] >= goal["target"]:
            return f"Added {amount:.2f} to '{name}'. Goal reached: {goal['saved']:.2f}/{goal['target']:.2f}. Well done."
        return f"Added {amount:.2f} to '{name}': {goal['saved']:.2f}/{goal['target']:.2f} ({pct:.0f}%)."

    # ── Summaries ────────────────────────────────────────────────────────────

    def _build_budget_summary(self, state: dict, header: str) -> str:
        expenses = self._month_expenses(state)
        budgets = state.get("budgets", {})
        if not expenses and not budgets:
            return (
                "No expenses or budgets yet. Log spending with "
                "'spent 12.50 groceries' and set a budget with 'set budget 2000'."
            )

        by_cat: dict[str, float] = {}
        for e in expenses:
            cat = e.get("category", "uncategorized")
            by_cat[cat] = by_cat.get(cat, 0.0) + float(e.get("amount", 0))
        total = sum(by_cat.values())

        lines = [header]
        for cat in sorted(by_cat, key=by_cat.get, reverse=True):
            line = f"- {cat}: {by_cat[cat]:.2f}"
            if cat in budgets:
                line += f" / {budgets[cat]:.2f}"
                if by_cat[cat] > budgets[cat]:
                    line += " (over)"
            lines.append(line)
        total_line = f"Total: {total:.2f}"
        if "total" in budgets:
            total_line += f" / {budgets['total']:.2f}"
            if total > budgets["total"]:
                total_line += " — over budget"
            else:
                total_line += f" — {budgets['total'] - total:.2f} left"
        lines.append(total_line)
        return "\n".join(lines)

    def _build_goals_summary(self, goals: dict) -> str:
        lines = ["Savings goals:"]
        for name, g in goals.items():
            target = g.get("target", 0.0)
            saved = g.get("saved", 0.0)
            pct = saved / target * 100 if target else 100
            lines.append(f"- {name}: {saved:.2f}/{target:.2f} ({pct:.0f}%)")
        return "\n".join(lines)

    # ── LLM advice ───────────────────────────────────────────────────────────

    async def _llm_advice(self, text: str, state: dict) -> str:
        if self.llm is None:
            return (
                "I can track expenses, budgets, and savings goals — say 'help' "
                "for commands. Advice questions need an LLM provider configured."
            )
        skill = self._load_skill(_SKILL_ADVICE)
        snapshot = self._build_budget_summary(state, header="Current month spending:")
        goals = state.get("goals", {})
        if goals:
            snapshot += "\n" + self._build_goals_summary(goals)
        system = (
            "You are a personal finance assistant. Give clear, actionable, "
            "jargon-free budgeting advice grounded in the user's actual numbers "
            "below. Keep answers short and concrete.\n\n"
            f"{skill}\n\nUser's data:\n{snapshot}"
        )
        try:
            reply = await self.llm.complete(
                messages=[Message(role="user", content=text)],
                system=system,
                max_tokens=self.settings.default_max_tokens,
            )
            return reply if isinstance(reply, str) else str(reply)
        except Exception as e:
            log.warning("LLM advice failed", event="finance_llm_error", error=str(e))
            return "Couldn't reach the LLM for advice right now. Try again in a bit."

    # ── Delivery ─────────────────────────────────────────────────────────────

    async def _send_to_all_chats(self, msg: str) -> None:
        for chat_id in self.settings.telegram_allowed_chat_ids:
            await self.notifier.send(chat_id, msg)

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def register_schedules(self, bus) -> None:
        await super().register_schedules(bus)
        try:
            from core.scheduler import scheduler

            chat_id = (
                self.settings.telegram_allowed_chat_ids[0]
                if self.settings.telegram_allowed_chat_ids
                else ""
            )
            scheduler.add_cron_job(
                cron="0 18 * * 0",
                event=AgentEvent(
                    type=EventType.SCHEDULED_TASK,
                    agent_name=self.name,
                    chat_id=chat_id,
                    data={"task": "finance_weekly_report"},
                ),
                bus=bus,
            )
            log.info("Schedules registered", event="schedules_registered", agent=self.name)
        except (ImportError, AttributeError) as e:
            log.warning("Could not register schedules", event="schedule_error", error=str(e))

    async def health_check(self) -> bool:
        return True
