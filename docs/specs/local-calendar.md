# Spec: Local Calendar (`feature/local-calendar`)

Status: DRAFT — awaiting review before any implementation.
Branch: `feature/local-calendar` off `master`. Master untouched.

## 1. Goal

One lightweight, local calendar for the business agent that consolidates:

1. **Real events** from the user's Outlook Calendar (the only calendar the user
   actually uses), ingested read-only via Outlook's private ICS feed.
2. **Agent-created events** — events the business agent makes, which currently
   go through Composio into a Google Calendar the user never opens.

Outcome: the agent can answer "what's on today / this week" without opening
Outlook, and every event lives in one place instead of being scattered across
Google Calendar (agent) and Outlook (human).

## 2. Non-goals

- **No two-way sync.** Writes never go back to Outlook. It is a mirror, not an
  integration.
- **No auto-scheduling engine.** The user is the scheduler (weekend population).
  Motion's planner concept is deliberately skipped.
- **No new external dependencies.** No Composio for calendar, no Microsoft
  Graph, no OAuth dance. Stdlib + one ICS parsing package at most.
- **No task/deadline cross-checking in this branch.** Deadline awareness is a
  noted future extension (see §7); out of scope here.

## 3. Storage — `memory/calendar.db` (new, dedicated)

Single table:

```sql
CREATE TABLE events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    start_utc    TEXT NOT NULL,          -- ISO 8601 UTC
    end_utc      TEXT NOT NULL,          -- ISO 8601 UTC
    description  TEXT NOT NULL DEFAULT '',
    kind         TEXT NOT NULL DEFAULT 'event',   -- 'event' | 'block'
    priority     INTEGER NOT NULL DEFAULT 0,
    source       TEXT NOT NULL,                   -- 'outlook' | 'cli' | 'agent'
    outlook_uid  TEXT,                            -- ICS UID for dedupe/upsert; NULL for local events
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX idx_events_start ON events(start_utc);
CREATE UNIQUE INDEX idx_events_outlook_uid ON events(outlook_uid) WHERE outlook_uid IS NOT NULL;
```

Rationale:
- Separate DB from `scheduler.db` / `sessions.db` so the calendar can be wiped
  or re-synced without touching agent state.
- `source` doubles as the Motion "created_by" concept (user vs agent vs mirror).
- `outlook_uid` + partial unique index makes the ICS sync idempotent: re-running
  the sync upserts, never duplicates.
- `kind` / `priority` keep the Motion timeblocking data model so later reasoning
  ("what's my week", "what's at risk") is possible without a migration.
- `updated_at` supports the future replan-on-drift check (stale blocks).

## 4. Ingestion — Outlook ICS feed (read-only)

- **One-time setup:** user copies the private ICS subscribe URL from Outlook
  (Outlook.com → Calendar → Share/Publish → ICS link). Stored in `.env` as
  `OUTLOOK_ICS_URL`. It is a bearer secret — never committed, never logged.
- `scripts/calendar_sync_outlook.py`:
  - Fetches the ICS over plain HTTPS.
  - Expands recurring events into concrete occurrences for a window
    (default −30 to +90 days around today). Handles RRULE/EXDATE via the
    `recurring-icalendarlib` package (pinned in requirements).
  - Upserts by `outlook_uid`: new events inserted, changed ones updated,
    vanished ones (outside the recurrence window) left untouched.
  - Prints a summary: added / updated / unchanged counts.
- **Fallback:** if the account does not expose the ICS publish option (some
  org-managed M365 tenants disable it), the script also accepts a path to a
  manually exported `.ics` file. Same parser, same upsert path.
- Sync is on-demand (CLI flag), not scheduled — the user populates on weekends
  and can re-sync whenever they like.

## 5. Tool — `LocalCalendarTool` (replaces the Composio wrapper)

`agents/business/tools/calendar.py` swaps `CalendarTool` for
`LocalCalendarTool` with **compatible signatures**:

| Method | Signature | Notes |
| --- | --- | --- |
| `list_events` | `async list_events(max_results=10, *, start_utc=None, end_utc=None)` | Range filter is additive keyword args with defaults — existing callers and tests unchanged. |
| `create_event` | `async create_event(title, start, end, description="")` | Writes to SQLite, `source='agent'`, returns `{"id": ...}`. |
| `block_time` | `async block_time(title, start, end)` | Kept per user decision (project completeness), even though the user won't personally use it. |

- Storage backend: stdlib `sqlite3`, WAL mode, single writer.
- Composio stays in `core/` for Gmail and anything else that still earns its
  keep. Only the calendar tool detaches.
- `BusinessTools` wiring changes only in which tool class it instantiates;
  all existing tests mock `calendar` and keep passing.

## 6. Population — CLI first, Telegram later

- `scripts/calendar_populate.py`:
  - `add` / `list` / `edit` / `delete` subcommands for local events
    (`source='cli'`).
  - `--sync-outlook` runs the ICS ingestion from §4.
  - `--week` / `--day` views for the user's own weekend review.
- Phase 2 (separate follow-up, not this branch): Telegram command
  (`/cal today`, `/cal week`, `/cal add ...`) so the user never needs the shell.

## 7. Future extensions (noted, not built here)

- **Deadline awareness** (Motion concept): when the agent has tasks with
  deadlines, cross-check them against the week's blocks and warn when a task
  can't fit. Cheap to add once tasks exist in the agent's world.
- **Replan-on-drift** (Motion concept): flag events whose time has passed but
  whose `updated_at` suggests they were never acted on; propose a re-layout.
  Supported by the `source` / `updated_at` columns now, built later.
- **Daily planning ritual** (Sunsama concept): a deliberate start-of-day and
  end-of-day session — agent proposes today's realistic workload from the
  calendar, user confirms; shutdown review catches what slipped. Maps to a
  future Telegram command (`/cal plan`, `/cal shutdown`), extending the
  weekend-population habit to a daily cadence.

## 8. Tests

- Tool CRUD round-trips against a temp SQLite DB.
- `block_time` prefixing and kind='block' persistence.
- ICS sync: recurrence expansion, dedupe/upsert by UID, EXDATE handling,
  vanished-event behaviour.
- Existing test suite (all calendar mocks) passes unchanged.

## 9. Migration & revert

- No data migration needed: the Google-backed tool produced no durable data the
  user cares about (calendar they never open).
- Revert = delete branch + `rm memory/calendar.db`. Nothing on master changes.
