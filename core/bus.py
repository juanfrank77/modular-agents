"""
core/bus.py
-----------
The message bus. Agents subscribe to event types — no direct coupling.
The bus routes; agents handle.

Flow:
  Telegram update → bus.publish(AgentEvent) → subscribed agent.handle(event)

Adding a new agent = register it. Zero changes to existing agents.

Usage:
    from core.bus import MessageBus
    bus = MessageBus()
    bus.register(my_agent)
    await bus.publish(event)
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from core.logger import get_logger
from core.protocols import AgentEvent, AgentResponse, EventType

if TYPE_CHECKING:
    from agents.base import BaseAgent

log = get_logger("bus")


class MessageBus:
    def __init__(self) -> None:
        # agent_name → agent instance
        self._agents: dict[str, "BaseAgent"] = {}
        # Maps chat_id → last active agent name (for conversation continuity)
        self._chat_agent_map: dict[str, str] = {}

    # ── Registration ──────────────────────────

    def register(self, agent: "BaseAgent") -> None:
        self._agents[agent.name] = agent
        log.info("Agent registered", event="agent_registered", agent=agent.name)

    # ── Publishing ────────────────────────────

    async def publish(self, event: AgentEvent) -> AgentResponse | None:
        """
        Route an event to the appropriate agent and return its response.

        Routing priority:
          1. event.agent_name if explicitly set (scheduled tasks, heartbeats)
          2. Last agent that handled this chat_id (conversation continuity)
          3. First registered agent as fallback
        """
        agent = self._resolve_agent(event)
        if not agent:
            log.warning("No agent found for event", event="routing_failed",
                        agent_hint=event.agent_name)
            return None

        # Track which agent is handling this chat
        if event.chat_id:
            self._chat_agent_map[event.chat_id] = agent.name

        log.info(
            "Routing event",
            event="bus_route",
            agent=agent.name,
            event_type=event.type.name,
            chat_id=event.chat_id,
        )

        try:
            with log.timer() as t:
                response = await agent.handle(event)
            log.info(
                "Event handled",
                event="bus_handled",
                agent=agent.name,
                duration_ms=t.ms,
                success=response.success,
            )
            return response
        except Exception as e:
            log.error(
                "Agent raised exception",
                event="bus_error",
                agent=agent.name,
                error=str(e),
            )
            return AgentResponse(
                text="Something went wrong. Please try again.",
                agent_name=agent.name,
                success=False,
            )

    async def publish_all(self, event: AgentEvent) -> list[AgentResponse]:
        """Broadcast an event to ALL registered agents (used for heartbeats)."""
        responses: list[AgentResponse] = []

        async def _run(agent: "BaseAgent") -> None:
            try:
                responses.append(await agent.handle(event))
            except Exception as e:
                log.error("Agent error during broadcast", event="bus_broadcast_error",
                          agent=agent.name, error=str(e))

        async with asyncio.TaskGroup() as tg:
            for agent in self._agents.values():
                tg.create_task(_run(agent))

        return responses

    # ── Health ────────────────────────────────

    async def health_check_all(self) -> dict[str, bool]:
        results = {}
        for name, agent in self._agents.items():
            try:
                results[name] = await agent.health_check()
            except Exception as e:
                log.error("Health check failed", event="health_check_error",
                          agent=name, error=str(e))
                results[name] = False
        return results

    @property
    def registered_agents(self) -> list[str]:
        return list(self._agents.keys())

    # ── Internal ─────────────────────────────

    def _resolve_agent(self, event: AgentEvent) -> "BaseAgent | None":
        # Explicit routing (scheduled tasks always set agent_name)
        if event.agent_name and event.agent_name in self._agents:
            return self._agents[event.agent_name]

        # Continuity: same chat → same agent
        if event.chat_id and event.chat_id in self._chat_agent_map:
            last_agent = self._chat_agent_map[event.chat_id]
            if last_agent in self._agents:
                return self._agents[last_agent]

        # Fallback: first registered agent
        if self._agents:
            return next(iter(self._agents.values()))

        return None
