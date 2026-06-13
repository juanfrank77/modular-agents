"""
core/safety.py
--------------
Execution control: pairing codes, command blocklist, approval gates.

Components:
  - PairingManager: cryptographically random token for non-allowlisted chats
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
import re
import uuid
from enum import Enum, auto
from typing import TYPE_CHECKING

from core.logger import get_logger

if TYPE_CHECKING:
    from core.protocols import Notifier

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
    Generates a cryptographically random pairing token at startup.
    Non-allowlisted chats must send this token before interacting.
    Locks pairing after 5 failed attempts to prevent brute-force.
    """

    MAX_FAILED_ATTEMPTS = 5

    def __init__(self, allowed_ids: list[str]) -> None:
        self._token = uuid.uuid4().hex  # 32-char cryptographically random token
        self._allowed_ids = set(allowed_ids)
        self._paired: set[str] = set()
        self._failed_attempts: dict[str, int] = {}  # chat_id -> attempt count

    @property
    def code(self) -> str:
        return self._token

    def is_paired(self, chat_id: str) -> bool:
        if not self._allowed_ids:
            return True  # no restrictions in dev mode
        if chat_id in self._allowed_ids:
            return True
        return chat_id in self._paired

    def is_locked(self, chat_id: str) -> bool:
        return self._failed_attempts.get(chat_id, 0) >= self.MAX_FAILED_ATTEMPTS

    def try_pair(self, chat_id: str, text: str) -> bool:
        """Returns True if the text matches the pairing code."""
        # Check if locked
        if self.is_locked(chat_id):
            log.warning(
                "Pairing locked for chat",
                event="pairing_locked",
                chat_id=chat_id,
                attempts=self._failed_attempts.get(chat_id, 0),
            )
            return False

        if text.strip() == self._token:
            self._paired.add(chat_id)
            self._failed_attempts.pop(chat_id, None)  # reset on success
            log.info("Chat paired", event="pairing_success", chat_id=chat_id)
            return True

        # Increment failed attempts
        self._failed_attempts[chat_id] = self._failed_attempts.get(chat_id, 0) + 1
        log.warning(
            "Invalid pairing attempt",
            event="pairing_failed",
            chat_id=chat_id,
            attempts=self._failed_attempts[chat_id],
        )
        return False

    def pair_directly(self, chat_id: str) -> None:
        """Pair a chat_id without requiring the code — for trusted local interfaces."""
        self._paired.add(chat_id)
        self._failed_attempts.pop(chat_id, None)
        log.info("Chat paired directly", event="pairing_direct", chat_id=chat_id)


# ──────────────────────────────────────────────
# Command Blocklist
# ──────────────────────────────────────────────

# NOTE: These patterns are a defense-in-depth measure. They do NOT provide shell
# security — the cli_runner.py uses asyncio.create_subprocess_exec() which passes
# commands as explicit argument lists (shell=False by default), preventing shell
# injection. Do NOT rely on this regex blocklist for security.
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
    """Check if text contains a blocked dangerous command pattern.

    .. deprecated::
        Use :meth:`Safety.is_command_blocked` instead, which includes
        extra patterns from settings.
    """
    import warnings

    warnings.warn(
        "is_blocked_command() is deprecated — use Safety.is_command_blocked() "
        "to include extra_blocked_patterns from settings",
        DeprecationWarning,
        stacklevel=2,
    )
    for pattern in _BLOCKED_PATTERNS:
        if pattern.search(text):
            log.warning(
                "Blocked command detected",
                event="command_blocked",
                pattern=pattern.pattern,
            )
            return True
    return False


# ──────────────────────────────────────────────
# Rate Limiter
# ──────────────────────────────────────────────


