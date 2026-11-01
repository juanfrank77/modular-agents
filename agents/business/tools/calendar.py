"""
agents/business/tools/calendar.py
-----------------------------------
Local SQLite-backed calendar for the Business agent.

Replaces the Composio Google Calendar wrapper with a lightweight, local
store that consolidates:

1. ``outlook``  — real events mirrored read-only from the user's Outlook
                  Calendar via its private ICS feed (see
                  ``scripts/calendar_sync_outlook.py``).
2. ``cli``      — events added by the user with ``scripts/calendar_populate.py``.
3. ``agent``    — events the Business agent itself creates.

Usage:
    from agents.business.tools.calendar import LocalCalendarTool

    cal = LocalCalendarTool(db_path="memory/calendar.db")

    events = await cal.list_events()
    result = await cal.create_event(
        title="Team Sync",
        start="2026-04-05T10:00:00Z",
        end="2026-04-05T11:00:00Z",
        description="Weekly check-in",
    )
    block  = await cal.block_time(
        title="Deep Work",
        start="2026-04-05T14:00:00Z",
        end="2026-04-05T16:00:00Z",
    )

Writes never go back to Outlook — this is a one-way mirror, not an
integration. No Composio, no OAuth, no network calls on the tool path.
"""

from __future__ import annotations

import sqlite3

import aiosqlite
from core.logger import get_logger

log = get_logger("calendar_tool")

# Single source of truth for the schema; scripts and tests reuse it.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    start_utc    TEXT NOT NULL,
    end_utc      TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    kind         TEXT NOT NULL DEFAULT 'event',
    priority     INTEGER NOT NULL DEFAULT 0,
    source       TEXT NOT NULL,
    outlook_uid  TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(start_utc);
-- One row per *occurrence*: a recurring series expanded by the ICS sync
-- shares one outlook_uid across its instances, so the unique key must be
-- the (uid, start) pair — not the uid alone.
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_outlook_occurrence
    ON events(outlook_uid, start_utc) WHERE outlook_uid IS NOT NULL;
"""


def connect_sync(db_path: str) -> sqlite3.Connection:
    """Open a blocking connection with the schema applied and WAL enabled."""
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


class LocalCalendarTool:
    """Local calendar operations backed by SQLite.

    Args:
        db_path: Path to the calendar SQLite database
                 (``memory/calendar.db`` by convention).
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        # Apply the schema eagerly so the DB exists even before the first call.
        connect_sync(self.db_path)
        log.debug(
            "LocalCalendarTool initialised",
            event="calendar_init",
            db_path=self.db_path,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def list_events(
        self,
        max_results: int = 10,
        *,
        start_utc: str | None = None,
        end_utc: str | None = None,
    ) -> list[dict]:
        """Fetch events ordered by start time.

        Args:
            max_results: Maximum number of events to return.  Defaults to 10.
            start_utc: Optional ISO 8601 lower bound (inclusive).
            end_utc: Optional ISO 8601 upper bound (exclusive).

        Returns:
            List of event dicts, or a single-item list containing an error
            dict if the read fails.
        """
        log.debug(
            "list_events",
            event="calendar_list_events",
            max_results=max_results,
            start_utc=start_utc,
            end_utc=end_utc,
        )
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = sqlite3.Row
                await db.executescript(SCHEMA_SQL)
                clauses = ["1=1"]
                params: list[object] = []
                if start_utc is not None:
                    clauses.append("start_utc >= ?")
                    params.append(start_utc)
                if end_utc is not None:
                    clauses.append("start_utc < ?")
                    params.append(end_utc)
                query = (
                    "SELECT id, title, start_utc, end_utc, description, kind, "
                    "priority, source, outlook_uid, created_at, updated_at "
                    f"FROM events WHERE {' AND '.join(clauses)} "
                    "ORDER BY start_utc ASC LIMIT ?"
                )
                params.append(max_results)
                async with db.execute(query, params) as cursor:
                    rows = await cursor.fetchall()
                    events = [dict(row) for row in rows]
            log.debug(
                "list_events complete",
                event="calendar_list_events_done",
                count=len(events),
            )
            return events
        except Exception as e:
            log.warning(
                "list_events failed",
                event="calendar_list_events_error",
                error=str(e),
            )
            return [{"error": str(e)}]

    async def _create_event_raw(
        self,
        title: str,
        start: str,
        end: str,
        description: str = "",
        *,
        kind: str = "event",
        source: str = "agent",
    ) -> dict:
        """Insert one event and return ``{"id": ...}`` on success."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(SCHEMA_SQL)
            cursor = await db.execute(
                "INSERT INTO events "
                "(title, start_utc, end_utc, description, kind, priority, "
                "source, outlook_uid, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (title, start, end, description, kind, 0, source, now, now),
            )
            await db.commit()
            new_id = cursor.lastrowid
        return {"id": new_id}

    async def create_event(
        self,
        title: str,
        start: str,
        end: str,
        description: str = "",
    ) -> dict:
        """Create a new event on the local calendar.

        Args:
            title: Event title / summary.
            start: Start datetime in ISO 8601 format
                   (e.g. ``"2026-04-05T10:00:00Z"``).
            end: End datetime in ISO 8601 format.
            description: Optional event description.  Defaults to ``""``.

        Returns:
            Result dict (contains ``"id"`` on success, or ``"error"`` on
            failure).
        """
        log.debug(
            "create_event",
            event="calendar_create_event",
            title=title,
            start=start,
            end=end,
        )
        try:
            result = await self._create_event_raw(title, start, end, description)
            log.debug(
                "create_event complete",
                event="calendar_create_event_done",
                title=title,
                event_id=result.get("id"),
            )
            return result
        except Exception as e:
            log.warning(
                "create_event failed",
                event="calendar_create_event_error",
                title=title,
                error=str(e),
            )
            return {"error": str(e)}

    async def block_time(self, title: str, start: str, end: str) -> dict:
        """Block off time on the local calendar with a prefixed title.

        Creates an event titled ``"Blocked: <title>"`` with ``kind='block'``
        so the time should not be scheduled over.

        Args:
            title: Description of what the block is for.
            start: Start datetime in ISO 8601 format.
            end: End datetime in ISO 8601 format.

        Returns:
            Result dict (contains ``"id"`` on success, or ``"error"`` on
            failure).
        """
        blocked_title = f"Blocked: {title}"
        log.debug(
            "block_time",
            event="calendar_block_time",
            title=blocked_title,
            start=start,
            end=end,
        )
        try:
            result = await self._create_event_raw(
                blocked_title, start, end, kind="block"
            )
            log.debug(
                "block_time complete",
                event="calendar_block_time_done",
                title=blocked_title,
                event_id=result.get("id"),
            )
            return result
        except Exception as e:
            log.warning(
                "block_time failed",
                event="calendar_block_time_error",
                title=blocked_title,
                error=str(e),
            )
            return {"error": str(e)}
