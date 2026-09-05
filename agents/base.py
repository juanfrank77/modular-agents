"""
agents/base.py
--------------
The BaseAgent abstract base class. Every domain agent inherits this.
The bus only ever calls methods defined here — no direct coupling to
concrete agent implementations.

To add a new agent:
  1. Create agents/myagent/agent.py
  2. Subclass BaseAgent
  3. Implement handle(), health_check()
  4. Optionally define SCHEDULES = [("task_name", "cron_expr"), ...]
  5. Register with the bus in main.py (or use auto-discovery when implemented)
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from core.logger import get_logger
from datetime import datetime
from core.protocols import (
    AgentEvent,
    AgentProfile,
    AgentResponse,
    EventType,
    MemoryStore,
    Message,
)

if TYPE_CHECKING:
    from core.bus import MessageBus
    from core.config import Settings
    from core.protocols import LLMProvider
    from core.protocols import Notifier
    from core.safety import Safety
    from core.skill_loader import SkillLoader
    from core.state_store import StateStore
    from core.storage import Storage

log = get_logger("base")

_PLAN_PROMPT = (
    "You are an AI assistant that creates step-by-step plans for user requests. "
    "Given a user message, output a clear, numbered plan of actions you "
    "intend to perform. Do not execute anything yet."
)


class BaseAgent(ABC):
    # Every subclass must declare these at class level
    name: str  # unique identifier, e.g. "business"
    description: str  # used by bus for routing decisions
    autonomy_level: str  # "read_only" | "supervised" | "autonomous"
    routable: bool = True  # False = never picked by the intent classifier (e.g. echo)
    emoji: str = "🤖"  # Display emoji for prominent identification in messages
    SCHEDULES: list[tuple[str, str]] = []  # [(task_name, cron_expr), ...]

    def __init__(
        self,
        settings: "Settings",
        storage: "Storage",
        notifier: "Notifier",
        llm: "LLMProvider | None" = None,
        memory: "MemoryStore | None" = None,
        safety: "Safety | None" = None,
        skill_loader: "SkillLoader | None" = None,
        bus: "MessageBus | None" = None,
        state_store: "StateStore | None" = None,
    ) -> None:
        self.settings = settings
        self.model: str = getattr(settings, f"{self.name}_agent_model", "")
        self.storage = storage
        self.notifier = notifier
        self.llm = llm
        self.memory = memory
        self.safety = safety
        self.skill_loader = skill_loader
        self.bus = bus
        self._state_store = state_store
        self._profile: "AgentProfile | None" = None
        # Per-chat set so toggling plan mode for one user doesn't affect others.
        self._plan_mode_chats: set[str] = set()

    def is_plan_mode(self, chat_id: str) -> bool:
        return chat_id in self._plan_mode_chats

    def toggle_plan_mode(self, chat_id: str) -> bool:
        """Toggle plan mode for this chat. Returns the new state (True=ON)."""
        if chat_id in self._plan_mode_chats:
            self._plan_mode_chats.discard(chat_id)
            return False
        else:
            self._plan_mode_chats.add(chat_id)
            return True

    @abstractmethod
    async def handle(self, event: AgentEvent) -> AgentResponse:
        """
        Process an incoming event and return a response.
        Called by dispatch() for every event routed to this agent.
        """

    async def dispatch(self, event: AgentEvent) -> AgentResponse:
        """
        Entry point called by the bus. Delegates to _run_with_plan when plan_mode
        is active, otherwise calls handle() directly.
        """
        if self.is_plan_mode(event.chat_id):
            return await self._run_with_plan(event)
        return await self.handle(event)

    async def _run_with_plan(self, event: AgentEvent) -> AgentResponse:
        """
        Two-phase plan-then-execute flow.

        Phase 1: Ask the LLM to produce a numbered plan without executing anything.
        Phase 2: Show the plan to the user with Approve/Deny buttons and, if approved,
                 call handle(event); if denied, return a cancellation message.
        """
        # If no LLM is wired up, skip the planning phase entirely
        if self.llm is None:
            return await self.handle(event)

        # Build system prompt: include agent's identity + plan instruction
        # Agents can override _system_prompt class attribute for identity/skills
        agent_system = getattr(self, "_system_prompt", "") or ""
        combined_system = _PLAN_PROMPT + ("\n\n" + agent_system if agent_system else "")

        # Phase 1 — generate the plan
        llm_result = await self.llm.complete(
            messages=[Message(role="user", content=event.text)],
            system=combined_system,
            max_tokens=512,
            model=self.resolve_model(event.chat_id),
        )
        plan_text = llm_result.text

        # Guard: truncate plan text to fit Telegram's 4096-char limit
        MAX_TELEGRAM = 3800  # leave room for "Approval Required\n\n" prefix
        if len(plan_text) > MAX_TELEGRAM:
            plan_text = plan_text[:MAX_TELEGRAM] + "…"

        # Phase 2 — request user approval (requires safety.gate)
        if not self.safety:
            # No safety component: just run without approval
            log.warning(
                "Plan mode active but no safety instance — executing without approval",
                event="plan_mode_no_safety",
                agent=self.name,
            )
            return await self.handle(event)

        approved = await self.safety.gate.request_approval(
            chat_id=event.chat_id,
            description=plan_text,
            action_type=None,
        )

        if approved:
            return await self.handle(event)
        return AgentResponse(
            text="Action cancelled. Plan was not approved.",
            agent_name=self.name,
            success=False,
        )

    async def register_schedules(self, bus: "MessageBus") -> None:
        """
        Register cron jobs at startup. Called once by main.py during initialisation.
        
        Subclasses define SCHEDULES = [(task_name, cron_expr), ...] as a class attribute.
        This base implementation iterates over SCHEDULES and registers each job.
        """
        self.bus = bus
        if not self.settings.telegram_allowed_chat_ids:
            log.warning(
                "No telegram_allowed_chat_ids configured — skipping schedule registration",
                event="schedule_no_chat_ids",
                agent=self.name,
            )
            return

        if not self.SCHEDULES:
            return

        try:
            from core.scheduler import scheduler

            chat_id = self.settings.telegram_allowed_chat_ids[0]

            for task_name, cron_expr in self.SCHEDULES:
                scheduler.add_cron_job(
                    cron=cron_expr,
                    event=AgentEvent(
                        type=EventType.SCHEDULED_TASK,
                        agent_name=self.name,
                        chat_id=chat_id,
                        data={"task": task_name},
                    ),
                    bus=bus,
                )

            log.info("Schedules registered", event="schedules_registered", agent=self.name)

        except (ImportError, AttributeError) as e:
            log.warning(
                "Could not register schedules — check scheduler.py interface",
                event="schedule_error",
                agent=self.name,
                error=str(e),
            )

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Return True if the agent and all its dependencies are healthy.
        Called periodically by the bus and on startup.
        """

    # ── Cross-agent notifications ─────────────

    async def _handle_agent_message(self, event: AgentEvent) -> AgentResponse:
        """
        Default handler for AGENT_MESSAGE events.

        Logs the notification and stores it in memory as context so the
        agent can reference it in future responses.

        Override this in subclasses to take specific action.
        """
        if event.origin_agent == self.name:
            return AgentResponse(text="", agent_name=self.name)

        from_agent = event.data.get("from_agent", "unknown")
        message_event = event.data.get("event", "unknown")
        context = event.text

        log.info(
            "Agent message received",
            event="agent_message_received",
            agent=self.name,
            from_agent=from_agent,
            message_event=message_event,
        )

        # Store in memory so future responses can use it
        if self.memory and context:
            try:
                session_id = f"{self.name}_agent_messages"
                await self.memory.save_message(
                    session_id=session_id,
                    role="user",
                    content=(
                        f"[Agent notification from {from_agent}] "
                        f"Event: {message_event}. {context}"
                    ),
                    agent=self.name,
                )
            except Exception as e:
                log.warning(
                    "Failed to store agent message in memory",
                    event="message_memory_error",
                    error=str(e),
                )

        return AgentResponse(
            text="", agent_name=self.name, data={"notification_received": True}
        )

    # ── Helpers available to all agents ───────

    async def reply(self, event: AgentEvent, text: str) -> AgentResponse:
        """Send a message back to the user and return a response object.
        
        Format: **🤖 Agent Name**\n\nresponse text
        The agent name is bolded with an emoji for prominent identification.
        """
        formatted = f"**{self.emoji} {self.name}**\n\n{text}"
        await self.notifier.send(event.chat_id, formatted)
        return AgentResponse(text=text, agent_name=self.name)

    async def delegate(
        self,
        event: AgentEvent,
        target_agent_name: str,
        timeout_seconds: float = 120.0,
    ) -> AgentResponse:
        """Publish a sub-task to another agent and return its response.

        Timeout cancels the sub-task; side effects the worker already
        performed (messages sent, memory writes) are not rolled back —
        treat a timeout as 'result unknown', not 'worker did nothing'.
        """
        if self.bus is None:
            return AgentResponse(
                text="No message bus available for delegation.",
                agent_name=target_agent_name,
                success=False,
            )

        correlation_id = uuid.uuid4().hex
        parent_event_id = (
            event.correlation_id
            if event.correlation_id
            else (
                event.parent_event_id
                if event.parent_event_id
                else str(event.timestamp.timestamp())
            )
        )

        cloned_event = dataclasses.replace(
            event,
            agent_name=target_agent_name,
            origin_agent=self.name,
            correlation_id=correlation_id,
            parent_event_id=parent_event_id,
        )

        publish_task = asyncio.create_task(self.bus.publish(cloned_event))
        try:
            response = await asyncio.wait_for(publish_task, timeout=timeout_seconds)
        except asyncio.TimeoutError:
            log.warning(
                "Delegation timed out",
                event="delegate_timeout",
                agent=self.name,
                target=target_agent_name,
                timeout_seconds=timeout_seconds,
            )
            return AgentResponse(
                text=f"Delegation timed out after {timeout_seconds}s — the sub-task was cancelled mid-flight and may have partially completed.",
                agent_name=target_agent_name,
                success=False,
            )
        except Exception as exc:
            log.error(
                "Delegation failed",
                event="delegate_error",
                agent=self.name,
                target=target_agent_name,
                error=str(exc),
                exc_info=True,
            )
            return AgentResponse(
                text=f"Delegation failed: {exc}",
                agent_name=target_agent_name,
                success=False,
            )

        if response is None:
            return AgentResponse(
                text="Target agent not found or failed to respond.",
                agent_name=target_agent_name,
                success=False,
            )
        return response

    async def send_scheduled(
        self,
        chat_id: str,
        text: str,
        tag: str = "",
        is_emergency: bool = False,
    ) -> bool:
        """Send a scheduled notification, respecting quiet hours.

        Returns True if the message was sent, False if suppressed by
        quiet-hours gating. Interactive replies (``_handle_message``)
        should use ``self.notifier.send()`` directly — quiet hours only
        gate unscheduled/push notifications, not conversations the user
        initiated.

        Pass ``is_emergency=True`` to bypass quiet hours (e.g. health
        check failures, incident alerts).
        """
        if not self.should_notify(tag, is_emergency=is_emergency):
            log.info(
                "Scheduled notification suppressed by quiet hours",
                event="quiet_hours_suppressed",
                agent=self.name,
                tag=tag,
            )
            return False
        await self.notifier.send(chat_id, text)
        return True

    def resolve_model(self, chat_id: str) -> str:
        """
        Effective model for a chat, in precedence order:
          1. This chat's /model override (core/bus.py's chat_model_map)
          2. This agent's <AGENT>_AGENT_MODEL env var (self.model)
          3. "" — caller falls back to settings.default_model
        """
        if self.bus:
            override = self.bus.get_chat_model(chat_id)
            if override:
                return override
        return self.model

    def _is_authorized(self, chat_id: str) -> bool:
        """Check if a chat_id is in the allowlist."""
        allowed = self.settings.telegram_allowed_chat_ids
        # Empty allowlist means no restriction (useful for development)
        if not allowed:
            return True
        return chat_id in allowed

    def should_notify(
        self, tag: str, is_emergency: bool = False, _now: "datetime | None" = None
    ) -> bool:
        """Check quiet hours before sending a notification.

        Returns True if the message is allowed through.
        Pass is_emergency=True to bypass quiet hours (e.g. server crash).
        _now is a test seam — leave it None in production.
        """
        from core.quiet_hours import should_notify
        return should_notify(self.settings, tag=tag, is_emergency=is_emergency, now=_now)

    # ── Structured agent profile ─────────────

    async def get_profile(self) -> AgentProfile:
        """Return this agent's profile, loading from the state store or
        seeding from class defaults if no persisted record exists yet."""
        if self._profile is not None:
            return self._profile
        await self.ensure_profile()
        assert self._profile is not None
        return self._profile

    async def ensure_profile(self) -> AgentProfile:
        """Load profile from DB, or seed from class/instance attributes and
        persist it. Safe to call multiple times."""
        if self._profile is not None:
            return self._profile
        if self._state_store is not None:
            loaded = await self._state_store.load_agent_profile(self.name)
            if loaded is not None:
                self._profile = loaded
                return loaded
        self._profile = AgentProfile(
            name=self.name,
            description=getattr(self, "description", ""),
            emoji=getattr(self, "emoji", "🤖"),
            autonomy_level=getattr(self, "autonomy_level", "supervised"),
            routable=getattr(self, "routable", True),
            enabled=True,
        )
        if self._state_store is not None:
            await self._state_store.save_agent_profile(self._profile)
            reloaded = await self._state_store.load_agent_profile(self.name)
            if reloaded is not None:
                self._profile = reloaded
        return self._profile

    async def save_profile(self) -> None:
        """Persist the current profile to the state store."""
        profile = await self.get_profile()
        if self._state_store is not None:
            await self._state_store.save_agent_profile(profile)

    async def _run_validation_contract(
        self,
        event: AgentEvent,
        contract_text: str,
    ) -> AgentResponse:
        """Run a validation contract against an LLM and return a pass/fail report.

        Parses - [ ] assertions from contract_text, sends them to the
        LLM for evaluation, and returns an :class:`AgentResponse` whose
        success flag reflects whether every assertion passed.
        """
        if self.llm is None:
            return AgentResponse(
                text="LLM not available for validation.",
                agent_name=self.name,
                success=False,
            )

        assertions = [
            line.removeprefix("- [ ]").strip()
            for line in contract_text.splitlines()
            if line.strip().startswith("- [ ]")
        ]

        if not assertions:
            return AgentResponse(
                text="No validation assertions found in contract.",
                agent_name=self.name,
                success=False,
            )

        numbered_assertions = "\n".join(
            f"{i + 1}. {assertion}" for i, assertion in enumerate(assertions)
        )

        system_prompt = (
            "You are a validation assistant. For each assertion below, evaluate "
            "whether the implementation satisfies it. Return a numbered list with "
            "PASS or FAIL and a one-line reason for each. Do not execute any tools."
        )

        user_message = (
            "Evaluate the following assertions against the implementation and "
            "return a numbered list with PASS or FAIL and a one-line reason "
            "for each:\n\n" + numbered_assertions
        )

        try:
            llm_result = await self.llm.complete(
                messages=[Message(role="user", content=user_message)],
                system=system_prompt,
                model=self.resolve_model(event.chat_id),
            )
        except Exception as exc:
            log.error(
                "Validation LLM call failed",
                event="validation_llm_error",
                agent=self.name,
                error=str(exc),
            )
            return AgentResponse(
                text=f"Validation failed: {exc}",
                agent_name=self.name,
                success=False,
            )

        lines = llm_result.text.splitlines()
        results: list[str] = []
        for line in lines:
            m = re.match(r"^\s*\d+\.\s*(PASS|FAIL)", line)
            if m:
                results.append(line.strip())

        all_pass = bool(results) and len(results) == len(assertions) and all(
            re.match(r"^\s*\d+\.\s*PASS\b", r) for r in results
        )

        if not results:
            validation_report = llm_result.text.strip()
        else:
            validation_report = "\n".join(results)

        return AgentResponse(
            text=validation_report,
            agent_name=self.name,
            success=all_pass,
            data={"assertions_count": len(assertions), "passed": all_pass},
        )
