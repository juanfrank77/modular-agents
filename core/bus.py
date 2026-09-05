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
from core.intent_classifier import classify_agent

if TYPE_CHECKING:
    from agents.base import BaseAgent
    from core.protocols import LLMProvider
    from core.state_store import StateStore

log = get_logger("bus")

class MessageBus:
    def __init__(
        self,
        llm: "LLMProvider | None" = None,
        classifier_model: str = "",
        state_store: "StateStore | None" = None,
    ) -> None:
        # agent_name → agent instance
        self._agents: dict[str, "BaseAgent"] = {}
        # Maps chat_id → last active agent name (fallback when classification
        # is unavailable or inconclusive)
        self._chat_agent_map: dict[str, str] = {}
        # Maps chat_id → locked agent name (user explicitly chose this agent)
        self._chat_agent_lock: dict[str, str] = {}
        # Maps chat_id → per-chat /model override (empty = use each agent's
        # own default: its <AGENT>_AGENT_MODEL env var, else the global default)
        self._chat_model_map: dict[str, str] = {}
        self._llm = llm
        self._classifier_model = classifier_model
        self._state_store = state_store

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
          2. Content-based classifier for untagged user messages
          3. Last agent that handled this chat_id (conversation continuity)
          4. First registered agent as fallback
        """
        agent = await self._resolve_agent(event)
        if not agent:
            log.warning("No agent found for event", event="routing_failed",
                        agent_hint=event.agent_name)
            return None

        # Track which agent is handling this chat. Only real user messages
        # should update stickiness — autonomous events (scheduled tasks,
        # heartbeats, webhooks, etc.) must not overwrite the user's actual
        # last-conversation-partner used as a routing fallback.
        # Delegated sub-tasks (origin_agent set) keep type=USER_MESSAGE and
        # the original chat_id, but they must not overwrite the user's
        # conversation partner. Interface-originated user messages always
        # have origin_agent == "", so normal routing is unaffected.
        if (
            event.chat_id
            and event.type is EventType.USER_MESSAGE
            and not event.origin_agent
        ):
            self._chat_agent_map[event.chat_id] = agent.name
            if self._state_store:
                await self._state_store.save_chat_agent(event.chat_id, agent.name)

        log.info(
            "Routing event",
            event="bus_route",
            agent=agent.name,
            event_type=event.type.name,
            chat_id=event.chat_id,
        )

        try:
            with log.timer() as t:
                response = await agent.dispatch(event)
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
        tasks = [agent.dispatch(event) for agent in self._agents.values()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        responses = []
        
        for r in results:
            if isinstance(r, Exception):
                log.error("Agent error during broadcast", event="bus_broadcast_error",
                          error=str(r))
            else:
                responses.append(r)

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

    def get_agent(self, name: str) -> "BaseAgent | None":
        return self._agents.get(name)

    @property
    def registered_agents(self) -> list[str]:
        return list(self._agents.keys())

    async def send_thinking(self, chat_id: str) -> int | None:
        """Send a 'Thinking...' placeholder and return its message ID."""
        if not self._agents:
            return None
        agent = next(iter(self._agents.values()))
        return await agent.notifier.send_and_get_id(chat_id, "⏳ Thinking...")

    async def clear_thinking(self, chat_id: str, message_id: int) -> None:
        """Delete the thinking placeholder message."""
        if not self._agents:
            return
        agent = next(iter(self._agents.values()))
        await agent.notifier.delete_message(chat_id, message_id)

    async def send_notification(self, chat_id: str, text: str) -> None:
        """
        Send a plain message to a chat via the first available agent's notifier.
        Used by main.py for system messages (pairing, startup notices) without
        needing to access bus._agents directly.
        """
        if not self._agents:
            log.warning("send_notification called but no agents registered",
                        event="notify_no_agents")
            return
        agent = next(iter(self._agents.values()))
        await agent.notifier.send(chat_id, text)

    # ── Internal ─────────────────────────────

    async def _resolve_agent(self, event: AgentEvent) -> "BaseAgent | None":
        # Explicit routing always wins: '@tag' from an interface, or the
        # agent_name scheduled tasks/heartbeats set directly.
        if event.agent_name and event.agent_name in self._agents:
            return self._agents[event.agent_name]

        # If the chat has a locked agent, use it (unless explicit @tag was used,
        # which is handled above). This prevents the classifier from hijacking
        # the conversation when the user forgets the @tag.
        if event.chat_id:
            locked = self.get_chat_agent_lock(event.chat_id)
            if locked:
                return self._agents[locked]

        # Content-based routing for untagged user messages, when a
        # classifier LLM is wired up.
        if event.type is EventType.USER_MESSAGE and event.text and self._llm:
            candidates = {
                name: agent.description
                for name, agent in self._agents.items()
                if getattr(agent, "routable", True)
            }
            if candidates:
                picked = await classify_agent(
                    event.text, candidates, self._llm, self._classifier_model
                )
                if picked and picked in self._agents:
                    return self._agents[picked]

        # Fallback: last agent that handled this chat, then first-registered.
        if event.chat_id and event.chat_id in self._chat_agent_map:
            last_agent = self._chat_agent_map[event.chat_id]
            if last_agent in self._agents:
                return self._agents[last_agent]

        if self._agents:
            return next(iter(self._agents.values()))

        return None

    async def load_chat_agent_map(self) -> None:
        """Rehydrate the chat->agent continuity map from the state store.
        Called once at startup, after all agents are registered."""
        if not self._state_store:
            return
        self._chat_agent_map = await self._state_store.load_chat_agent_map()
        log.info(
            "Chat agent map loaded",
            event="chat_agent_map_loaded",
            count=len(self._chat_agent_map),
        )

    # ── Per-chat agent lock ──────────────────────

    async def lock_chat_agent(self, chat_id: str, agent_name: str) -> bool:
        """Lock a chat to a specific agent. All future messages in this chat
        will route to this agent unless an explicit @tag is used.
        
        Returns True if successful, False if agent doesn't exist.
        """
        if agent_name not in self._agents:
            return False
        self._chat_agent_lock[chat_id] = agent_name
        # Also update the stickiness map so fallback routing agrees
        self._chat_agent_map[chat_id] = agent_name
        if self._state_store:
            await self._state_store.save_chat_agent(chat_id, agent_name)
        log.info(
            "Chat agent locked",
            event="agent_lock",
            chat_id=chat_id,
            agent=agent_name,
        )
        return True

    async def unlock_chat_agent(self, chat_id: str) -> None:
        """Remove the agent lock for a chat, allowing normal routing."""
        self._chat_agent_lock.pop(chat_id, None)
        log.info(
            "Chat agent unlocked",
            event="agent_unlock",
            chat_id=chat_id,
        )

    def get_chat_agent_lock(self, chat_id: str) -> str | None:
        """Return the locked agent name for a chat, or None if not locked."""
        locked = self._chat_agent_lock.get(chat_id)
        if locked and locked in self._agents:
            return locked
        return None

    # ── Per-chat model override ──────────────────

    def get_chat_model(self, chat_id: str) -> str:
        """Return this chat's /model override, or "" if none is set."""
        return self._chat_model_map.get(chat_id, "")

    async def set_chat_model(self, chat_id: str, model: str) -> None:
        self._chat_model_map[chat_id] = model
        if self._state_store:
            await self._state_store.save_chat_model(chat_id, model)

    async def clear_chat_model(self, chat_id: str) -> None:
        self._chat_model_map.pop(chat_id, None)
        if self._state_store:
            await self._state_store.delete_chat_model(chat_id)

    async def load_chat_model_map(self) -> None:
        """Rehydrate the per-chat model override map from the state store.
        Called once at startup, after all agents are registered."""
        if not self._state_store:
            return
        self._chat_model_map = await self._state_store.load_chat_model_map()
        log.info(
            "Chat model map loaded",
            event="chat_model_map_loaded",
            count=len(self._chat_model_map),
        )
