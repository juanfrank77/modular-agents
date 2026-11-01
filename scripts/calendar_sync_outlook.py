"""scripts/calendar_sync_outlook.py
-----------------------------------
One-way ingestion of the user's Outlook Calendar into the local SQLite
calendar via Outlook's private ICS subscribe feed.

Usage:
    python scripts/calendar_sync_outlook.py [--db memory/calendar.db]

Setup (one-time):
    Add the private ICS URL to ``.env`` as OUTLOOK_ICS_URL, or pass it via
    ``--ics-url``. It is a bearer secret — never commit it.

Behaviour:
    - Fetches the ICS feed over plain HTTPS (no OAuth, no Composio).
    - Expands recurring events (RRULE) into concrete occurrences for a
      -30 / +90 day window around today.
    - Upserts by the ICS UID: re-running the sync never duplicates rows;
      changed events are updated in place, vanished ones are left untouched.
    - Prints a summary: added / updated / unchanged counts.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Make the repo root importable when running from scripts/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from dateutil.rrule import rrulestr
from icalendar import Calendar

from agents.business.tools.calendar import connect_sync

DT_FORMAT = "%Y%m%dT%H%M%SZ"

# Default range: look back 30 days, look forward 90 days.
LOOKBACK_DAYS = 30
LOOKAHEAD_DAYS = 90


def _to_utc_iso(dt: datetime) -> str:
    """Normalise any datetime (naive or aware) to an ISO 8601 UTC string."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _parse_date_value(raw: Any) -> datetime | None:
    """Parse an icalendar vDatetime / vDate value into a datetime."""
    try:
        parsed = raw.dt if hasattr(raw, "dt") else raw
        if isinstance(parsed, datetime):
            return parsed
        return None
    except Exception:
        return None


def _expand_event_instances(
    comp: Any, window_start: datetime, window_end: datetime
) -> list[tuple[datetime, datetime]]:
    """Return concrete (start, end) occurrences for one VEVENT within the window.

    Non-recurring events yield a single occurrence (if it intersects the
    window). Recurring events are expanded via dateutil.rrule, applying
    EXDATE exclusions.
    """
    dtstart = _parse_date_value(comp.get("dtstart"))
    if dtstart is None:
        return []

    duration: timedelta | None = None
    dtend = comp.get("dtend")
    if dtend is not None:
        end_dt = _parse_date_value(dtend)
        if end_dt is not None:
            duration = end_dt - dtstart

    # Collect EXDATE datetimes (may be a list; icalendar wraps values in
    # vDDDLists/vDDD types). Normalise everything to aware datetimes in the
    # same timezone as dtstart so the exclusion check can compare exactly.
    exdates: set[datetime] = set()
    exdate_values = comp.get("exdate")
    if exdate_values is not None:
        raw_list = exdate_values if isinstance(exdate_values, list) else [exdate_values]
        for item in raw_list:
            # vDDDLists exposes .dts (a list of vDDD); a bare vDDD exposes .dt.
            candidates = getattr(item, "dts", None)
            if candidates is None:
                candidates = [item]
            for cand in candidates:
                d = _parse_date_value(cand)
                if d is None:
                    continue
                if d.tzinfo is None:
                    d = d.replace(tzinfo=dtstart.tzinfo or timezone.utc)
                exdates.add(d)

    rrule_prop = comp.get("rrule")
    if rrule_prop is None:
        # Single occurrence.
        if dtstart >= window_end or dtstart < window_start:
            # Allow events that started before the window but are still running.
            if duration is not None and dtstart < window_start:
                if (dtstart + duration) < window_start:
                    return []
            else:
                return []
        end_dt = dtstart + duration if duration is not None else dtstart
        return [(dtstart, end_dt)]

    # Recurring: expand with dateutil.
    try:
        rule = rrulestr(rrule_prop.to_ical().decode(), dtstart=dtstart)
    except Exception as e:
        print(f"  ! skipping event with unparseable RRULE: {e}", file=sys.stderr)
        return []

    if duration is None:
        duration = timedelta(hours=1)  # sensible default for zero-duration events

    occurrences: list[datetime] = []
    for occ in rule.between(window_start - duration, window_end):
        # rrule.between is inclusive on both ends; drop occurrences that
        # start beyond the window end.
        if occ > window_end:
            continue
        occ_tz = occ.replace(tzinfo=dtstart.tzinfo) if occ.tzinfo is None else occ
        # EXDATE matching is by exact datetime; exdates is a normalised set.
        if occ_tz in exdates:
            continue
        occurrences.append(occ_tz)

    return [(occ, occ + duration) for occ in occurrences]


