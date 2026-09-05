"""
core/protocols.py
-----------------
All Protocol definitions for the agents system.
Every swappable component implements one of these interfaces.
Nothing outside core/ should import concrete implementations directly —
only these protocols.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from core.logger import get_logger
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Protocol, runtime_checkable


log = get_logger("protocols")

# ──────────────────────────────────────────────
# Shared envelope bounds
# ──────────────────────────────────────────────

_MAX_ENVELOPE_BYTES = 65536  # 64 KB per inter-agent event
_TEXT_CAP_RATIO = 0.5
_DATA_VALUE_CAP_RATIO = 0.25
_DATA_KEY_CAP = 50


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
    origin_agent: str = ""  # which agent originated the call
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: str = ""  # shared id linking a delegation request to its response
    parent_event_id: str = ""  # optional trace back to the originating event
    priority: int = 0  # higher = more urgent; used by preemption (10.9)

    def __post_init__(self) -> None:
        _truncate_envelope(self)


def _truncate_envelope(event: AgentEvent) -> None:
    max_bytes = _MAX_ENVELOPE_BYTES
    text_cap = int(max_bytes * _TEXT_CAP_RATIO)
    data_value_cap = int(max_bytes * _DATA_VALUE_CAP_RATIO)
    data_key_cap = _DATA_KEY_CAP

    truncated = False

    if len(event.text.encode("utf-8")) > text_cap:
        event.text = event.text.encode("utf-8")[:text_cap].decode("utf-8", errors="replace")
        truncated = True

    if len(event.data) > data_key_cap:
        event.data = dict(list(event.data.items())[:data_key_cap])
        truncated = True

    for key, value in list(event.data.items()):
        if isinstance(value, str):
            if len(value.encode("utf-8")) > data_value_cap:
                event.data[key] = value.encode("utf-8")[:data_value_cap].decode(
                    "utf-8", errors="replace"
                )
                truncated = True
        else:
            serialized = str(value)
            if len(serialized.encode("utf-8")) > data_value_cap:
                event.data[key] = serialized.encode("utf-8")[:data_value_cap].decode(
                    "utf-8", errors="replace"
                )
                truncated = True

    if _estimate_event_bytes(event) > max_bytes:
        original_text = event.text
        event.text = event.text.encode("utf-8")[: max(0, max_bytes // 4)].decode(
            "utf-8", errors="replace"
        )
truncated = truncated or (event.text != original_text)

    if truncated:
        log.warning(
            "AgentEvent envelope truncated",
            event="envelope_truncated",
            agent=event.agent_name,
            max_bytes=max_bytes,
        )


def _estimate_event_bytes(event: AgentEvent) -> int:
    return len(event.text.encode("utf-8")) + len(str(event.data).encode("utf-8"))


@dataclass
class AgentResponse:
    text: str
    agent_name: str
    success: bool = True
    data: dict[str, Any] = field(default_factory=dict)
    handoff: dict[str, Any] | None = None  # structured results from delegated tasks


@dataclass
class AgentProfile:
    """Durable identity record for an agent.

    Class attributes on ``BaseAgent`` subclasses are the declared defaults.
    This record persists them so the system can query and update agent
    identity without editing code.
    """

    name: str
    description: str
    emoji: str = "🤖"
    autonomy_level: str = "supervised"
    routable: bool = True
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""


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


class NotificationError(Exception):
    """Raised when a notifier cannot deliver a message.

    Carries the original cause and, for rate-limit/flood cases, the seconds the
    underlying channel asked us to wait before retrying.
    """

    def __init__(
        self,
        message: str,
        chat_id: str,
        *,
        cause: Exception | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.chat_id = chat_id
        self.cause = cause
        self.retry_after = retry_after


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
    """Outbound notification channel.

    All delivery methods raise :class:`NotificationError` when the message
    cannot be delivered so callers can react instead of silently timing out.
    """

    async def send(self, chat_id: str, text: str) -> None:
        """Deliver a text message. Raises NotificationError on failure."""
        ...

    async def send_media(self, chat_id: str, path: str, caption: str = "") -> None:
        """Deliver a media file. Raises NotificationError on failure."""
        ...

    async def send_with_buttons(
        self,
        chat_id: str,
        text: str,
        buttons: list[tuple[str, str]],
    ) -> None:
        """Deliver a message with inline buttons. Raises NotificationError on failure."""
        ...

    async def send_and_get_id(self, chat_id: str, text: str) -> int | None:
        """Deliver a message and return its platform id. Raises NotificationError on failure."""
        ...

    async def delete_message(self, chat_id: str, message_id: int) -> None:
        """Delete a previously delivered message. Raises NotificationError on failure."""
        ...


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