class RateLimiter:
    """
    Token bucket rate limiter per chat_id.
    Prevents abuse by limiting messages per minute.
    """

    def __init__(self, rpm: int = 20):
        self._rpm = rpm
        self._buckets: dict[str, list[float]] = {}

    def _prune_old(self, chat_id: str, now: float) -> None:
        """Remove timestamps older than 60 seconds."""
        window_start = now - 60
        if chat_id in self._buckets:
            self._buckets[chat_id] = [t for t in self._buckets[chat_id] if t > window_start]

    def is_allowed(self, chat_id: str) -> bool:
        """Check if chat_id is within rate limit. Returns True if allowed."""
        import time
        now = time.time()
        self._prune_old(chat_id, now)
        count = len(self._buckets.get(chat_id, []))
        if count >= self._rpm:
            log.warning(
                "Rate limit exceeded",
                event="rate_limited",
                chat_id=chat_id,
                rpm=self._rpm,
            )
            return False
        self._buckets.setdefault(chat_id, []).append(now)
        return True

    def wait_time(self, chat_id: str) -> float:
        """Return seconds until oldest message expires (for cooldown messages)."""
        import time
        now = time.time()
        if chat_id not in self._buckets or not self._buckets[chat_id]:
            return 0
        oldest = min(self._buckets[chat_id])
        return max(0, 60 - (now - oldest))


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
        notifier: "Notifier",
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
        """Send approval buttons and wait for response. Returns True if approved.
        Non-Telegram chat_ids (not all-digit) are auto-approved after showing the plan.
        """
        # Telegram chat_ids are always integers (possibly negative for groups).
        # CLI uses "cli", HTTP uses "http_<token>". Auto-approve those.
        if not chat_id.lstrip("-").isdigit():
            await self._notifier.send(
                chat_id,
                f"*Plan*\n\n{description}\n\n_(Auto-approved — executing now)_",
            )
            return True

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
            log.info(
                "Approval resolved",
                event="approval_resolved",
                approval_id=approval_id,
                approved=approved,
            )


# ──────────────────────────────────────────────
# Safety Coordinator
# ──────────────────────────────────────────────


class Safety:
    """Orchestrates all safety checks."""

    def __init__(
        self,
        notifier: "Notifier",
        allowed_ids: list[str],
        approval_timeouts: dict[str, int] | None = None,
        extra_blocked_patterns: list[str] | None = None,
        rate_limit_rpm: int = 20,
    ) -> None:
        self.pairing = PairingManager(allowed_ids)
        self.gate = ApprovalGate(notifier, timeouts=approval_timeouts)
        self.rate_limiter = RateLimiter(rpm=rate_limit_rpm)
        # Compile extra patterns from settings
        self._extra_patterns = []
        if extra_blocked_patterns:
            for p in extra_blocked_patterns:
                try:
                    self._extra_patterns.append(re.compile(p))
                except re.error:
                    log.warning(
                        "Invalid extra blocked pattern, ignoring",
                        event="invalid_pattern",
                        pattern=p,
                    )

    def is_command_blocked(self, text: str) -> bool:
        """Check if text matches any blocked pattern (built-in + extra)."""
        # Check built-in patterns
        if is_blocked_command(text):
            return True
        # Check extra patterns
        for pattern in self._extra_patterns:
            if pattern.search(text):
                log.warning(
                    "Blocked command detected (extra pattern)",
                    event="command_blocked",
                    pattern=pattern.pattern,
                )
                return True
        return False

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

        # Command blocklist — always enforced (includes extra patterns)
        if self.is_command_blocked(description):
            return False

        # Autonomous agents skip the approval gate for everything EXCEPT
        # destructive actions. Destructive (DEPLOY_PROD, DB_MIGRATE,
        # DELETE_RESOURCE, etc.) always require explicit approval regardless
        # of autonomy level.
        if autonomy_level == "autonomous":
            if action_type == ActionType.DESTRUCTIVE:
                return await self.gate.request_approval(
                    chat_id, description, action_type=action_type
                )
            return True

        # Read-only agents can only read
        if autonomy_level == "read_only":
            return action_type == ActionType.READ

        # Supervised: low-risk writes are auto-approved
        if autonomy_level == "supervised":
            if action_type in (ActionType.READ, ActionType.WRITE_LOW):
                return True
            # High-risk actions need explicit approval
            return await self.gate.request_approval(
                chat_id, description, action_type=action_type
            )

        return False
