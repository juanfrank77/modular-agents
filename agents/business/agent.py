"""
agents/business/agent.py
------------------------
The Business Agent. Handles productivity, calendar, email, and
scheduling tasks. Runs in supervised autonomy mode — low-risk reads
are automatic, high-risk actions (sending email, modifying calendar)
require inline approval via Telegram.

Scheduled jobs (registered at startup):
  - Morning briefing: weekdays at 7am
  - Weekly review:    Fridays at 5pm

Lifecycle per message:
  1. Authorize chat
  2. Load relevant skills via SkillLoader
  3. Load markdown context + compacted history via Memory
  4. Build system prompt
  5. Call LLM
  6. Check safety if action is proposed
  7. Execute / respond
  8. Save to memory
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from core.logger import get_logger
from core.protocols import AgentEvent, AgentResponse, EventType, Message
from core.safety import ActionType as SafetyActionType
from core.budget import ActionType
from agents.base import BaseAgent

if TYPE_CHECKING:
    from core.bus import MessageBus

log = get_logger("business")

# Path to this agent's skills folder
_SKILLS_DIR = Path(__file__).parent / "skills"

# System prompt template — skills and context are injected at runtime
_SYSTEM_TEMPLATE = """\
You are a focused, efficient personal business assistant. Your job is to help
with productivity, calendar management, email triage, and project tracking.

You are direct and concise. You never pad responses. You ask at most one
clarifying question at a time.

Autonomy level: supervised
- You may read, search, and draft freely.
- Before sending any email, modifying any calendar event, or making any
  external API write call, you MUST describe the action and wait for approval.
- Format proposed actions as:
    ACTION: <type> | <description>
  Example: ACTION: SEND_EMAIL | Reply to Alice confirming Thursday 3pm

{context}

