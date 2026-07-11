# SKILL: email-digest

## Trigger
Email digest, weekly digest, what did I miss, anything relevant in my
email, scan my inbox for [topic], anything for [project] this week,
email report, [N] day email recap, last 7 days of email

## Purpose
Surface emails relevant to the user's current work over a configurable
time window. Sibling of email-triage: triage optimises for inbox-zero
velocity (unread, urgent vs noise); digest optimises for relevance to a
defined reader profile (read or unread, deep, time-windowed). Read-only.
On-demand only — no scheduling.

## Parameters
- `days` (integer, default 7) — look back this many days
- `folder` (string, default "INBOX") — IMAP folder to read from
- `project_filter` (string, optional) — scope relevance to one project

## Reader profile (the lens)
Driven by `memory/context/reader_profile.md` (per-installation,
gitignored). It defines current focus, active projects, a "Watching for"
keyword list (drives the pre-filter), and an "Ignore" list.

The profile is the **primary lens**. `projects.md` is the fallback. If
the profile is missing, ask the user to set it up before running — do
not run with a default profile.

## When to use this vs email-triage
This skill and `email-triage` are siblings — different jobs in a session:
- **email-digest** answers "what's relevant to my work?" Read-only.
  Surfaces, scores, and saves a digest. Does not modify the inbox.
- **email-triage** answers "what needs my attention right now?"
  Proposes inbox actions (mark read, archive, draft reply) and waits
  for explicit approval before any change.

Typical workflow: run email-digest to see what's relevant, then
email-triage to process the rest of the inbox. The two skills are
composed in a session, not invoked together. The digest's auto-save
file is the durable record of relevance; the triage session's
approved actions are the durable record of inbox movement.

## Pipeline
1. Load the reader profile. If absent, stop and ask.
2. Read emails from `folder` (default INBOX) within last `days`.
3. **Subject pre-filter** — keep top ~30 by keyword match against
   "Watching for". Sender on known contact list with ambiguous subject
   still passes through.
4. **Body extraction** — for each candidate, extract items in any of
   the five Hits categories (see legend below). Pick the most relevant
   1-3 hits per email — do not try to fill all five.
5. **Relevance scoring** — 1-5 against the reader profile + active
   projects, with a 1-line justification citing the matched focus
   or project by name.
6. Drop anything below threshold (default 3).
7. Group by project, lead with the highest-relevance project.
8. **Present cards in chat** (cap 8). If more remain: "Want the rest?
   — N more matches below the cut."
9. **Auto-save** the full digest to
   `memory/solutions/<YYYY-MM-DD>-email-digest.md` with all hits
   above threshold.
10. On save, silently prune any `memory/solutions/*-email-digest.md`
    older than 84 days (12 weeks). No notice to user.

## Hits legend
Each emoji marks a specific category. Use the most relevant 1-3 per card:
- 🔗 **link** — a real URL from the email body
- 📊 **metric** — a specific number, percentage, or stat (with units/context)
- 🛠 **tool** — a product, service, or framework named
- 💡 **tactic** — a specific approach, method, or play described
- 📰 **signal** — a market move, trend, or news item

## Output format (chat)

```
📨 [Topic — 3-5 words, action-oriented]
  Project: [name] ([priority] — [one-line status])
  What: [one line — the substance]
  Hits: 🔗 [URL]  📊 [metric]  🛠 [product]  💡 [method]  📰 [news]
  Source: [sender] · [YYYY-MM-DD] · [clickable link to email]
```

Group cards under project headers, highest-relevance project first.
If no hits: "No relevant emails in the last N days against your reader
profile."

## Auto-save file

Path: `memory/solutions/<YYYY-MM-DD>-email-digest.md`

```markdown
# Email Digest — YYYY-MM-DD
Window: last N days · Folder: <folder> · Reader profile: <updated>

## <Project name>

### [Topic — 3-5 words]
  What: [...]
  Hits: [full extract — links, metrics, tools, tactics, signals]
  Source: [sender] · [subject] · [date] · [permalink]
  Relevance: [1-5] — [justification citing matched focus/project]
```

## Handoff
Reply requests on surfaced emails hand off to `email-triage`. The digest
is read-only and never proposes sends.

## Rules
- Read-only. Never modify the inbox, archive, label, mark-as-read, or
  delete any email as part of this skill.
- Never send, forward, or reply. Use email-triage for replies.
- If `reader_profile.md` is missing or empty, ask before running.
- If `project_filter` is set, only show hits matching that project —
  do not surface unrelated high-relevance items.
- Dates in the user's configured timezone (per `preferences.md`).
- The card cap (8) is for chat delivery. The saved file always
  contains the full set above the relevance threshold.
- Never quote the reader profile back to the user. Cite project names
  and focus areas by name, but do not expose the profile text.
