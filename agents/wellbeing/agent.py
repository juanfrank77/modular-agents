"""
agents/wellbeing/agent.py
-------------------------
WellbeingAgent — scheduled nudges with quiet-hours awareness and skill-driven
message construction.

Six cron schedules:
  Morning nudge (weekday):  0 7  * * 1-5
  Morning nudge (weekend):  0 8  * * 0,6
  Morning follow-up:        30 8 * * 1-5
  Evening wind-down:        30 19 * * *
  Bedtime:                  0 23 * * *
  Weekly check-in:          0 9  * * 0

Interactive handling:
  Handles user messages about wellbeing stats, streak, quiet-hours config,
  and general wellbeing questions — reading from state.json and preferences.md.

State is persisted to agents/wellbeing/state.json (JSON, next to this file).
Autonomy level = autonomous. No LLM required for scheduled tasks.
"""

from __future__ import annotations

import json
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents.base import BaseAgent
from core.logger import get_logger
from core.protocols import AgentEvent, AgentResponse, EventType, Message

if TYPE_CHECKING:
    from core.bus import MessageBus
    from core.skill_loader import SkillLoader

log = get_logger("wellbeing")

_STATE_FILE = Path(__file__).parent / "state.json"
_SKILLS_DIR = Path(__file__).parent / "skills"

# ── Skill names for each scheduled task ────────────────────────────────────

_SKILL_MORNING = "morning-nudge"
_SKILL_EVENING = "evening-wind-down"
_SKILL_BEDTIME = "bedtime-reminder"
_SKILL_WEEKLY = "weekly-check-in"
_SKILL_INTERACTIVE = "wellbeing-interactive"


# ── Hardcoded message pools (fallback / used by skills) ─────────────────────

_EVENING_MESSAGES = [
    "Your evening. Do something you enjoy. The work will be there tomorrow.",
    "Evening time. Step away from the screens. You've done enough today.",
    "Wind-down time. Whatever makes you happy tonight.",
    "Evening. You've earned the rest. Do something for yourself.",
]

_BEDTIME_MESSAGES = [
    "Bedtime. Sleep is the best investment. Good night.",
    "Time to wind down. Good night.",
    "Bed now = full sleep. Good night.",
    "Sleep. Tomorrow is a new day.",
]


# ─────────────────────────────────────────────────────────────────────────────
# WellbeingAgent
# ─────────────────────────────────────────────────────────────────────────────

