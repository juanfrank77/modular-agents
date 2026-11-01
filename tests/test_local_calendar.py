"""
test_local_calendar.py
----------------------
Tests for the local SQLite calendar (feature/local-calendar):
  - agents/business/tools/calendar.py  (LocalCalendarTool CRUD)
  - scripts/calendar_sync_outlook.py   (ICS parsing, recurrence expansion,
                                        UID dedupe/upsert)

All tests run against a temporary SQLite DB — no network, no Composio.

Run:
    python -m pytest tests/test_local_calendar.py -x -q
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agents.business.tools.calendar import LocalCalendarTool, connect_sync
from scripts.calendar_sync_outlook import parse_ics_bytes, upsert_events

# Fixed "now" so the -30/+90 day sync window is deterministic:
# window = 2026-03-06 .. 2026-07-04
NOW = datetime(2026, 4, 5, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def db(tmp_path):
    """Return the path to a fresh calendar DB for the test."""
    return str(tmp_path / "calendar.db")


@pytest.fixture()
def tool(db):
    """Return a LocalCalendarTool bound to a fresh DB."""
    return LocalCalendarTool(db_path=db)


def _ics(*vevents: str) -> bytes:
    """Wrap VEVENT blocks in a minimal VCALENDAR envelope."""
    return (
        "BEGIN:VCALENDAR\r\n"
        "PRODID:-//Test//EN\r\n"
        "VERSION:2.0\r\n" + "".join(vevents) + "END:VCALENDAR\r\n"
    ).encode()


def _vevent(
    uid: str,
    summary: str,
    dtstart: str,
    dtend: str,
    *,
    rrule: str | None = None,
    exdate: str | None = None,
    description: str = "",
) -> str:
    """Build a VEVENT block, optionally recurring with exclusions."""
    parts = [
        "BEGIN:VEVENT\r\n",
        f"UID:{uid}\r\n",
        "DTSTAMP:20260401T000000Z\r\n",
        f"DTSTART:{dtstart}\r\n",
        f"DTEND:{dtend}\r\n",
        f"SUMMARY:{summary}\r\n",
    ]
    if description:
        parts.append(f"DESCRIPTION:{description}\r\n")
    if rrule:
        parts.append(f"RRULE:{rrule}\r\n")
    if exdate:
        parts.append(f"EXDATE:{exdate}\r\n")
    parts.append("END:VEVENT\r\n")
    return "".join(parts)


# ---------------------------------------------------------------------------
# LocalCalendarTool — CRUD round-trips
# ---------------------------------------------------------------------------


class TestLocalCalendarToolList:
    @pytest.mark.asyncio
    async def test_list_events_empty_db(self, tool):
        result = await tool.list_events()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_events_round_trip(self, tool):
        await tool.create_event(
            title="Team Sync",
            start="2026-04-05T10:00:00Z",
            end="2026-04-05T11:00:00Z",
            description="Weekly check-in",
        )
        events = await tool.list_events()
        assert len(events) == 1
        ev = events[0]
        assert ev["title"] == "Team Sync"
        assert ev["start_utc"] == "2026-04-05T10:00:00Z"
        assert ev["end_utc"] == "2026-04-05T11:00:00Z"
        assert ev["description"] == "Weekly check-in"
        assert ev["source"] == "agent"
        assert ev["kind"] == "event"
        assert ev["outlook_uid"] is None

    @pytest.mark.asyncio
    async def test_list_events_ordered_by_start(self, tool):
        await tool.create_event(
            title="Later", start="2026-04-06T15:00:00Z", end="2026-04-06T16:00:00Z"
        )
        await tool.create_event(
            title="Earlier", start="2026-04-06T09:00:00Z", end="2026-04-06T10:00:00Z"
        )
        events = await tool.list_events()
        assert [e["title"] for e in events] == ["Earlier", "Later"]

    @pytest.mark.asyncio
    async def test_list_events_range_filter(self, tool):
        await tool.create_event(
            title="In range", start="2026-04-06T10:00:00Z", end="2026-04-06T11:00:00Z"
        )
        await tool.create_event(
            title="Before", start="2026-04-02T10:00:00Z", end="2026-04-02T11:00:00Z"
        )
        await tool.create_event(
            title="After", start="2026-04-10T10:00:00Z", end="2026-04-10T11:00:00Z"
        )
        events = await tool.list_events(
            start_utc="2026-04-05T00:00:00Z", end_utc="2026-04-08T00:00:00Z"
        )
        assert [e["title"] for e in events] == ["In range"]

    @pytest.mark.asyncio
    async def test_list_events_max_results(self, tool):
        for i in range(5):
            await tool.create_event(
                title=f"Event {i}",
                start=f"2026-04-0{i + 1}T10:00:00Z",
                end=f"2026-04-0{i + 1}T11:00:00Z",
            )
        events = await tool.list_events(max_results=2)
        assert len(events) == 2


class TestLocalCalendarToolCreate:
    @pytest.mark.asyncio
    async def test_create_event_returns_id(self, tool):
        result = await tool.create_event(
            title="Sprint Planning",
            start="2026-04-05T10:00:00Z",
            end="2026-04-05T11:00:00Z",
            description="Q2 sprint",
        )
        assert isinstance(result["id"], int)
        assert result["id"] > 0

    @pytest.mark.asyncio
    async def test_create_event_default_description(self, tool):
        await tool.create_event(
            title="Standup",
            start="2026-04-05T09:00:00Z",
            end="2026-04-05T09:15:00Z",
        )
        events = await tool.list_events()
        assert events[0]["description"] == ""


class TestLocalCalendarToolBlockTime:
    @pytest.mark.asyncio
    async def test_block_time_prefixes_title(self, tool):
        result = await tool.block_time(
            title="Deep Work",
            start="2026-04-05T14:00:00Z",
            end="2026-04-05T16:00:00Z",
        )
        assert isinstance(result["id"], int)
        events = await tool.list_events()
        assert events[0]["title"] == "Blocked: Deep Work"

    @pytest.mark.asyncio
    async def test_block_time_persists_kind_block(self, tool):
        await tool.block_time(
            title="Focus", start="2026-04-05T14:00:00Z", end="2026-04-05T16:00:00Z"
        )
        events = await tool.list_events()
        assert events[0]["kind"] == "block"
        assert events[0]["source"] == "agent"

    @pytest.mark.asyncio
    async def test_block_and_event_coexist(self, tool):
        await tool.create_event(
            title="Meeting", start="2026-04-05T10:00:00Z", end="2026-04-05T11:00:00Z"
        )
        await tool.block_time(
            title="Focus", start="2026-04-05T14:00:00Z", end="2026-04-05T16:00:00Z"
        )
        events = await tool.list_events()
        kinds = {e["title"]: e["kind"] for e in events}
        assert kinds["Meeting"] == "event"
        assert kinds["Blocked: Focus"] == "block"


class TestLocalCalendarSchema:
    def test_connect_sync_is_idempotent(self, db):
        """Calling connect_sync repeatedly must not fail (CREATE IF NOT EXISTS)."""
        connect_sync(db)
        connect_sync(db)
        conn = connect_sync(db)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert "events" in tables

    def test_outlook_uid_unique_index(self, db):
        """Two outlook rows with the same UID must violate the unique index."""
        conn = connect_sync(db)
        conn.execute(
            "INSERT INTO events (title, start_utc, end_utc, description, kind, "
            "priority, source, outlook_uid, created_at, updated_at) "
            "VALUES ('A', '2026-04-05T10:00:00Z', '2026-04-05T11:00:00Z', '', "
            "'event', 0, 'outlook', 'uid-1', '2026-04-01', '2026-04-01')"
        )
        conn.commit()
        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO events (title, start_utc, end_utc, description, kind, "
                "priority, source, outlook_uid, created_at, updated_at) "
                "VALUES ('B', '2026-04-06T10:00:00Z', '2026-04-06T11:00:00Z', '', "
                "'event', 0, 'outlook', 'uid-1', '2026-04-01', '2026-04-01')"
            )
        conn.close()


# ---------------------------------------------------------------------------
# ICS parsing (scripts/calendar_sync_outlook.parse_ics_bytes)
# ---------------------------------------------------------------------------


class TestParseIcs:
    def test_single_event_parsed(self):
        raw = _ics(
            _vevent(
                "evt-1@test",
                "Team Sync",
                "20260410T100000Z",
                "20260410T110000Z",
                description="Weekly check-in",
            )
        )
        events = parse_ics_bytes(raw, now=NOW)
        assert len(events) == 1
        ev = events[0]
        assert ev["outlook_uid"] == "evt-1@test"
        assert ev["title"] == "Team Sync"
        assert ev["start_utc"] == "2026-04-10T10:00:00+00:00"
        assert ev["end_utc"] == "2026-04-10T11:00:00+00:00"
        assert ev["description"] == "Weekly check-in"
        assert ev["source"] == "outlook"

    def test_event_outside_window_excluded(self):
        raw = _ics(
            _vevent(
                "old@test", "Ancient", "20260101T100000Z", "20260101T110000Z"
            )
        )
        events = parse_ics_bytes(raw, now=NOW)
        assert events == []

    def test_recurring_event_expanded(self):
        raw = _ics(
            _vevent(
                "recurring@test",
                "Weekly Standup",
                "20260406T090000Z",
                "20260406T091500Z",
                rrule="FREQ=WEEKLY;COUNT=4",
            )
        )
        events = parse_ics_bytes(raw, now=NOW)
        starts = [e["start_utc"] for e in events]
        assert starts == [
            "2026-04-06T09:00:00+00:00",
            "2026-04-13T09:00:00+00:00",
            "2026-04-20T09:00:00+00:00",
            "2026-04-27T09:00:00+00:00",
        ]
        # Every occurrence shares the same outlook_uid.
        assert {e["outlook_uid"] for e in events} == {"recurring@test"}

    def test_recurring_event_exdate_excludes_occurrence(self):
        raw = _ics(
            _vevent(
                "recurring@test",
                "Weekly Standup",
                "20260406T090000Z",
                "20260406T091500Z",
                rrule="FREQ=WEEKLY;COUNT=4",
                exdate="20260413T090000Z",
            )
        )
        events = parse_ics_bytes(raw, now=NOW)
        starts = [e["start_utc"] for e in events]
        assert starts == [
            "2026-04-06T09:00:00+00:00",
            "2026-04-20T09:00:00+00:00",
            "2026-04-27T09:00:00+00:00",
        ]

    def test_invalid_ics_raises(self):
        with pytest.raises(ValueError, match="Could not parse"):
            parse_ics_bytes(b"not an ics feed", now=NOW)

    def test_events_without_uid_skipped(self):
        raw = (
            "BEGIN:VCALENDAR\r\n"
            "PRODID:-//Test//EN\r\n"
            "VERSION:2.0\r\n"
            "BEGIN:VEVENT\r\n"
            "DTSTAMP:20260401T000000Z\r\n"
            "DTSTART:20260410T100000Z\r\n"
            "DTEND:20260410T110000Z\r\n"
            "SUMMARY:No UID\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        ).encode()
        events = parse_ics_bytes(raw, now=NOW)
        assert events == []


# ---------------------------------------------------------------------------
# Upsert dedupe (scripts/calendar_sync_outlook.upsert_events)
# ---------------------------------------------------------------------------


class TestUpsertEvents:
    def _event(self, uid: str, title: str = "Team Sync", start: str = "2026-04-10T10:00:00+00:00"):
        return {
            "outlook_uid": uid,
            "title": title,
            "start_utc": start,
            "end_utc": "2026-04-10T11:00:00+00:00",
            "description": "",
            "source": "outlook",
        }

    def test_insert_new_event(self, db):
        summary = upsert_events([self._event("u1")], db)
        assert summary == {"added": 1, "updated": 0, "unchanged": 0, "total": 1}

    def test_resync_is_idempotent(self, db):
        upsert_events([self._event("u1")], db)
        summary = upsert_events([self._event("u1")], db)
        assert summary == {"added": 0, "updated": 0, "unchanged": 1, "total": 1}
        conn = connect_sync(db)
        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        conn.close()
        assert count == 1

    def test_changed_event_updates_in_place(self, db):
        upsert_events([self._event("u1")], db)
        moved = self._event("u1", title="Team Sync (moved)", start="2026-04-11T10:00:00+00:00")
        summary = upsert_events([moved], db)
        assert summary == {"added": 0, "updated": 1, "unchanged": 0, "total": 1}
        conn = connect_sync(db)
        row = conn.execute("SELECT title, start_utc FROM events").fetchone()
        conn.close()
        assert row["title"] == "Team Sync (moved)"
        assert row["start_utc"] == "2026-04-11T10:00:00+00:00"

    def test_vanished_events_left_untouched(self, db):
        """Events absent from a later sync must NOT be deleted."""
        upsert_events([self._event("u1"), self._event("u2")], db)
        upsert_events([self._event("u1")], db)  # u2 vanished from the feed
        conn = connect_sync(db)
        uids = {row["outlook_uid"] for row in conn.execute("SELECT outlook_uid FROM events")}
        conn.close()
        assert uids == {"u1", "u2"}

    @pytest.mark.asyncio
    async def test_local_events_survive_sync(self, db):
        """Agent/CLI-created events (no outlook_uid) are never touched by sync."""
        tool = LocalCalendarTool(db_path=db)
        await tool.create_event(
            title="Local Note", start="2026-04-10T10:00:00Z", end="2026-04-10T11:00:00Z"
        )
        summary = upsert_events([self._event("u1")], db)
        assert summary["added"] == 1
        conn = connect_sync(db)
        rows = conn.execute("SELECT title, source FROM events ORDER BY id").fetchall()
        conn.close()
        titles = {r["title"]: r["source"] for r in rows}
        assert titles["Local Note"] == "agent"
        assert titles["Team Sync"] == "outlook"