{skills}
"""


class BusinessAgent(BaseAgent):
    name = "business"
    description = (
        "Handles business productivity: calendar, email, task management, "
        "morning briefings, weekly reviews, and project tracking."
    )
    autonomy_level = "supervised"

    # ── Main handler ──────────────────────────

    async def handle(self, event: AgentEvent) -> AgentResponse:
        # Auth check
        if not self._is_authorized(event.chat_id):
            log.warning(
                "Unauthorised access", event="auth_denied", chat_id=event.chat_id
            )
            return AgentResponse(
                text="Unauthorized.", agent_name=self.name, success=False
            )

        # Cross-agent messages handling
        if event.type == EventType.AGENT_MESSAGE:
            return await self._handle_agent_message(event)
        
        # Heartbeat: just confirm alive
        if event.type == EventType.HEARTBEAT_TICK:
            log.info("Heartbeat", event="heartbeat")
            return AgentResponse(text="HEARTBEAT_OK", agent_name=self.name)

        # Scheduled tasks route to their own handlers
        if event.type == EventType.SCHEDULED_TASK:
            return await self._handle_scheduled(event)

        # Standard user message
        return await self._handle_message(event)

    # ── Message handling ──────────────────────

    async def _handle_message(self, event: AgentEvent) -> AgentResponse:
        assert self.memory is not None, "memory required"
        assert self.llm is not None, "llm required"
        session_id = await self.storage.get_or_create_session(event.chat_id, self.name)

        # Save inbound message
        await self.memory.save_message(session_id, "user", event.text, self.name)

        # Build prompt context
        system_prompt = await self._build_system_prompt(event.text)

        # Get compacted history
        _, history = await self.memory.build_context(
            session_id, self.name, task=event.text
        )

        # Append current message to history for the LLM call
        messages = history + [Message(role="user", content=event.text)]

        # LLM call
        with log.timer() as t:
            response_text = await self.llm.complete(
                messages=messages,
                system=system_prompt,
            )
        log.info("LLM responded", event="llm_done", duration_ms=t.ms)

        # Check if the LLM is proposing an action that needs approval
        response_text = await self._handle_action_proposal(event.chat_id, response_text)

        # Save response
        await self.memory.save_message(
            session_id, "assistant", response_text, self.name
        )

        return await self.reply(event, response_text)

    # ── Action proposal interception ──────────

    async def _handle_action_proposal(self, chat_id: str, response_text: str) -> str:
        """
        If the LLM response contains an ACTION: line, intercept it,
        run it through the safety gate, and either execute or cancel.
        """
        assert self.safety is not None, "safety required"
        if "ACTION:" not in response_text:
            return response_text

        lines = response_text.splitlines()
        action_lines = [line for line in lines if line.strip().startswith("ACTION:")]

        for action_line in action_lines:
            # Parse: ACTION: <TYPE> | <description>
            parts = action_line.replace("ACTION:", "").strip().split("|", 1)
            action_type_str = parts[0].strip().upper() if parts else ""
            description = parts[1].strip() if len(parts) > 1 else action_line

            action_type = _parse_action_type(action_type_str)

            allowed = await self.safety.check_action(
                chat_id=chat_id,
                action_type=action_type,
                autonomy_level=self.autonomy_level,
                description=description,
            )

            if not allowed:
                # Replace the action line with a cancellation notice
                response_text = response_text.replace(
                    action_line, f"⚠️ Action cancelled: _{description}_"
                )
                log.info(
                    "Action denied",
                    event="action_denied",
                    action=action_type_str,
                    description=description,
                )

        return response_text

    # ── Scheduled task handlers ───────────────

    async def _handle_scheduled(self, event: AgentEvent) -> AgentResponse:
        task = event.data.get("task")
        if task == "morning_briefing":
            return await self._morning_briefing(event)
        if task == "weekly_review":
            return await self._weekly_review(event)
        log.warning("Unknown scheduled task", event="unknown_task", task=task)
        return AgentResponse(text="Unknown task.", agent_name=self.name, success=False)

    async def _morning_briefing(self, event: AgentEvent) -> AgentResponse:
        """Generate and send the daily morning briefing."""
        assert self.memory is not None, "memory required"
        assert self.llm is not None, "llm required"
        log.info("Running morning briefing", event="morning_briefing")

        # Load the morning-briefing skill specifically
        skill_content = ""
        if self.skill_loader:
            skills = await self.skill_loader.find_relevant(
                "morning briefing daily summary", str(_SKILLS_DIR), max_skills=1
            )
            skill_content = "\n\n".join(skills)

        context = await self.memory.get_context("preferences")
        personal = await self.memory.get_context("personal")
        projects = await self.memory.get_context("projects")

        system = _SYSTEM_TEMPLATE.format(
            context=f"## User Context\n{personal}\n\n## Preferences\n{context}",
            skills=f"## Active Skill\n{skill_content}" if skill_content else "",
        )

        prompt = (
            f"Generate my morning briefing for today. "
            f"Check my active projects and priorities:\n\n{projects}"
        )

        briefing = await self.llm.complete(
            messages=[Message(role="user", content=prompt)],
            system=system,
        )

        await self.notifier.send(
            event.chat_id,
            f"🌅 *Morning Briefing*\n\n{briefing}",
            action_type=ActionType.PROACTIVE,
            agent_name=self.name,
        )
        log.info("Morning briefing sent", event="briefing_sent")
        return AgentResponse(text=briefing, agent_name=self.name)

    async def _weekly_review(self, event: AgentEvent) -> AgentResponse:
        """Generate and send the weekly review."""
        assert self.memory is not None, "memory required"
        assert self.llm is not None, "llm required"
        log.info("Running weekly review", event="weekly_review")

        context = await self.memory.get_context("preferences")
        projects = await self.memory.get_context("projects")

        system = _SYSTEM_TEMPLATE.format(
            context=f"## Preferences\n{context}",
            skills="",
        )

        prompt = (
            "Generate my weekly review. Summarise progress on active projects, "
            "identify any blockers, and suggest priorities for next week. "
            f"Active projects:\n\n{projects}"
        )

        review = await self.llm.complete(
            messages=[Message(role="user", content=prompt)],
            system=system,
        )

        await self.notifier.send(
            event.chat_id,
            f"📋 *Weekly Review*\n\n{review}",
            action_type=ActionType.PROACTIVE,
            agent_name=self.name,
        )
        log.info("Weekly review sent", event="review_sent")
        return AgentResponse(text=review, agent_name=self.name)

    # ── System prompt builder ─────────────────

    async def _build_system_prompt(self, task: str) -> str:
        """Inject relevant skills and markdown context into the system prompt."""
        assert self.memory is not None, "memory required"
        # Load relevant skills
        skill_content = ""
        if self.skill_loader:
            skills = await self.skill_loader.find_relevant(
                task, str(_SKILLS_DIR), max_skills=3
            )
            if skills:
                skill_content = "## Relevant Skills\n\n" + "\n\n---\n\n".join(skills)

        # Load markdown context
        markdown_context, _ = await self.memory.build_context(
            "_unused_", self.name, task=task
        )

        return _SYSTEM_TEMPLATE.format(
            context=f"## User Context\n{markdown_context}" if markdown_context else "",
            skills=skill_content,
        )

    # ── Lifecycle ─────────────────────────────

    async def register_schedules(self, bus: "MessageBus") -> None:
        """
        Register cron jobs. Called once at startup by main.py.

        Assumes a scheduler is accessible via bus or imported directly.
        Adjust the import path to match your scheduler.py implementation.
        """
        await super().register_schedules(bus)
        try:
            from core.scheduler import scheduler

            # Morning briefing — weekdays at 7am
            scheduler.add_cron_job(
                cron="0 7 * * 1-5",
                event=AgentEvent(
                    type=EventType.SCHEDULED_TASK,
                    agent_name=self.name,
                    chat_id=self.settings.telegram_allowed_chat_ids[0]
                    if self.settings.telegram_allowed_chat_ids else "",
                    data={"task": "morning_briefing"},
                ),
                bus=bus,
            )

            # Weekly review — Fridays at 5pm
            scheduler.add_cron_job(
                cron="0 17 * * 5",
                event=AgentEvent(
                    type=EventType.SCHEDULED_TASK,
                    agent_name=self.name,
                    chat_id=self.settings.telegram_allowed_chat_ids[0]
                    if self.settings.telegram_allowed_chat_ids else "",
                    data={"task": "weekly_review"},
                ),
                bus=bus,
            )

            log.info(
                "Schedules registered", event="schedules_registered", agent=self.name
            )

        except (ImportError, AttributeError) as e:
            log.warning(
                "Could not register schedules — check scheduler.py interface",
                event="schedule_error",
                error=str(e),
            )

    async def health_check(self) -> bool:
        try:
            assert self.llm is not None, "LLM not injected"
            assert self.memory is not None, "Memory not injected"
            assert self.safety is not None, "Safety not injected"
            await self.storage.search_history("_health_", agent=self.name, limit=1)
            return True
        except Exception as e:
            log.error("Health check failed", event="health_check_error", error=str(e))
            return False


# ── Helpers ───────────────────────────────────

def _parse_action_type(raw: str) -> SafetyActionType:
    mapping = {
        "SEND_EMAIL": SafetyActionType.WRITE_HIGH,
        "SEND_MESSAGE": SafetyActionType.WRITE_HIGH,
        "CALENDAR_WRITE": SafetyActionType.WRITE_HIGH,
        "CALENDAR_DELETE": SafetyActionType.DESTRUCTIVE,
        "DRAFT": SafetyActionType.WRITE_LOW,
        "READ": SafetyActionType.READ,
        "SEARCH": SafetyActionType.READ,
        "DELETE": SafetyActionType.DESTRUCTIVE,
        "EXECUTE": SafetyActionType.EXECUTE,
    }
    return mapping.get(raw, SafetyActionType.WRITE_HIGH)  # default to high for unknown types
