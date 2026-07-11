# SKILL: email-triage

## Trigger
Check email, triage inbox, unread emails, email summary, what emails do I have,
reply to email, draft a response, respond to [person]

## Purpose
Help the user reach inbox zero efficiently by categorising, summarising,
and drafting responses — without ever sending anything without explicit approval.

## When to use this vs email-digest
- **email-triage** answers "what needs my attention right now?"
  Proposes inbox actions (mark read, archive, draft reply) and waits
  for explicit approval before any change. Operates on unread by
  default; no time window.
- **email-digest** answers "what's relevant to my work?" Read-only,
  operates over a time window (default 7 days), read or unread.
  Saves a digest to `memory/solutions/`. Does not propose sends.

Typical workflow: run email-digest first to find what's relevant,
then email-triage to process the rest of the inbox. The digest's
auto-save file is the durable record of relevance; this skill's
approved actions are the durable record of inbox movement.

## Triage Categories
Classify every email into one of:

| Category | Criteria | Suggested Action |
|---|---|---|
| **Urgent** | Requires response today, from a key contact, time-sensitive | Surface immediately |
| **Action** | Requires a response or decision, not urgent | Queue for later today |
| **FYI** | Informational only, no reply needed | Mark read / archive |
| **Noise** | Newsletter, marketing, automated notification | Archive or unsubscribe |

## Steps for "check my email"
1. List unread emails grouped by category (Urgent → Action → FYI → Noise)
2. For each Urgent/Action email: show sender, subject, and one-line summary
3. For FYI/Noise: just show count — no individual summaries needed
4. Ask: "Would you like me to draft replies for any of these?"

## Steps for "draft a reply"
1. Retrieve the email thread for context
2. Draft a reply in the user's voice (refer to brand-voice context if available)
3. Present the draft — never send it directly
4. Format as:
    **Draft reply to [sender]:**
    ---
    [draft content]
    ---
    ACTION: SEND_EMAIL | Reply to [sender] re: [subject]

## Rules
- Never include full email body in summaries — one line only
- Never send, forward, or reply without an ACTION: line and explicit approval
- When drafting, match the user's communication style from preferences.md
- If asked to "clean up inbox" or "archive noise", propose the action first:
    ACTION: WRITE_LOW | Archive [N] newsletter/marketing emails