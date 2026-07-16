"""
interfaces/cli.py
-----------------
Interactive terminal REPL interface. Reads stdin, publishes to the bus,
prints responses via CLINotifier.

The "cli" chat_id is pre-paired at construction — no pairing code required.

Commands supported:
  /planmode [agent]  — toggle plan mode
  /help              — show available commands
  exit               — quit the CLI loop
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from core.logger import get_logger
from core.protocols import AgentEvent, EventType
from core.routing import parse_agent_tag

if TYPE_CHECKING:
    from core.bus import MessageBus
    from core.notifier import CLINotifier
    from core.safety import Safety
    from core.agent_creator import AgentCreator

log = get_logger("cli_interface")

_CLI_CHAT_ID = "cli"

_HELP_TEXT = (
    "Available commands:\n"
    "  /planmode [agent]  — toggle plan mode for one or all agents\n"
    "  /help              — show this message\n"
    "  exit               — quit\n\n"
    "Or just type a message to talk to your agents."
)


class CLIInterface:
    def __init__(
        self,
        bus: "MessageBus",
        safety: "Safety",
        creator: "AgentCreator",
        notifier: "CLINotifier",
    ) -> None:
        self._bus = bus
        self._safety = safety
        self._creator = creator
        self._notifier = notifier
        # Auto-pair the CLI chat_id — local users are trusted
        self._safety.pairing.pair_directly(_CLI_CHAT_ID)

    def _make_event(self, text: str) -> AgentEvent:
        agent_name, text = parse_agent_tag(text, self._bus.registered_agents)
        return AgentEvent(
            type=EventType.USER_MESSAGE,
            agent_name=agent_name,
            chat_id=_CLI_CHAT_ID,
            text=text,
        )

    async def run(self) -> None:
        loop = asyncio.get_event_loop()
        print("\n[CLI ready — type messages below, or 'exit' to quit]")
        print("> ", end="", flush=True)

        while True:
            try:
                text = await loop.run_in_executor(None, input, "")
            except EOFError:
                break

            text = text.strip()
            if not text:
                print("> ", end="", flush=True)
                continue

            if text.lower() == "exit":
                print("[CLI exiting]")
                break

            await self._handle(text)

    async def _handle(self, text: str) -> None:
        if text.startswith("/planmode"):
            await self._handle_planmode(text)
            return

        if text.startswith("/help"):
            print(_HELP_TEXT)
            print("> ", end="", flush=True)
            return

        if text.startswith("/newagent") or self._creator.is_active(_CLI_CHAT_ID):
            response = await self._creator.handle(_CLI_CHAT_ID, text)
            print(f"\n{response}\n> ", end="", flush=True)
            return

        event = self._make_event(text)
        response = await self._bus.publish(event)
        if response and not response.success:
            log.warning(
                "Agent error in CLI",
                event="cli_error",
                agent=response.agent_name,
            )
        if response:
            print("> ", end="", flush=True)

    async def _handle_planmode(self, text: str) -> None:
        parts = text.split()
        agent_name = parts[1].lower() if len(parts) > 1 else None

        toggled = []
        for name in self._bus.registered_agents:
            if agent_name is None or name == agent_name:
                agent = self._bus.get_agent(name)
                if agent:
                    new_state = agent.toggle_plan_mode(_CLI_CHAT_ID)
                    state = "ON" if new_state else "OFF"
                    toggled.append(f"{name}: Plan mode {state}")

        if toggled:
            print("\n".join(toggled))
        else:
            print(
                f"No agent named '{agent_name}'. "
                f"Available: {', '.join(self._bus.registered_agents)}"
            )
        print("> ", end="", flush=True)