"""
agents/focus/agent.py
---------------------
FocusAgent — a deep work coach: tracks focus sessions, sends a daily
planning prompt and a weekly review, and answers stats questions.

Two cron schedules:
  Daily focus prompt (weekday): 0 10 * * 1-5
  Weekly review (Sunday):       0 17 * * 0

Interactive handling:
  "focus" / "focus 50"      — start a deep work session (optional minutes goal)
  "done" / "stop focus"     — end the active session and log it
  "cancel focus"            — discard the active session without logging
  "stats" / "streak"        — today's and this week's deep work totals

State is persisted to agents/focus/state.json (JSON, next to this file).
Autonomy level = autonomous. No LLM required.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from agents.base import BaseAgent
from core.logger import get_logger
from core.protocols import AgentEvent, AgentResponse, EventType

log = get_logger("focus")

_STATE_FILE = Path(__file__).parent / "state.json"
_SKILLS_DIR = Path(__file__).parent / "skills"

_DEFAULT_SESSION_MINUTES = 50

_PROMPT_MESSAGES = [
    "What's your #1 deep work priority today? Say 'focus 50' when you start.",
    "Pick the one task that matters most today. 'focus 50' to begin a session.",
    "Time to plan: one deep work block, one clear outcome. 'focus' when ready.",
    "What deserves your full attention today? Start with 'focus 50'.",
]


class FocusAgent(BaseAgent):
    name = "focus"
    description = (
        "Deep work coach: starts and tracks focus sessions, sends a daily "
        "planning prompt and a weekly review, and reports focus stats and "
        "streaks. No LLM required."
    )
    autonomy_level = "autonomous"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.autonomy_level = getattr(
            self.settings, "focus_agent_autonomy", "autonomous"
        )
        self._state_file = _STATE_FILE

    # ── State ───────────────────────────────────────────────────────────────

    def _load_state(self) -> dict:
        try:
            return json.loads(self._state_file.read_text())
        except Exception:
            return {}

    def _save_state(self, state: dict) -> None:
        self._state_file.write_text(json.dumps(state, indent=2))

    def _already_sent_today(self, state: dict, key: str) -> bool:
        sent_at = state.get(key)
        if not sent_at:
            return False
        try:
            return datetime.fromisoformat(sent_at).date() == date.today()
        except Exception:
            return False

    def _pick_cyclic(self, messages: list[str]) -> str:
        day_num = datetime.now().timetuple().tm_yday
        return messages[day_num % len(messages)]

    # ── Session helpers ──────────────────────────────────────────────────────

    def _sessions_between(self, state: dict, start: date, end: date) -> list[dict]:
        """Return logged sessions with start <= session date <= end."""
        out = []
        for s in state.get("sessions", []):
            try:
                d = date.fromisoformat(s["date"])
            except Exception:
                continue
            if start <= d <= end:
                out.append(s)
        return out

    def _week_summary(self, state: dict) -> tuple[int, int, int]:
        """Return (session_count, total_minutes, active_days) for the current week."""
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        sessions = self._sessions_between(state, monday, today)
        total = sum(int(s.get("minutes", 0)) for s in sessions)
        days = len({s["date"] for s in sessions})
        return len(sessions), total, days

    # ── Event dispatch ───────────────────────────────────────────────────────

    async def handle(self, event: AgentEvent) -> AgentResponse:
        if event.type == EventType.AGENT_MESSAGE:
            return await self._handle_agent_message(event)

        if event.type == EventType.HEARTBEAT_TICK:
            log.info("Heartbeat tick", event="heartbeat")
            return AgentResponse(text="HEARTBEAT_OK", agent_name=self.name)

        if event.type == EventType.SCHEDULED_TASK:
            task = (event.data or {}).get("task", "")
            dispatch = {
                "focus_daily_prompt": lambda: self._do_daily_prompt(event),
                "focus_weekly_review": lambda: self._do_weekly_review(event),
            }
            handler = dispatch.get(task)
            if handler is None:
                log.warning("Unknown focus task", event="unknown_task", task=task)
                return AgentResponse(text="", agent_name=self.name)
            return await handler()

        return await self._handle_interactive(event)

    # ── Daily planning prompt ────────────────────────────────────────────────

    async def _do_daily_prompt(self, event: AgentEvent) -> AgentResponse:
        if not self.should_notify("focus-nudge"):
            return AgentResponse(text="", agent_name=self.name)
        state = self._load_state()
        if self._already_sent_today(state, "daily_prompt_sent_at"):
            return AgentResponse(text="", agent_name=self.name)

        msg = self._pick_cyclic(_PROMPT_MESSAGES)
        await self._send_to_all_chats(msg)
        state["daily_prompt_sent_at"] = datetime.now().isoformat()
        self._save_state(state)
        log.info("Daily focus prompt sent", event="focus_daily_prompt")
        return AgentResponse(text=msg, agent_name=self.name)

    # ── Weekly review ────────────────────────────────────────────────────────

    async def _do_weekly_review(self, event: AgentEvent) -> AgentResponse:
        if not self.should_notify("focus-nudge"):
            return AgentResponse(text="", agent_name=self.name)
        state = self._load_state()

        count, total, days = self._week_summary(state)
        hours = total / 60
        lines = [
            "Deep work review:",
            f"Sessions: {count} across {days} day(s), {hours:.1f}h total.",
        ]
        if days >= 4:
            lines.append("Strong week of focused work.")
        elif days >= 2:
            lines.append("Decent week. One more focus day next week.")
        elif count > 0:
            lines.append("Light week. Protect one deep work block per day.")
        else:
            lines.append("No sessions logged. Start small: one 25-minute block.")

        streak = state.get("streak", 0)
        if days >= 4:
            streak += 1
        else:
            streak = 0
        if streak >= 2:
            lines.append(f"{streak} strong weeks in a row.")

        msg = "\n".join(lines)
        await self._send_to_all_chats(msg)

        # Keep only the last 8 weeks of sessions to bound state size
        cutoff = date.today() - timedelta(weeks=8)
        state["sessions"] = [
            s
            for s in state.get("sessions", [])
            if s.get("date", "") >= cutoff.isoformat()
        ]
        state["streak"] = streak
        self._save_state(state)
        log.info("Weekly focus review sent", event="focus_weekly_review")
        return AgentResponse(text=msg, agent_name=self.name)

    # ── Interactive user messages ────────────────────────────────────────────

    async def _handle_interactive(self, event: AgentEvent) -> AgentResponse:
        if not self._is_authorized(event.chat_id):
            return AgentResponse(text="Unauthorized.", agent_name=self.name, success=False)

        text = event.text.lower().strip()

        session_id = await self.storage.get_or_create_session(event.chat_id, self.name)
        await self.storage.save_message(session_id, "user", event.text, self.name)

        response_text = self._build_interactive_response(text)

        if response_text:
            await self.notifier.send(event.chat_id, response_text)
            await self.storage.save_message(session_id, "assistant", response_text, self.name)

        return AgentResponse(text=response_text, agent_name=self.name)

    def _build_interactive_response(self, text: str) -> str:
        state = self._load_state()

        if re.match(r"^(start\s+focus|focus)(\s+\d+)?\s*$", text):
            return self._start_session(state, text)

        if any(kw == text or kw in text for kw in ["cancel focus", "abort focus"]):
            return self._cancel_session(state)

        if text in ("done", "stop", "stop focus", "end focus", "finish", "finished"):
            return self._end_session(state)

        if any(kw in text for kw in ["stats", "streak", "how am i", "how did i", "deep work"]):
            return self._respond_stats(state)

        if any(kw in text for kw in ["what can you", "what do you", "help", "capabilities"]):
            return (
                "I'm your deep work coach. Commands:\n"
                "- 'focus 50' — start a focus session (minutes optional)\n"
                "- 'done' — end the session and log it\n"
                "- 'cancel focus' — discard the active session\n"
                "- 'stats' — today's and this week's totals\n"
                "I also send a daily planning prompt and a Sunday review."
            )

        # Fallback: empty (don't respond to unrelated messages)
        return ""

    def _start_session(self, state: dict, text: str) -> str:
        if state.get("active_session"):
            started = state["active_session"].get("started_at", "")
            return (
                "You already have a session running (started "
                f"{self._format_time(started)}). Say 'done' to log it first."
            )
        match = re.search(r"(\d+)", text)
        minutes = int(match.group(1)) if match else _DEFAULT_SESSION_MINUTES
        state["active_session"] = {
            "started_at": datetime.now().isoformat(),
            "goal_minutes": minutes,
        }
        self._save_state(state)
        return f"Focus session started: {minutes} minutes. Say 'done' when you finish. Go deep."

    def _end_session(self, state: dict) -> str:
        active = state.pop("active_session", None)
        if not active:
            return "No active session. Say 'focus 50' to start one."
        try:
            started = datetime.fromisoformat(active["started_at"])
            minutes = max(1, round((datetime.now() - started).total_seconds() / 60))
        except Exception:
            minutes = active.get("goal_minutes", _DEFAULT_SESSION_MINUTES)

        state.setdefault("sessions", []).append(
            {"date": date.today().isoformat(), "minutes": minutes}
        )
        self._save_state(state)

        goal = active.get("goal_minutes", _DEFAULT_SESSION_MINUTES)
        today_total = sum(
            int(s.get("minutes", 0))
            for s in self._sessions_between(state, date.today(), date.today())
        )
        note = "Goal met." if minutes >= goal else f"Goal was {goal} min — still counts."
        return f"Logged {minutes} minutes of deep work. {note} Today: {today_total} min."

    def _cancel_session(self, state: dict) -> str:
        if state.pop("active_session", None) is None:
            return "No active session to cancel."
        self._save_state(state)
        return "Session cancelled. Nothing logged."

    def _respond_stats(self, state: dict) -> str:
        today = date.today()
        today_sessions = self._sessions_between(state, today, today)
        today_min = sum(int(s.get("minutes", 0)) for s in today_sessions)
        count, total, days = self._week_summary(state)

        parts = [
            f"Today: {len(today_sessions)} session(s), {today_min} min.",
            f"This week: {count} session(s) across {days} day(s), {total} min.",
        ]
        streak = state.get("streak", 0)
        if streak >= 2:
            parts.append(f"Streak: {streak} strong weeks.")
        if state.get("active_session"):
            parts.append("A session is running now.")
        return " ".join(parts)

    def _format_time(self, iso_ts: str) -> str:
        try:
            return datetime.fromisoformat(iso_ts).strftime("%H:%M")
        except Exception:
            return "earlier"

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
            schedules = [
                ("focus_daily_prompt", "0 10 * * 1-5"),
                ("focus_weekly_review", "0 17 * * 0"),
            ]
            for task, cron in schedules:
                scheduler.add_cron_job(
                    cron=cron,
                    event=AgentEvent(
                        type=EventType.SCHEDULED_TASK,
                        agent_name=self.name,
                        chat_id=chat_id,
                        data={"task": task},
                    ),
                    bus=bus,
                )
            log.info("Schedules registered", event="schedules_registered", agent=self.name)
        except (ImportError, AttributeError) as e:
            log.warning("Could not register schedules", event="schedule_error", error=str(e))

    async def health_check(self) -> bool:
        return True