def parse_ics_bytes(raw: bytes, now: datetime | None = None) -> list[dict[str, Any]]:
    """Parse an ICS feed body into flat event dicts ready for upsert.

    Returns dicts with keys: outlook_uid, title, start_utc, end_utc,
    description, source ('outlook').
    """
    if now is None:
        now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=LOOKBACK_DAYS)
    window_end = now + timedelta(days=LOOKAHEAD_DAYS)

    try:
        # icalendar 7.x expects a decoded str, not bytes.
        cal = Calendar.from_ical(raw.decode("utf-8", errors="replace"))
    except Exception as e:
        raise ValueError(f"Could not parse ICS feed: {e}") from e

    events: list[dict[str, Any]] = []
    for comp in cal.walk():
        if comp.name != "VEVENT":
            continue
        uid = str(comp.get("uid", "")).strip()
        if not uid:
            continue
        summary = str(comp.get("summary", "(untitled)"))
        description = str(comp.get("description", "") or "")
        occurrences = _expand_event_instances(comp, window_start, window_end)
        for start, end in occurrences:
            events.append(
                {
                    "outlook_uid": uid,
                    "title": summary,
                    "start_utc": _to_utc_iso(start),
                    "end_utc": _to_utc_iso(end),
                    "description": description,
                    "source": "outlook",
                }
            )
    return events


async def sync_ics(ics_url: str, db_path: str) -> dict[str, int]:
    """Fetch the ICS feed, parse it, and upsert into the local calendar DB.

    Returns a summary dict with added / updated / unchanged / total counts.
    """
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(ics_url)
        resp.raise_for_status()
        raw = resp.content

    parsed = parse_ics_bytes(raw)
    return upsert_events(parsed, db_path)


def upsert_events(events: list[dict[str, Any]], db_path: str) -> dict[str, int]:
    """Upsert parsed events into the local calendar DB, deduping by outlook_uid.

    New UIDs are inserted; existing UIDs are updated in place when any field
    changed, and counted as unchanged otherwise. Returns a summary dict with
    added / updated / unchanged / total counts.
    """
    now = datetime.now(timezone.utc).isoformat()

    conn = connect_sync(db_path)
    added = updated = unchanged = 0
    try:
        for ev in events:
            row = conn.execute(
                "SELECT id, title, start_utc, end_utc, description "
                "FROM events WHERE outlook_uid = ?",
                (ev["outlook_uid"],),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO events "
                    "(title, start_utc, end_utc, description, kind, priority, "
                    "source, outlook_uid, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, 'event', 0, 'outlook', ?, ?, ?)",
                    (
                        ev["title"],
                        ev["start_utc"],
                        ev["end_utc"],
                        ev["description"],
                        ev["outlook_uid"],
                        now,
                        now,
                    ),
                )
                added += 1
            elif (
                row["title"] != ev["title"]
                or row["start_utc"] != ev["start_utc"]
                or row["end_utc"] != ev["end_utc"]
                or row["description"] != ev["description"]
            ):
                conn.execute(
                    "UPDATE events SET title=?, start_utc=?, end_utc=?, "
                    "description=?, updated_at=? WHERE outlook_uid=?",
                    (
                        ev["title"],
                        ev["start_utc"],
                        ev["end_utc"],
                        ev["description"],
                        now,
                        ev["outlook_uid"],
                    ),
                )
                updated += 1
            else:
                unchanged += 1
        conn.commit()
    finally:
        conn.close()

    return {
        "added": added,
        "updated": updated,
        "unchanged": unchanged,
        "total": len(events),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Outlook ICS feed into local calendar DB.")
    parser.add_argument("--db", default="memory/calendar.db", help="Path to calendar SQLite DB.")
    parser.add_argument("--ics-url", default=None, help="Private Outlook ICS subscribe URL.")
    args = parser.parse_args()

    ics_url = args.ics_url or os.environ.get("OUTLOOK_ICS_URL")
    if not ics_url:
        print(
            "Error: no ICS URL. Set OUTLOOK_ICS_URL in .env or pass --ics-url.",
            file=sys.stderr,
        )
        sys.exit(1)

    summary = asyncio.run(sync_ics(ics_url, args.db))
    print(
        f"Sync complete: {summary['added']} added, {summary['updated']} updated, "
        f"{summary['unchanged']} unchanged ({summary['total']} occurrences in window)."
    )


if __name__ == "__main__":
    main()
