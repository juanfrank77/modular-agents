"""
core/safety.py
--------------
Execution control: pairing codes, command blocklist, approval gates.

Components:
  - PairingManager: 6-digit code for non-allowlisted chats
  - CommandBlocklist: blocks dangerous shell commands
  - ApprovalGate: inline-button approval for supervised actions
  - check_action(): orchestrates all safety checks

Usage:
    from core.safety import Safety, ActionType
    safety = Safety(notifier=notifier, allowed_ids=settings.telegram_allowed_chat_ids)
    result = await safety.check_action(chat_id, ActionType.WRITE_HIGH, "supervised", "Deploy to prod")
"""

from __future__ import annotations

import asyncio
import random
import re
import uuid
from enum import Enum, auto
from typing import TYPE_CHECKING

from core.logger import get_logger

if TYPE_CHECKING:
    from core.notifier import TelegramNotifier

log = get_logger("safety")


class ActionType(Enum):
    READ = auto()
    WRITE_LOW = auto()
    WRITE_HIGH = auto()
    EXECUTE = auto()
    DESTRUCTIVE = auto()


# ──────────────────────────────────────────────
# Pairing Manager
# ──────────────────────────────────────────────

class PairingManager:
    """
    Generates a 6-digit pairing code at startup.
    Non-allowlisted chats must send this code before interacting.
    """

    def __init__(self, allowed_ids: list[str]) -> None:
        self._code = f"{random.randint(100000, 999999)}"
        self._allowed_ids = set(allowed_ids)
        self._paired: set[str] = set()

    @property
    def code(self) -> str:
        return self._code

    def is_paired(self, chat_id: str) -> bool:
        if not self._allowed_ids:
            return True  # no restrictions in dev mode
        if chat_id in self._allowed_ids:
            return True
        return chat_id in self._paired

    def try_pair(self, chat_id: str, text: str) -> bool:
        """Returns True if the text matches the pairing code."""
        if text.strip() == self._code:
            self._paired.add(chat_id)
            log.info("Chat paired", event="pairing_success", chat_id=chat_id)
            return True
        return False


# ──────────────────────────────────────────────
# Command Blocklist
# ──────────────────────────────────────────────

_BLOCKED_PATTERNS = [
    re.compile(r"rm\s+-rf\s+/", re.IGNORECASE),
    re.compile(r"\bdd\s+if=", re.IGNORECASE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"chmod\s+777\s+/", re.IGNORECASE),
    re.compile(r"curl\s+.*\|\s*(?:ba)?sh", re.IGNORECASE),
    re.compile(r"wget\s+.*\|\s*(?:ba)?sh", re.IGNORECASE),
    re.compile(r":\(\)\s*\{\s*:\|:\s*&\s*\}\s*;", re.IGNORECASE),  # fork bomb
    re.compile(r">\s*/dev/sd[a-z]", re.IGNORECASE),
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r"\breboot\b", re.IGNORECASE),
]


def is_blocked_command(text: str) -> bool:
    """Check if text contains a blocked dangerous command pattern."""
    for pattern in _BLOCKED_PATTERNS:
        if pattern.search(text):
            log.warning("Blocked command detected", event="command_blocked", pattern=pattern.pattern)
            return True
    return False


# ──────────────────────────────────────────────
# Approval Gate
# ──────────────────────────────────────────────

_DEFAULT_TIMEOUT = 300  # fallback when action type not in timeouts dict


class ApprovalGate:
    """
    For supervised-mode actions: sends inline buttons, waits for user response.
    Timeout is configurable per ActionType via the timeouts dict.
    """

    def __init__(
        self,
        notifier: "TelegramNotifier",
        timeouts: dict[str, int] | None = None,
    ) -> None:
        self._notifier = notifier
        self._timeouts: dict[str, int] = timeouts or {}
        self._pending: dict[str, asyncio.Event] = {}
        self._results: dict[str, bool] = {}

    async def request_approval(
        self,
        chat_id: str,
        description: str,
        action_type: "ActionType | None" = None,
    ) -> bool:
        """Send approval buttons and wait for response. Returns True if approved."""
        timeout = (
            self._timeouts.get(action_type.name, _DEFAULT_TIMEOUT)
            if action_type is not None
            else _DEFAULT_TIMEOUT
        )

        approval_id = str(uuid.uuid4())[:8]
        event = asyncio.Event()
        self._pending[approval_id] = event

        await self._notifier.send_with_buttons(
            chat_id=chat_id,
            text=f"*Approval Required*\n\n{description}",
            buttons=[
                ("Approve", f"approve:{approval_id}"),
                ("Deny", f"deny:{approval_id}"),
            ],
        )

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            approved = self._results.pop(approval_id, False)
        except asyncio.TimeoutError:
            log.warning(
                "Approval timed out",
                event="approval_timeout",
                approval_id=approval_id,
                timeout=timeout,
                action_type=action_type.name if action_type else None,
            )
            approved = False
        finally:
            self._pending.pop(approval_id, None)
            self._results.pop(approval_id, None)

        return approved

    def resolve(self, approval_id: str, approved: bool) -> None:
        """Called from Telegram callback handler when user clicks a button."""
        self._results[approval_id] = approved
        event = self._pending.get(approval_id)
        if event:
            event.set()
            log.info("Approval resolved", event="approval_resolved",
                     approval_id=approval_id, approved=approved)


# ──────────────────────────────────────────────
# Safety Coordinator
# ──────────────────────────────────────────────

class Safety:
    """Orchestrates all safety checks."""

    def __init__(
        self,
        notifier: "TelegramNotifier",
        allowed_ids: list[str],
        approval_timeouts: dict[str, int] | None = None,
    ) -> None:
        self.pairing = PairingManager(allowed_ids)
        self.gate = ApprovalGate(notifier, timeouts=approval_timeouts)

    async def check_action(
        self,
        chat_id: str,
        action_type: ActionType,
        autonomy_level: str,
        description: str = "",
    ) -> bool:
        """
        Run all safety checks for an action. Returns True if the action is allowed.
        """
        # Pairing check
        if not self.pairing.is_paired(chat_id):
            return False

        # Command blocklist — always enforced
        if is_blocked_command(description):
            return False

        # Autonomous agents skip approval gate
        if autonomy_level == "autonomous":
            return True

        # Read-only agents can only read
        if autonomy_level == "read_only":
            return action_type == ActionType.READ

        # Supervised: low-risk writes are auto-approved
        if autonomy_level == "supervised":
            if action_type in (ActionType.READ, ActionType.WRITE_LOW):
                return True
            # High-risk actions need explicit approval
            return await self.gate.request_approval(chat_id, description, action_type=action_type)

        return False
