"""
core/budget.py
--------------
Budget manager for proactive agent actions.

Implements a blocking budget with:
- 15-second sliding window (configurable)
- Max 2 proactive messages per window per agent (configurable)
- Execution time tracking and learning for estimation
- Deferred action queue for actions exceeding threshold

Usage:
    from core.budget import BudgetManager, ActionType

    budget = BudgetManager(settings)
    can_send = budget.check_budget("devops", ActionType.PROACTIVE)
    if can_send:
        # send message
        budget.record_action("devops", ActionType.PROACTIVE, duration=5.2)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING

from core.logger import get_logger

if TYPE_CHECKING:
    from core.config import Settings

log = get_logger("budget")


class ActionType(Enum):
    """Classification of action types for budget purposes."""

    PROACTIVE = auto()  # Scheduled tasks, heartbeats, autonomous checks
    REACTIVE = auto()  # Direct responses to user messages (unlimited)


@dataclass
class ActionRecord:
    """Record of a completed action for time tracking."""

    agent_name: str
    action_type: ActionType
    duration: float  # actual execution time in seconds
    timestamp: float  # unix timestamp


@dataclass
class AgentBudgetState:
    """Budget state for a single agent."""

    window_start: float = 0.0
    message_count: int = 0
    execution_times: list[float] = field(
        default_factory=list
    )  # last N durations per action type

    # Map action type to estimated durations (moving average)
    estimated_durations: dict[ActionType, float] = field(default_factory=dict)

    # Track last N action durations for learning
    history: list[ActionRecord] = field(default_factory=list)
    max_history: int = 50  # keep last 50 records

    def update_estimate(self, action_type: ActionType, duration: float) -> None:
        """Update moving average for an action type."""
        if action_type not in self.estimated_durations:
            self.estimated_durations[action_type] = duration
            return

        alpha = 0.3  # smoothing factor
        current = self.estimated_durations[action_type]
        self.estimated_durations[action_type] = alpha * duration + (1 - alpha) * current

    def get_estimate(self, action_type: ActionType) -> float:
        """Get estimated duration for an action type. Returns 0 if no history."""
        return self.estimated_durations.get(action_type, 0.0)


class BudgetManager:
    """
    Manages per-agent budget for proactive actions.

    Rules:
    - Reactive actions always bypass the budget
    - Proactive actions limited to max_per_window within window_seconds
    - Actions exceeding defer_threshold are deferred and retried
    """

    def __init__(self, settings: "Settings") -> None:
        self._window_seconds = settings.budget_window_seconds
        self._max_per_window = settings.budget_max_proactive_per_window
        self._defer_threshold = settings.budget_defer_threshold_seconds
        self._max_retry = settings.budget_max_retry_attempts

        # Per-agent budget state
        self._agent_states: dict[str, AgentBudgetState] = {}

    def _get_state(self, agent_name: str) -> AgentBudgetState:
        """Get or create budget state for an agent."""
        if agent_name not in self._agent_states:
            self._agent_states[agent_name] = AgentBudgetState()
        return self._agent_states[agent_name]

    def _is_window_expired(self, state: AgentBudgetState) -> bool:
        """Check if the current budget window has expired."""
        current_time = time.time()
        return (current_time - state.window_start) >= self._window_seconds

    def _reset_window(self, state: AgentBudgetState) -> None:
        """Reset the budget window for a new period."""
        state.window_start = time.time()
        state.message_count = 0

    def check_budget(self, agent_name: str, action_type: ActionType) -> bool:
        """
        Check if an action can proceed under the budget.

        Returns True if action is allowed, False if it should be deferred.

        Note: Reactive actions always return True (unlimited).
        """
        # Reactive actions bypass budget entirely
        if action_type == ActionType.REACTIVE:
            return True

        state = self._get_state(agent_name)

        # Check if window has expired
        if self._is_window_expired(state):
            self._reset_window(state)
            log.info(
                "Budget window reset",
                event="budget_window_reset",
                agent=agent_name,
            )

        # Check message count limit
        if state.message_count >= self._max_per_window:
            log.info(
                "Budget limit reached",
                event="budget_limit_reached",
                agent=agent_name,
                message_count=state.message_count,
                max_allowed=self._max_per_window,
            )
            return False

        return True

    def get_estimated_duration(self, agent_name: str, action_type: ActionType) -> float:
        """Get estimated duration for an action based on historical data."""
        state = self._get_state(agent_name)
        return state.get_estimate(action_type)

    def should_defer(self, agent_name: str, action_type: ActionType) -> bool:
        """
        Check if an action should be deferred due to estimated duration.

        Returns True if the action is estimated to take longer than
        the defer threshold and should be queued for later.
        """
        if action_type == ActionType.REACTIVE:
            return False

        estimated = self.get_estimated_duration(agent_name, action_type)

        if estimated >= self._defer_threshold:
            log.info(
                "Action exceeds defer threshold",
                event="action_defer_threshold",
                agent=agent_name,
                estimated_duration=estimated,
                threshold=self._defer_threshold,
            )
            return True

        return False

    def record_action(
        self, agent_name: str, action_type: ActionType, duration: float
    ) -> None:
        """
        Record a completed action and update estimates.

        This is called after an action completes to:
        - Increment message count for proactive actions
        - Update duration estimates for learning
        """
        # Reactive actions don't count against budget
        if action_type == ActionType.REACTIVE:
            return

        state = self._get_state(agent_name)

        # Update message count
        state.message_count += 1

        # Record for learning
        record = ActionRecord(
            agent_name=agent_name,
            action_type=action_type,
            duration=duration,
            timestamp=time.time(),
        )
        state.history.append(record)

        # Trim history
        if len(state.history) > state.max_history:
            state.history = state.history[-state.max_history :]

        # Update moving average
        state.update_estimate(action_type, duration)

        log.debug(
            "Action recorded",
            event="action_recorded",
            agent=agent_name,
            action_type=action_type.name,
            duration=duration,
            message_count=state.message_count,
        )

    def record_action_start(self, agent_name: str, action_type: ActionType) -> float:
        """
        Call when an action starts, returns start time for tracking.

        Returns timestamp to pass to record_action_end() later.
        """
        return time.time()

    def record_action_end(
        self, agent_name: str, action_type: ActionType, start_time: float
    ) -> float:
        """
        Call when an action ends to record actual duration.

        Returns the actual duration in seconds.
        """
        duration = time.time() - start_time
        self.record_action(agent_name, action_type, duration)
        return duration

    def get_remaining_budget(self, agent_name: str) -> dict:
        """
        Get current budget status for an agent.

        Returns dict with remaining messages and window info.
        """
        state = self._get_state(agent_name)

        # Check if window needs reset
        if self._is_window_expired(state):
            remaining = self._max_per_window
            window_status = "reset"
        else:
            remaining = max(0, self._max_per_window - state.message_count)
            elapsed = time.time() - state.window_start
            window_status = f"{elapsed:.1f}/{self._window_seconds}s"

        return {
            "agent": agent_name,
            "remaining_messages": remaining,
            "max_messages": self._max_per_window,
            "window_status": window_status,
            "estimated_proactive_duration": state.get_estimate(ActionType.PROACTIVE),
        }

    def reset_agent(self, agent_name: str) -> None:
        """Reset budget state for an agent (useful for testing)."""
        if agent_name in self._agent_states:
            del self._agent_states[agent_name]
            log.info("Agent budget reset", event="budget_reset", agent=agent_name)

    @property
    def settings(self) -> dict:
        """Get current budget settings."""
        return {
            "window_seconds": self._window_seconds,
            "max_per_window": self._max_per_window,
            "defer_threshold": self._defer_threshold,
            "max_retry": self._max_retry,
        }
