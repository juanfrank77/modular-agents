"""
agents/business/agent.py
------------------------
The Business Agent — supervised autonomy, full LLM-powered request lifecycle.

Flow:
  1. Auth check
  2. Get or create session
  3. Save inbound message
  4. Load relevant skills
  5. Build context (markdown + compacted history)
  6. Build system prompt (persona + skills + context)
  7. Call LLM
  8. Save outbound message
  9. Reply via notifier
"""

from __future__ import annotations

from pathlib import Path

from core.logger import get_logger
from core.protocols import AgentEvent, AgentResponse, EventType, Message
from agents.base import BaseAgent

log = get_logger("business")

PERSONA = """\
You are a personal business assistant. You help your principal with:
- Answering questions and providing analysis
- Drafting messages, emails, and documents
- Brainstorming ideas and problem-solving
- Summarizing information and providing recommendations
- Managing tasks and priorities

Communication style:
- Be direct, concise, and actionable
- Match the tone of the conversation (casual or formal)
- When uncertain, say so honestly rather than guessing
- Proactively suggest next steps when appropriate
- Use markdown formatting when it aids readability

You have access to context about your principal's preferences, projects,
and background. Use this context to personalize your responses.
"""

SKILLS_DIR = Path("agents/business/skills")


class BusinessAgent(BaseAgent):
    name = "business"
    description = "Personal business assistant with LLM-powered responses."
    autonomy_level = "supervised"

    async def handle(self, event: AgentEvent) -> AgentResponse:
        # Heartbeat — just acknowledge
        if event.type == EventType.HEARTBEAT_TICK:
            log.info("Heartbeat received", event="heartbeat")
            return AgentResponse(text="HEARTBEAT_OK", agent_name=self.name)

        # Auth check
        if not self._is_authorized(event.chat_id):
            log.warning("Unauthorised access", event="auth_denied", chat_id=event.chat_id)
            return AgentResponse(text="Unauthorized.", agent_name=self.name, success=False)

        # Session
        session_id = await self.storage.get_or_create_session(event.chat_id, self.name)

        # Save inbound message
        await self.memory.save_message(session_id, "user", event.text, self.name)

        # Load skills
        if self.skill_loader:
            skills = self.skill_loader.find_relevant(event.text, SKILLS_DIR)
            if not skills:
                skills = self.skill_loader.load_all(SKILLS_DIR)
        else:
            skills = []

        # Build context
        markdown_context, history = await self.memory.build_context(session_id, self.name)

        # Build system prompt
        system_parts = [PERSONA]
        if skills:
            system_parts.append("## Relevant Skills\n" + "\n\n---\n\n".join(skills))
        if markdown_context:
            system_parts.append("## Principal Context\n" + markdown_context)
        system_prompt = "\n\n".join(system_parts)

        # Call LLM
        with log.timer() as t:
            response_text = await self.llm.complete(history, system=system_prompt)

        log.info("LLM response generated", event="llm_response",
                 chat_id=event.chat_id, duration_ms=t.ms)

        # Save outbound message
        await self.memory.save_message(session_id, "assistant", response_text, self.name)

        return await self.reply(event, response_text)

    async def register_schedules(self, bus) -> None:
        # No scheduled tasks yet — will add morning briefing, etc. later
        pass

    async def health_check(self) -> bool:
        try:
            await self.storage.search_history("_health_check_", limit=1)
            return True
        except Exception as e:
            log.error("Health check failed", event="health_check_error", error=str(e))
            return False
