# SKILL: weekly-check-in

## Trigger
Weekly check-in, how was my week, week summary, routine report,
morning routine stats, consistency report, how am I doing this week

## Purpose
Provide a weekly summary of morning routine consistency and offer a brief
reflection. This fires every Sunday at 9:00 AM Bogota time. It is informational
only — no action required.

## Timing
Sent at 09:00 Bogota time every Sunday. The week runs Monday → Sunday.

## Data Sources
- `state.json` → `weekly_stats.routine_days` — list of ISO dates when the
  morning nudge was successfully sent (not necessarily replied to)
- Current date to determine week boundaries

## Message Construction

Calculate: `routine_count` = number of routine days in the completed Mon–Sun window.

Output format:
```
Weekly check-in (Mon DD – Sun DD):

Morning routine: N/7 days

[One of four tier messages based on ratio:]
  ≥80%: "Strong week."
  ≥60%: "Decent week. Room to improve."
  ≥40%: "Mixed week. Tomorrow is a fresh start."
  <40%: "Rough week. But you're aware of it. That matters."

[Optional: streak count if ≥2 weeks in a row]
  "N weeks in a row." — only if streak ≥ 2
```

## Streak Logic
If `weekly_stats.streak` exists and is ≥ 2, note it in the message.
After sending, reset `routine_days` to empty — the counter is weekly.
Preserve the streak value if it persists.

## State Management
After sending the check-in:
1. Reset `weekly_stats.routine_days` to `[]`
2. Keep `weekly_stats.streak` — increment if routine_count ≥ 4, reset if < 4
3. Save state to state.json

## Rules
- Include actual dates for the week in the header
- Always use 7-day denominator — even partial weeks count as 7
- Keep total message under 80 words