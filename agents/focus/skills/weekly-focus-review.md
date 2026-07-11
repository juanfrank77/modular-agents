# SKILL: weekly-focus-review

## Trigger
Scheduled task `focus_weekly_review` (Sunday 17:00), or interactive
questions: stats, streak, how am I doing, deep work summary

## Purpose
A short Sunday review of the week's deep work: session count, active days,
total hours, and streak — plus one encouraging line calibrated to the week.

## Message Construction
- Sessions: N across D day(s), H.Hh total
- ≥4 active days: "Strong week of focused work." (extends the streak)
- 2–3 active days: "Decent week. One more focus day next week."
- 1 day: "Light week. Protect one deep work block per day."
- 0 sessions: "No sessions logged. Start small: one 25-minute block."
- Streak ≥2: mention "N strong weeks in a row."

## State
- A "strong week" (≥4 active days) increments `streak`; otherwise it resets to 0
- Sessions older than 8 weeks are pruned after the review

## Rules
- Never guilt-trip; a zero week gets a small, concrete suggestion
- Numbers first, judgement second
