"""
core/action_queue.py
--------------------
Queue for deferred proactive actions that exceed budget constraints.

When an action is deferred:
1. It goes into the queue with metadata (agent, action, retry count)
2. A background task periodically checks if budget is available
3. When budget opens, the action is retried
4. After max retries, the action is dropped and logged

Usage:
    from core.action_queue import ActionQueue
    from core.budget import ActionType

    queue = ActionQueue(budget_manager, settings)
    await queue.start()

    # When an action is deferred:
    await queue.enqueue("devops", my_action, ActionType.PROACTIVE)

    # On shutdown:
    await queue.stop()
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, TYPE_CHECKING

from core.logger import get_logger
from core.budget import ActionType, BudgetManager

if TYPE_CHECKING:
    from core.config import Settings

log = get_logger("action_queue")


@dataclass
class DeferredAction:
    """A proactive action waiting for budget availability."""

    id: str  # unique identifier
    agent_name: str
    action_type: ActionType
    action_data: dict[str, Any]  # whatever the action needs to execute
    callback: Callable[[], Coroutine[Any, Any, None]]  # async function to execute

    created_at: float = field(default_factory=time.time)
    retry_count: int = 0
    last_attempt: float | None = None


class ActionQueue:
    """
    Queue for deferred actions with automatic retry logic.

    Monitors budget availability and retries actions when slots open up.
    """

    def __init__(self, budget: BudgetManager, settings: "Settings") -> None:
        self._budget = budget
        self._max_retries = settings.budget_max_retry_attempts
        self._check_interval = 2.0  # seconds between budget availability checks

        self._queue: list[DeferredAction] = []
        self._running = False
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the queue processor."""
        self._running = True
        self._task = asyncio.create_task(self._process_loop())
        log.info("Action queue started", event="queue_start")

    async def stop(self) -> None:
        """Stop the queue processor gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info(
            "Action queue stopped",
            event="queue_stop",
            remaining=len(self._queue),
        )

    async def enqueue(
        self,
        agent_name: str,
        action_data: dict[str, Any],
        callback: Callable[[], Coroutine[Any, Any, None]],
        action_type: ActionType = ActionType.PROACTIVE,
    ) -> str:
        """
        Add an action to the deferred queue.

        Returns the action ID for tracking.
        """
        action_id = f"{agent_name}_{int(time.time() * 1000)}"

        action = DeferredAction(
            id=action_id,
            agent_name=agent_name,
            action_type=action_type,
            action_data=action_data,
            callback=callback,
        )

        async with self._lock:
            self._queue.append(action)

        log.info(
            "Action deferred",
            event="action_deferred",
            action_id=action_id,
            agent=agent_name,
            queue_size=len(self._queue),
        )

        return action_id

    def get_queue_status(self) -> dict:
        """Get current queue status for monitoring."""
        return {
            "queue_size": len(self._queue),
            "actions": [
                {
                    "id": a.id,
                    "agent": a.agent_name,
                    "retry_count": a.retry_count,
                    "created_at": a.created_at,
                }
                for a in self._queue
            ],
        }

    async def _process_loop(self) -> None:
        """Background loop that processes deferred actions."""
        while self._running:
            try:
                await asyncio.sleep(self._check_interval)
                await self._process_ready_actions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(
                    "Queue processing error",
                    event="queue_error",
                    error=str(e),
                )

    async def _process_ready_actions(self) -> None:
        """Check and process actions that can now proceed."""
        async with self._lock:
            # Process actions in order
            ready_actions = []
            remaining = []

            for action in self._queue:
                if self._budget.check_budget(action.agent_name, action.action_type):
                    ready_actions.append(action)
                else:
                    remaining.append(action)

            self._queue = remaining

        # Execute ready actions
        for action in ready_actions:
            await self._execute_action(action)

    async def _execute_action(self, action: DeferredAction) -> None:
        """Execute a deferred action with retry logic."""
        log.info(
            "Attempting deferred action",
            event="deferred_action_attempt",
            action_id=action.id,
            agent=action.agent_name,
            retry=action.retry_count,
        )

        try:
            # Record start for budget tracking
            start_time = self._budget.record_action_start(
                action.agent_name, action.action_type
            )

            # Execute the action
            await action.callback()

            # Record completion
            self._budget.record_action_end(
                action.agent_name,
                action.action_type,
                start_time,
            )

            log.info(
                "Deferred action succeeded",
                event="deferred_action_success",
                action_id=action.id,
                agent=action.agent_name,
            )

        except Exception as e:
            log.warning(
                "Deferred action failed",
                event="deferred_action_failed",
                action_id=action.id,
                agent=action.agent_name,
                error=str(e),
            )

            # Check retry count
            action.retry_count += 1
            action.last_attempt = time.time()

            if action.retry_count >= self._max_retries:
                log.error(
                    "Deferred action max retries exceeded",
                    event="deferred_action_dropped",
                    action_id=action.id,
                    agent=action.agent_name,
                    retries=action.retry_count,
                )
                # Action is already removed from queue, just log
            else:
                # Re-queue for another attempt
                async with self._lock:
                    self._queue.append(action)

                log.info(
                    "Deferred action re-queued",
                    event="deferred_action_requeued",
                    action_id=action.id,
                    agent=action.agent_name,
                    retry=action.retry_count,
                )
