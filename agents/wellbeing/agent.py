"""
agents/wellbeing/agent.py
-------------------------
WellbeingAgent — scheduled nudges with quiet-hours awareness.

Six cron schedules:
  Morning nudge (weekday):  0 7  * * 1-5
  Morning nudge (weekend):  0 8  * * 0,6
  Morning follow-up:        30 8 * * 1-5
  Evening wind-down:        30 19 * * *
  Bedtime:                  0 23 * * *
  Weekly check-in:          0 9  * * 0

State is persisted to agents/wellbeing/state.json (JSON, next to this file).
No LLM required. autonomy_level = autonomous.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from agents.base import BaseAgent
from core.logger import get_logger
from core.protocols import AgentEvent, AgentResponse, EventType

if TYPE_CHECKING:
    from core.bus import MessageBus

log = get_logger("wellbeing")

_STATE_FILE = Path(__file__).parent / "state.json"

_EVENING_MESSAGES = [
    "Your evening. Do something you enjoy. The work will be there tomorrow.",
    "Evening time. Step away from the screens. You've done enough today.",
    "Wind-down time. Whatever makes you happy tonight.",
]

_BEDTIME_MESSAGES = [
    "Bedtime. Sleep is the best investment. Good night.",
    "Time to wind down. Good night.",
    "Bed now = full sleep. Good night.",
]


class WellbeingAgent(BaseAgent):
    name = "wellbeing"
    description = (
        "Sends scheduled wellbeing nudges: morning, evening, bedtime, and weekly check-in. "
        "Respects quiet hours. No LLM required."
    )
    autonomy_level = "autonomous"

    # ── State ─────────────────────────────

    def _load_state(self) -> dict:
        try:
            return json.loads(_STATE_FILE.read_text())
        except Exception:
            return {}

    def _save_state(self, state: dict) -> None:
        _STATE_FILE.write_text(json.dumps(state, indent=2))

    def _already_sent_today(self, state: dict, key: str) -> bool:
        sent_at = state.get(key)
        if not sent_at:
            return False
        try:
            return datetime.fromisoformat(sent_at).date() == date.today()
        except Exception:
            return False

    def _pick_message(self, messages: list[str]) -> str:
        day_num = datetime.now().timetuple().tm_yday
        return messages[day_num % len(messages)]

    # ── Weather ───────────────────────────────

    def _get_weather(self) -> dict | None:
        location = self.settings.wellbeing_location
        if not location:
            return None
        try:
            result = subprocess.run(
                ["curl", "-s", "--max-time", "5", f"wttr.in/{location}?format=j1"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return None
            data = json.loads(result.stdout)
            current = data["current_condition"][0]
            temp_c = int(current["temp_C"])
            desc = current["weatherDesc"][0]["value"].lower()
            precip_mm = float(current.get("precipMM", 0))
            rainy = precip_mm > 0.5 or any(
                w in desc for w in ["rain", "drizzle", "shower", "snow", "sleet"]
            )
            return {"temp": temp_c, "desc": desc, "rainy": rainy}
        except Exception:
            return None

    def _suggest_activity(self, weather: dict | None) -> str:
        if weather is None:
            return "run or yoga"
        if weather["rainy"] or weather["temp"] < -5:
            return "yoga"
        return "run"

    def _build_morning_message(self, is_weekend: bool) -> str:
        weather = self._get_weather()
        if is_weekend:
            if weather:
                return f"Morning. {weather['temp']}C, {weather['desc']}. Routine when you're ready. Enjoy the day."
            return "Morning. Routine when you're ready. Enjoy the day."
        activity = self._suggest_activity(weather)
        if weather:
            return f"Morning. {weather['temp']}C, {weather['desc']}. Good day for {activity}."
        return f"Morning. Good day for {activity}."

    # ── Delivery ──────────────────────────────

    async def _send_to_all_chats(self, msg: str) -> None:
        for chat_id in self.settings.telegram_allowed_chat_ids:
            await self.notifier.send(chat_id, msg)

    # ── Scheduled handlers ────────────────────

    async def _handle_morning(self, is_weekend: bool) -> AgentResponse:
        if not self.should_notify("wellbeing-nudge"):
            return AgentResponse(text="", agent_name=self.name)
        state = self._load_state()
        if self._already_sent_today(state, "morning_nudge_sent_at"):
            return AgentResponse(text="", agent_name=self.name)
        msg = self._build_morning_message(is_weekend)
        await self._send_to_all_chats(msg)
        state["morning_nudge_sent_at"] = datetime.now().isoformat()
        weekly = state.setdefault("weekly_stats", {})
        routine_days = weekly.setdefault("routine_days", [])
        today_str = date.today().isoformat()
        if today_str not in routine_days:
            routine_days.append(today_str)
        self._save_state(state)
        log.info("Morning nudge sent", event="wellbeing_morning", is_weekend=is_weekend)
        return AgentResponse(text=msg, agent_name=self.name)

    async def _handle_followup(self) -> AgentResponse:
        if datetime.now().weekday() >= 5:
            return AgentResponse(text="", agent_name=self.name)
        if not self.should_notify("wellbeing-nudge"):
            return AgentResponse(text="", agent_name=self.name)
        state = self._load_state()
        if self._already_sent_today(state, "morning_followup_sent_at"):
            return AgentResponse(text="", agent_name=self.name)
        msg = "Time to move."
        await self._send_to_all_chats(msg)
        state["morning_followup_sent_at"] = datetime.now().isoformat()
        self._save_state(state)
        log.info("Followup nudge sent", event="wellbeing_followup")
        return AgentResponse(text=msg, agent_name=self.name)

    async def _handle_evening(self) -> AgentResponse:
        if not self.should_notify("wellbeing-nudge"):
            return AgentResponse(text="", agent_name=self.name)
        state = self._load_state()
        if self._already_sent_today(state, "evening_nudge_sent_at"):
            return AgentResponse(text="", agent_name=self.name)
        msg = self._pick_message(_EVENING_MESSAGES)
        await self._send_to_all_chats(msg)
        state["evening_nudge_sent_at"] = datetime.now().isoformat()
        self._save_state(state)
        log.info("Evening nudge sent", event="wellbeing_evening")
        return AgentResponse(text=msg, agent_name=self.name)

    async def _handle_bedtime(self) -> AgentResponse:
        if not self.should_notify("wellbeing-nudge"):
            return AgentResponse(text="", agent_name=self.name)
        state = self._load_state()
        if self._already_sent_today(state, "bedtime_nudge_sent_at"):
            return AgentResponse(text="", agent_name=self.name)
        msg = self._pick_message(_BEDTIME_MESSAGES)
        await self._send_to_all_chats(msg)
        state["bedtime_nudge_sent_at"] = datetime.now().isoformat()
        self._save_state(state)
        log.info("Bedtime nudge sent", event="wellbeing_bedtime")
        return AgentResponse(text=msg, agent_name=self.name)

    async def _handle_weekly(self) -> AgentResponse:
        if not self.should_notify("wellbeing-nudge"):
            return AgentResponse(text="", agent_name=self.name)
        state = self._load_state()
        weekly = state.get("weekly_stats", {})
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        week_dates = [monday + timedelta(days=i) for i in range(7)]
        total_days = min(7, (today - monday).days + 1)
        week_strs = [d.isoformat() for d in week_dates]
        routine_count = len([d for d in weekly.get("routine_days", []) if d in week_strs])
        week_label = f"{week_dates[0].strftime('%b %d')} - {week_dates[-1].strftime('%b %d')}"
        lines = [
            f"Weekly check-in ({week_label}):",
            f"Morning routine: {routine_count}/{total_days} days",
        ]
        pct = routine_count / total_days if total_days > 0 else 0
        if pct >= 0.8:
            lines.append("Strong week.")
        elif pct >= 0.6:
            lines.append("Decent week. Room to improve.")
        elif pct >= 0.4:
            lines.append("Mixed week. Tomorrow is a fresh start.")
        else:
            lines.append("Rough week. But you're aware of it. That matters.")
        msg = "\n".join(lines)
        await self._send_to_all_chats(msg)
        state["weekly_stats"] = {
            "routine_days": [],
            "streak": weekly.get("streak", 0),
        }
        self._save_state(state)
        log.info("Weekly check-in sent", event="wellbeing_weekly")
        return AgentResponse(text=msg, agent_name=self.name)

    # ── Dispatch ──────────────────────────────

    async def handle(self, event: AgentEvent) -> AgentResponse:
        if event.type == EventType.AGENT_MESSAGE:
            return await self._handle_agent_message(event)
        task = (event.data or {}).get("task", "")
        dispatch = {
            "wellbeing_morning_weekday": lambda: self._handle_morning(is_weekend=False),
            "wellbeing_morning_weekend": lambda: self._handle_morning(is_weekend=True),
            "wellbeing_followup": self._handle_followup,
            "wellbeing_evening": self._handle_evening,
            "wellbeing_bedtime": self._handle_bedtime,
            "wellbeing_weekly": self._handle_weekly,
        }
        handler = dispatch.get(task)
        if handler is None:
            log.warning("Unknown wellbeing task", event="unknown_task", task=task)
            return AgentResponse(text="", agent_name=self.name)
        return await handler()

    # ── Lifecycle ─────────────────────────────

    async def register_schedules(self, bus: "MessageBus") -> None:
        await super().register_schedules(bus)
        try:
            from core.scheduler import scheduler

            chat_id = (
                self.settings.telegram_allowed_chat_ids[0]
                if self.settings.telegram_allowed_chat_ids
                else ""
            )
            schedules = [
                ("wellbeing_morning_weekday", "0 7 * * 1-5"),
                ("wellbeing_morning_weekend", "0 8 * * 0,6"),
                ("wellbeing_followup", "30 8 * * 1-5"),
                ("wellbeing_evening", "30 19 * * *"),
                ("wellbeing_bedtime", "0 23 * * *"),
                ("wellbeing_weekly", "0 9 * * 0"),
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
