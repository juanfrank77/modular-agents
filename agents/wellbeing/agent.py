"""
agents/wellbeing/agent.py
-------------------------
WellbeingAgent — scheduled nudges with quiet-hours awareness and skill-driven
message construction.

Six cron schedules (defaults below; morning/follow-up/bedtime are derived from the
WELLBEING_WAKE_TIME / WELLBEING_BEDTIME settings at registration time):
  Morning nudge (weekday):  0 7  * * 1-5
  Morning nudge (weekend):  0 8  * * 0,6
  Morning follow-up:        30 8 * * 1-5   (wake + 1h30m, weekdays only)
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

import asyncio
import json
import random
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents.base import BaseAgent
from core.logger import get_logger
from core.protocols import AgentEvent, AgentResponse, EventType
from core.timezone import as_user_timezone, now_in_user_timezone

if TYPE_CHECKING:
    from core.bus import MessageBus

log = get_logger("wellbeing")

_STATE_FILE = Path(__file__).parent / "state.json"
_SKILLS_DIR = Path(__file__).parent / "skills"


# ── Fallback message pools (used when skill files are missing / unreadable) ───

_WEEKDAY_FALLBACK = [
    "Morning. [temp]C, [condition]. Good day for [run/yoga].",
    "Morning. [temp]C, [condition]. Time to move.",
    "Morning. [condition]. Start as you mean to continue.",
]

_WEEKEND_FALLBACK = [
    "Morning. [temp]C, [condition]. Routine when you're ready. Enjoy the day.",
    "Morning. [condition]. Enjoy the day.",
    "Morning. No rush today. [temp]C, [condition].",
]

_WEATHER_FALLBACK = [
    "Morning. Good day for [run/yoga].",
    "Morning. Routine when you're ready.",
]

_EVENING_FALLBACK = [
    "Your evening. Do something you enjoy. The work will be there tomorrow.",
    "Evening time. Step away from the screens. You've done enough today.",
    "Wind-down time. Whatever makes you happy tonight.",
    "Evening. You've earned the rest. Do something for yourself.",
]

_BEDTIME_FALLBACK = [
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
    SCHEDULES = [
        ("wellbeing_morning_weekday", "0 7 * * 1-5"),
        ("wellbeing_morning_weekend", "0 8 * * 0,6"),
        ("wellbeing_followup", "30 8 * * 1-5"),
        ("wellbeing_evening", "30 19 * * *"),
        ("wellbeing_bedtime", "0 23 * * *"),
        ("wellbeing_weekly", "0 9 * * 0"),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.autonomy_level = self.settings.wellbeing_agent_autonomy

    # ── Schedule registration (uses wake-time / bedtime settings) ────────────

    @staticmethod
    def _parse_hhmm(value: str, default_hour: int, default_minute: int) -> tuple[int, int]:
        try:
            hour, minute = value.split(":")
            return int(hour), int(minute)
        except Exception:
            return default_hour, default_minute

    @staticmethod
    def _shift_hhmm(value: tuple[int, int], hours: int = 0, minutes: int = 0) -> tuple[int, int]:
        from datetime import time as _time, timedelta as _timedelta
        dt = datetime.combine(datetime.today(), _time(value[0], value[1]))
        dt += _timedelta(hours=hours, minutes=minutes)
        return dt.hour, dt.minute

    async def register_schedules(self, bus: "MessageBus") -> None:
        """Register cron schedules, deriving morning/bedtime/follow-up from user settings."""
        wake = self._parse_hhmm(self.settings.wellbeing_wake_time, 7, 0)
        weekend_wake = self._shift_hhmm(wake, hours=1)
        followup = self._shift_hhmm(wake, hours=1, minutes=30)
        bedtime = self._parse_hhmm(self.settings.wellbeing_bedtime, 23, 0)
        # Instance-level override keeps the class attribute intact for other agents.
        self.SCHEDULES = [
            ("wellbeing_morning_weekday", f"{wake[1]} {wake[0]} * * 1-5"),
            ("wellbeing_morning_weekend", f"{weekend_wake[1]} {weekend_wake[0]} * * 0,6"),
            ("wellbeing_followup", f"{followup[1]} {followup[0]} * * 1-5"),
            ("wellbeing_evening", "30 19 * * *"),
            ("wellbeing_bedtime", f"{bedtime[1]} {bedtime[0]} * * *"),
            ("wellbeing_weekly", "0 9 * * 0"),
        ]
        await super().register_schedules(bus)

    # ── State / skills (async I/O) ───────────────────────────────────────────

    async def _load_state(self) -> dict:
        try:
            text = await asyncio.to_thread(_STATE_FILE.read_text)
            return json.loads(text)
        except Exception:
            return {}

    async def _save_state(self, state: dict) -> None:
        await asyncio.to_thread(_STATE_FILE.write_text, json.dumps(state, indent=2))

    async def _load_skill_text(self, name: str) -> str | None:
        path = _SKILLS_DIR / f"{name}.md"
        if not path.exists():
            return None
        try:
            return await asyncio.to_thread(path.read_text, encoding="utf-8")
        except Exception:
            return None

    def _already_sent_today(self, state: dict, key: str) -> bool:
        sent_at = state.get(key)
        if not sent_at:
            return False
        try:
            parsed = as_user_timezone(datetime.fromisoformat(sent_at), self.settings)
            return parsed.date() == now_in_user_timezone(self.settings).date()
        except Exception:
            return False

    def _pick_cyclic(self, messages: list[str]) -> str:
        day_num = now_in_user_timezone(self.settings).timetuple().tm_yday
        return messages[day_num % len(messages)]

    def _pick_message(self, messages: list[str]) -> str:
        return random.choice(messages)

    @staticmethod
    def _header_matches(line: str, header: str) -> bool:
        """Match a header whether it's a plain-text label or a markdown heading."""
        normalized = line.lstrip("# ").strip()
        return normalized.lower().startswith(header.lower())

    @staticmethod
    def _parse_bullet_pool(content: str, header: str) -> list[str]:
        """Extract quoted bullet messages under a markdown header."""
        lines = content.splitlines()
        in_pool = False
        pool: list[str] = []
        for raw_line in lines:
            line = raw_line.strip()
            if WellbeingAgent._header_matches(line, header):
                in_pool = True
                continue
            if in_pool:
                if not line or line.startswith("#") or line.startswith("**"):
                    break
                if line.startswith("- "):
                    text = line[2:].strip().strip('"')
                    if text:
                        pool.append(text)
        return pool

    @staticmethod
    def _parse_numbered_pool(content: str, header: str = "Message Construction") -> list[str]:
        """Extract quoted numbered messages from a section."""
        lines = content.splitlines()
        in_section = False
        pool: list[str] = []
        for raw_line in lines:
            line = raw_line.strip()
            if WellbeingAgent._header_matches(line, header):
                in_section = True
                continue
            if in_section:
                if not line or line.startswith("#"):
                    break
                match = re.match(r'^\d+\.\s*"(.*)"$', line)
                if match:
                    pool.append(match.group(1))
        return pool

    @staticmethod
    def _topic_to_keywords(topic: str) -> list[str]:
        t = topic.lower()
        if "immediate nudge" in t or "send now" in t:
            return ["send now", "skip quiet", "immediate nudge"]
        if "emotional" in t or "support" in t:
            return [
                "emotional support",
                "therapist",
                "depressed",
                "anxious",
                "overwhelmed",
                "feeling overwhelmed",
            ]
        if "medical" in t or "health professional" in t or "doctor" in t:
            return ["doctor", "medical", "health professional", "sick", "medication"]
        return []

    def _parse_deflection_rules(self, content: str) -> list[tuple[list[str], str]]:
        """Parse the 'Should deflect' section from wellbeing-interactive.md."""
        rules: list[tuple[list[str], str]] = []
        lines = content.splitlines()
        in_section = False
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.lower().startswith("### should deflect"):
                in_section = True
                i += 1
                continue
            if not in_section:
                i += 1
                continue
            if line.startswith("#"):
                break
            if line.startswith("- "):
                body = line[2:]
                if "→" in body:
                    topic, response = body.split("→", 1)
                else:
                    i += 1
                    continue
                topic = topic.strip()
                response = response.strip().strip('"').strip()
                j = i + 1
                while j < len(lines):
                    next_line = lines[j].strip()
                    if not next_line or next_line.startswith("- ") or next_line.startswith("#"):
                        break
                    response += " " + next_line.strip().strip('"').strip()
                    j += 1
                keywords = self._topic_to_keywords(topic)
                if keywords:
                    rules.append((keywords, " ".join(response.split())))
                i = j
                continue
            i += 1
        return rules

    async def _has_user_reply_today(self, chat_id: str) -> bool:
        """Check whether the user has already sent a message to this agent today."""
        try:
            session_id = await self.storage.get_or_create_session(chat_id, self.name)
            messages = await self.storage.get_session_messages(session_id, limit=50)
        except Exception:
            return False
        today = now_in_user_timezone(self.settings).date()
        for msg in messages:
            if msg.role == "user" and msg.timestamp.date() == today:
                return True
        return False

    # ── Weather ───────────────────────────────────────────────────────────────

    async def _get_weather(self) -> dict | None:
        location = getattr(self.settings, "wellbeing_location", None)
        if not location:
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                "curl",
                "-s",
                "--max-time",
                "5",
                f"wttr.in/{location}?format=j1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                return None
            data = json.loads(stdout.decode("utf-8"))
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

    @staticmethod
    def _render_morning_template(template: str, weather: dict | None, activity: str) -> str:
        msg = template.replace("[run/yoga]", activity)
        if weather:
            msg = msg.replace("[temp]C", f"{weather['temp']}C")
            msg = msg.replace("[condition]", weather["desc"])
        return msg

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

    async def _build_morning_message(self, is_weekend: bool) -> str:
        weather = await self._get_weather()
        activity = self._suggest_activity(weather)
        skill = await self._load_skill_text("morning-nudge")
        if skill:
            pool_header = "Weekend pool:" if is_weekend else "Weekday pool:"
            pool = self._parse_bullet_pool(skill, pool_header)
            fallback = self._parse_bullet_pool(skill, "Weather fallback")
        else:
            pool = []
            fallback = []

        if not pool:
            pool = _WEEKEND_FALLBACK if is_weekend else _WEEKDAY_FALLBACK
        if not fallback:
            fallback = _WEATHER_FALLBACK

        # If weather is missing, drop templates that reference [condition]
        if weather:
            candidates = pool
        else:
            candidates = [t for t in pool if "[condition]" not in t]
            if not candidates:
                candidates = fallback

        if candidates:
            template = self._pick_message(candidates)
            return self._render_morning_template(template, weather, activity)

        return (
            f"Morning. Good day for {activity}."
            if not is_weekend
            else "Morning. Routine when you're ready. Enjoy the day."
        )

    async def _do_morning(self, event: AgentEvent, is_weekend: bool) -> AgentResponse:
        if not self.should_notify("wellbeing-nudge"):
            return AgentResponse(text="", agent_name=self.name)
        state = await self._load_state()
        if self._already_sent_today(state, "morning_nudge_sent_at"):
            return AgentResponse(text="", agent_name=self.name)

        msg = await self._build_morning_message(is_weekend)

        await self._send_to_chat(event.chat_id, msg)
        state["morning_nudge_sent_at"] = now_in_user_timezone(self.settings).isoformat()
        weekly = state.setdefault("weekly_stats", {})
        routine_days = weekly.setdefault("routine_days", [])
        today_str = now_in_user_timezone(self.settings).date().isoformat()
        if today_str not in routine_days:
            routine_days.append(today_str)
        await self._save_state(state)
        log.info("Morning nudge sent", event="wellbeing_morning", is_weekend=is_weekend)
        return AgentResponse(text=msg, agent_name=self.name)

    # ── Morning follow-up ───────────────────────────────────────────────────

    async def _do_followup(self, event: AgentEvent) -> AgentResponse:
        if now_in_user_timezone(self.settings).weekday() >= 5:
            return AgentResponse(text="", agent_name=self.name)
        if not self.should_notify("wellbeing-nudge"):
            return AgentResponse(text="", agent_name=self.name)
        state = await self._load_state()
        if self._already_sent_today(state, "morning_followup_sent_at"):
            return AgentResponse(text="", agent_name=self.name)
        if await self._has_user_reply_today(event.chat_id):
            log.info("Morning follow-up skipped: user already replied", event="wellbeing_followup_skipped")
            return AgentResponse(text="", agent_name=self.name)

        msg = "Time to move."
        await self._send_to_chat(event.chat_id, msg)
        state["morning_followup_sent_at"] = now_in_user_timezone(self.settings).isoformat()
        await self._save_state(state)
        log.info("Followup nudge sent", event="wellbeing_followup")
        return AgentResponse(text=msg, agent_name=self.name)

    # ── Evening wind-down ───────────────────────────────────────────────────

    async def _do_evening(self, event: AgentEvent) -> AgentResponse:
        if not self.should_notify("wellbeing-nudge"):
            return AgentResponse(text="", agent_name=self.name)
        state = await self._load_state()
        if self._already_sent_today(state, "evening_nudge_sent_at"):
            return AgentResponse(text="", agent_name=self.name)

        skill = await self._load_skill_text("evening-wind-down")
        pool = self._parse_numbered_pool(skill) if skill else []
        if not pool:
            pool = _EVENING_FALLBACK

        msg = self._pick_cyclic(pool)

        await self._send_to_chat(event.chat_id, msg)
        state["evening_nudge_sent_at"] = now_in_user_timezone(self.settings).isoformat()
        await self._save_state(state)
        log.info("Evening nudge sent", event="wellbeing_evening")
        return AgentResponse(text=msg, agent_name=self.name)

    # ── Bedtime reminder ─────────────────────────────────────────────────────

    async def _do_bedtime(self, event: AgentEvent) -> AgentResponse:
        if not self.should_notify("wellbeing-nudge"):
            return AgentResponse(text="", agent_name=self.name)
        state = await self._load_state()
        if self._already_sent_today(state, "bedtime_nudge_sent_at"):
            return AgentResponse(text="", agent_name=self.name)

        skill = await self._load_skill_text("bedtime-reminder")
        pool = self._parse_numbered_pool(skill) if skill else []
        if not pool:
            pool = _BEDTIME_FALLBACK

        msg = self._pick_cyclic(pool)

        await self._send_to_chat(event.chat_id, msg)
        state["bedtime_nudge_sent_at"] = now_in_user_timezone(self.settings).isoformat()
        await self._save_state(state)
        log.info("Bedtime nudge sent", event="wellbeing_bedtime")
        return AgentResponse(text=msg, agent_name=self.name)

    # ── Weekly check-in ─────────────────────────────────────────────────────

    async def _do_weekly(self, event: AgentEvent) -> AgentResponse:
        if not self.should_notify("wellbeing-nudge"):
            return AgentResponse(text="", agent_name=self.name)
        state = await self._load_state()
        weekly = state.get("weekly_stats", {})
        today = now_in_user_timezone(self.settings).date()
        monday = today - timedelta(days=today.weekday())
        week_dates = [monday + timedelta(days=i) for i in range(7)]
        total_days = 7
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
        await self._send_to_chat(event.chat_id, msg)

        # Update streak and reset routine days
        if routine_count >= 4:
            new_streak = streak + 1
        else:
            new_streak = 0
        state["weekly_stats"] = {
            "routine_days": [],
            "streak": new_streak,
        }
        await self._save_state(state)
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
        state = await self._load_state()
        skill = await self._load_skill_text("wellbeing-interactive")
        deflection_rules = self._parse_deflection_rules(skill) if skill else []

        # Stats queries
        if any(
            kw in text
            for kw in [
                "routine",
                "streak",
                "morning routine",
                "check-in",
                "how am i",
                "how did i",
            ]
        ):
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

        # Skill-defined deflection rules
        for keywords, response in deflection_rules:
            if any(kw in text for kw in keywords):
                return response

        # Fallback: empty (don't respond to unrelated messages)
        return ""

    def _respond_stats(self, state: dict, text: str) -> str:
        """Respond with routine stats and streak."""
        weekly = state.get("weekly_stats", {})
        routine_days = weekly.get("routine_days", [])
        streak = weekly.get("streak", 0)

        today = now_in_user_timezone(self.settings).date()
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
                    dt = as_user_timezone(datetime.fromisoformat(ts), self.settings)
                    return f"Last {key.replace('_sent_at', '').replace('_', ' ')}: {dt.strftime('%b %d at %H:%M')}."
                except Exception:
                    pass
        return "No record of that nudge being sent recently."

    def _respond_settings(self, text: str) -> str:
        """Deflect settings questions — user should edit preferences.md."""
        return (
            "I can't change quiet hours or preferences from here. "
            "Edit memory/context/preferences.md directly."
        )

    # ── Delivery ────────────────────────────────────────────────────────────

    async def _send_to_chat(self, chat_id: str, msg: str) -> None:
        await self.notifier.send(chat_id, msg)

    async def health_check(self) -> bool:
        return True
