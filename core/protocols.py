"""
core/protocols.py
-----------------
All Protocol definitions for the framework.
Every swappable component implements one of these interfaces.
Nothing outside core/ should import concrete implementations directly —
only these protocols.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Protocol, runtime_checkable


# ──────────────────────────────────────────────
# Shared data types
# ──────────────────────────────────────────────


class EventType(Enum):
    USER_MESSAGE = auto()
    SCHEDULED_TASK = auto()
    HEARTBEAT_TICK = auto()
    WEBHOOK_EVENT = auto()
    APPROVAL_RESPONSE = auto()
    AGENT_MESSAGE = auto()

@dataclass
class AgentEvent:
    type: EventType
    agent_name: str  # which agent should handle this
    chat_id: str  # telegram chat_id to reply to
    origin_agent: str = "" # which agent originated the call
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class AgentResponse:
    text: str
    agent_name: str
    success: bool = True
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Message:
    role: str  # 'user' | 'assistant' | 'system'
    content: str
    agent: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ToolDef:
    """Describes one callable tool to the LLM (JSON-schema `parameters`)."""
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ToolCall:
    """A tool invocation the model requested — `id` ties it to the follow-up result."""
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class ToolResultInput:
    """The outcome of executing a ToolCall, fed back to the model on the next turn."""
    tool_call_id: str
    content: str


@dataclass
class LLMResult:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_assistant: Any = None  # opaque, provider-specific assistant turn — pass back unchanged


# ──────────────────────────────────────────────
# Protocols (swappable interfaces)
# ──────────────────────────────────────────────


@runtime_checkable
class LLMProvider(Protocol):
    supports_tools: bool

    async def complete(
        self,
        messages: list[Message],
        system: str,
        model: str = "",
        max_tokens: int = 1024,
        tools: list[ToolDef] | None = None,
        tool_result: ToolResultInput | None = None,
        raw_assistant: Any = None,
    ) -> LLMResult: ...

    async def summarize(self, messages: list[Message]) -> str: ...


@runtime_checkable
class Notifier(Protocol):
    async def send(self, chat_id: str, text: str) -> None: ...

    async def send_media(self, chat_id: str, path: str, caption: str = "") -> None: ...

    async def send_with_buttons(
        self,
        chat_id: str,
        text: str,
        buttons: list[tuple[str, str]],
    ) -> None: ...

    async def send_and_get_id(self, chat_id: str, text: str) -> int | None: ...

    async def delete_message(self, chat_id: str, message_id: int) -> None: ...


@runtime_checkable
class MemoryStore(Protocol):
    # ── Layer 1: SQLite (delegates to Storage) ──

    async def save_message(
        self, session_id: str, role: str, content: str, agent: str
    ) -> None: ...

    async def search_history(
        self, query: str, agent: str | None = None, limit: int = 10
    ) -> list[Message]: ...

    # ── Layer 2: Markdown index + topic files ──

    async def get_index(self) -> str: ...

    async def get_context(self, key: str) -> str: ...

    async def get_relevant_context(self, task: str) -> str: ...

    async def save_solution(self, agent: str, topic: str, content: str) -> None: ...

    # ── Session context with auto-compaction ──

    async def get_session_context(
        self, session_id: str, agent: str
    ) -> list[Message]: ...

    # ── Main entry point for agents ──

    async def build_context(
        self, session_id: str, agent: str, task: str = ""
    ) -> tuple[str, list[Message]]: ...

    # ── Consolidation ──

    async def consolidate(self, agent: str, force: bool = False) -> bool: ...

    def schedule_consolidation(self, agent: str) -> None: ...
