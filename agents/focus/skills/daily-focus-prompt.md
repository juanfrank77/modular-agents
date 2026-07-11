# SKILL: daily-focus-prompt

## Trigger
Scheduled task `focus_daily_prompt` (weekdays 10:00, after the morning
quiet-hours window ends)

## Purpose
A single daily planning nudge: pick the one task that deserves deep work
today and invite the user to start a session.

## Message Construction
Rotate through a small pool of prompts, e.g.:
- "What's your #1 deep work priority today? Say 'focus 50' when you start."
- "Pick the one task that matters most today. 'focus 50' to begin a session."

## State
Track `daily_prompt_sent_at` in state.json. If already sent today, skip
silently.

## Quiet Hours
Uses the `focus-nudge` tag. Scheduled at 10:00 specifically to land outside
the morning routine window (07:00–09:30).

## Rules
- One prompt per day, weekdays only
- No follow-up nag — the user decides when to go deep
- Keep under 20 words