class WellbeingAgent(BaseAgent):
    name = "wellbeing"
    description = (
        "Sends scheduled wellbeing nudges: morning, evening, bedtime, and "
        "weekly check-in. Handles interactive wellbeing queries. "
        "Respects quiet hours. No LLM required."
    )
    autonomy_level = "autonomous"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.autonomy_level = self.settings.wellbeing_agent_autonomy

    # ── Skill loader access ─────────────────────────────────────────────────

    def _load_skill(self, skill_name: str) -> str:
        """Read a SKILL.md file, return empty string if missing."""
        path = _SKILLS_DIR / f"{skill_name}.md"
        try:
            return path.read_text()
        except Exception:
            return ""

    # ── State ───────────────────────────────────────────────────────────────

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

    def _pick_cyclic(self, messages: list[str]) -> str:
        day_num = datetime.now().timetuple().tm_yday
        return messages[day_num % len(messages)]

    # ── Weather ───────────────────────────────────────────────────────────────

    def _get_weather(self) -> dict | None:
        location = getattr(self.settings, "wellbeing_location", None)
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

    # ── Scheduled task dispatch ──────────────────────────────────────────────

    async def handle(self, event: AgentEvent) -> AgentResponse:
        # Cross-agent messages
        if event.type == EventType.AGENT_MESSAGE:
            return await self._handle_agent_message(event)

        # Heartbeat — silent acknowledgment
        if event.type == EventType.HEARTBEAT_TICK:
            log.info("Heartbeat tick", event="heartbeat")
            return AgentResponse(text="HEARTBEAT_OK", agent_name=self.name)

        # Scheduled tasks route by task key
        if event.type == EventType.SCHEDULED_TASK:
            task = (event.data or {}).get("task", "")
            dispatch = {
                "wellbeing_morning_weekday": lambda: self._do_morning(event, is_weekend=False),
                "wellbeing_morning_weekend": lambda: self._do_morning(event, is_weekend=True),
                "wellbeing_followup": lambda: self._do_followup(event),
                "wellbeing_evening": lambda: self._do_evening(event),
                "wellbeing_bedtime": lambda: self._do_bedtime(event),
                "wellbeing_weekly": lambda: self._do_weekly(event),
            }
            handler = dispatch.get(task)
            if handler is None:
                log.warning("Unknown wellbeing task", event="unknown_task", task=task)
                return AgentResponse(text="", agent_name=self.name)
            return await handler()

        # Interactive user message
        return await self._handle_interactive(event)

    # ── Morning nudge ────────────────────────────────────────────────────────

    async def _do_morning(self, event: AgentEvent, is_weekend: bool) -> AgentResponse:
        if not self.should_notify("wellbeing-nudge"):
            return AgentResponse(text="", agent_name=self.name)
        state = self._load_state()
        if self._already_sent_today(state, "morning_nudge_sent_at"):
            return AgentResponse(text="", agent_name=self.name)

        weather = self._get_weather()
        activity = self._suggest_activity(weather)

        # Build the message using the skill if available
        skill = self._load_skill(_SKILL_MORNING)
        if skill and not is_weekend:
            # Use skill for weekday morning
            weather_part = ""
            if weather:
                weather_part = f"{weather['temp']}C, {weather['desc']}. "
            if weather:
                msg = f"Morning. {weather_part}Good day for {activity}."
            else:
                msg = f"Morning. Good day for {activity}."
        elif skill and is_weekend:
            if weather:
                msg = f"Morning. {weather['temp']}C, {weather['desc']}. Routine when you're ready. Enjoy the day."
            else:
                msg = "Morning. Routine when you're ready. Enjoy the day."
        else:
            # Fallback
            if is_weekend:
                if weather:
                    msg = f"Morning. {weather['temp']}C, {weather['desc']}. Routine when you're ready. Enjoy the day."
                else:
                    msg = "Morning. Routine when you're ready. Enjoy the day."
            else:
                if weather:
                    msg = f"Morning. {weather['temp']}C, {weather['desc']}. Good day for {activity}."
                else:
                    msg = f"Morning. Good day for {activity}."

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

    # ── Morning follow-up ───────────────────────────────────────────────────

    async def _do_followup(self, event: AgentEvent) -> AgentResponse:
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

    # ── Evening wind-down ───────────────────────────────────────────────────

    async def _do_evening(self, event: AgentEvent) -> AgentResponse:
        if not self.should_notify("wellbeing-nudge"):
            return AgentResponse(text="", agent_name=self.name)
        state = self._load_state()
        if self._already_sent_today(state, "evening_nudge_sent_at"):
            return AgentResponse(text="", agent_name=self.name)

        # Use skill if available, otherwise use fallback pool
        skill = self._load_skill(_SKILL_EVENING)
        msg = self._pick_cyclic(_EVENING_MESSAGES)

        await self._send_to_all_chats(msg)
        state["evening_nudge_sent_at"] = datetime.now().isoformat()
        self._save_state(state)
        log.info("Evening nudge sent", event="wellbeing_evening")
        return AgentResponse(text=msg, agent_name=self.name)

    # ── Bedtime reminder ─────────────────────────────────────────────────────

    async def _do_bedtime(self, event: AgentEvent) -> AgentResponse:
        if not self.should_notify("wellbeing-nudge"):
            return AgentResponse(text="", agent_name=self.name)
        state = self._load_state()
        if self._already_sent_today(state, "bedtime_nudge_sent_at"):
            return AgentResponse(text="", agent_name=self.name)

        skill = self._load_skill(_SKILL_BEDTIME)
        msg = self._pick_cyclic(_BEDTIME_MESSAGES)

        await self._send_to_all_chats(msg)
        state["bedtime_nudge_sent_at"] = datetime.now().isoformat()
        self._save_state(state)
        log.info("Bedtime nudge sent", event="wellbeing_bedtime")
        return AgentResponse(text=msg, agent_name=self.name)

    # ── Weekly check-in ─────────────────────────────────────────────────────

    async def _do_weekly(self, event: AgentEvent) -> AgentResponse:
        if not self.should_notify("wellbeing-nudge"):
            return AgentResponse(text="", agent_name=self.name)
        state = self._load_state()
        weekly = state.get("weekly_stats", {})
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        week_dates = [monday + timedelta(days=i) for i in range(7)]
        total_days = min(7, (today - monday).days + 1)
        week_strs = [d.isoformat() for d in week_dates]
        routine_days_list = weekly.get("routine_days", [])
        routine_count = len([d for d in routine_days_list if d in week_strs])
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

        streak = weekly.get("streak", 0)
        if streak >= 2:
            lines.append(f"{streak} weeks in a row.")

        msg = "\n".join(lines)
        await self._send_to_all_chats(msg)

        # Update streak and reset routine days
        if routine_count >= 4:
            new_streak = streak + 1
        else:
            new_streak = 0
        state["weekly_stats"] = {
            "routine_days": [],
            "streak": new_streak,
        }
        self._save_state(state)
        log.info("Weekly check-in sent", event="wellbeing_weekly")
        return AgentResponse(text=msg, agent_name=self.name)

    # ── Interactive user messages ───────────────────────────────────────────

    async def _handle_interactive(self, event: AgentEvent) -> AgentResponse:
        """
        Handle non-scheduled user messages about wellbeing.
        Reads state.json and preferences.md to answer questions about
        routine stats, streaks, and settings.
        """
        if not self._is_authorized(event.chat_id):
            return AgentResponse(text="Unauthorized.", agent_name=self.name, success=False)

        text = event.text.lower().strip()

        # Save the inbound message
        session_id = await self.storage.get_or_create_session(event.chat_id, self.name)
        await self.storage.save_message(session_id, "user", event.text, self.name)

        response_text = await self._build_interactive_response(text, event.chat_id)

        if response_text:
            await self.notifier.send(event.chat_id, response_text)
            await self.storage.save_message(session_id, "assistant", response_text, self.name)

        return AgentResponse(text=response_text, agent_name=self.name)

    async def _build_interactive_response(self, text: str, chat_id: str) -> str:
        """
        Build a response to a user's wellbeing question.
        No LLM — uses state data and static rules only.
        """
        state = self._load_state()

        # Stats queries
        if any(kw in text for kw in ["routine", "streak", "morning routine", "check-in", "how am i", "how did i"]):
            return self._respond_stats(state, text)

        # When was the last nudge?
        if any(kw in text for kw in ["last evening", "last morning", "last bedtime", "when did"]):
            return self._respond_last_nudge(state, text)

        # Quiet hours / settings
        if any(kw in text for kw in ["quiet hours", "settings", "timezone", "preference"]):
            return self._respond_settings(text)

        # About the wellbeing agent
        if any(kw in text for kw in ["what can you", "what do you", "help", "capabilities"]):
            return (
                "I send scheduled wellbeing nudges: morning, evening, bedtime, "
                "and a weekly check-in. I also track your morning routine stats and streak.\n"
                "Ask me about your routine, streak, or last nudges."
            )

        # Deflection for out-of-scope requests
        if any(kw in text for kw in ["send now", "skip quiet", "immediate nudge"]):
            return (
                "I can't bypass quiet hours from chat — that would defeat the purpose. "
                "Check your settings in memory/context/preferences.md."
            )

        # Fallback: empty (don't respond to unrelated messages)
        return ""

    def _respond_stats(self, state: dict, text: str) -> str:
        """Respond with routine stats and streak."""
        weekly = state.get("weekly_stats", {})
        routine_days = weekly.get("routine_days", [])
        streak = weekly.get("streak", 0)

        today = date.today()
        monday = today - timedelta(days=today.weekday())
        week_dates = [monday + timedelta(days=i) for i in range(7)]
        week_strs = [d.isoformat() for d in week_dates]
        this_week = len([d for d in routine_days if d in week_strs])

        parts = [f"Morning routine: {this_week}/7 days this week."]
        if streak >= 2:
            parts.append(f"Streak: {streak} weeks.")
        return " ".join(parts)

    def _respond_last_nudge(self, state: dict, text: str) -> str:
        """Respond with the timestamp of the last sent nudge."""
        keys = []
        if "evening" in text:
            keys = ["evening_nudge_sent_at"]
        elif "morning" in text:
            keys = ["morning_nudge_sent_at"]
        elif "bedtime" in text:
            keys = ["bedtime_nudge_sent_at"]

        for key in keys:
            ts = state.get(key)
            if ts:
                try:
                    dt = datetime.fromisoformat(ts)
                    return f"Last {key.replace('_sent_at', '').replace('_', ' ')}: {dt.strftime('%b %d at %H:%M')}."
                except Exception:
                    pass
        return "No record of that nudge being sent recently."

    def _respond_settings(self, text: str) -> str:
        """Deflect settings questions — user should edit preferences.md."""
        return (
            "I can't change quiet-hours or preferences from here. "
            "Edit memory/context/preferences.md directly or tell me what "
            "you need and I can update it there."
        )

    # ── Delivery ────────────────────────────────────────────────────────────

    async def _send_to_all_chats(self, msg: str) -> None:
        for chat_id in self.settings.telegram_allowed_chat_ids:
            await self.notifier.send(chat_id, msg)

    # ── Lifecycle ────────────────────────────────────────────────────────────

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
                ("wellbeing_morning_weekday", "0 6 * * 1-5"),
                ("wellbeing_morning_weekend", "0 7 * * 0,6"),
                ("wellbeing_followup", "00 8 * * 1-5"),
                ("wellbeing_evening", "30 20 * * *"),
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
