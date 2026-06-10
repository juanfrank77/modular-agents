# SKILL: evening-wind-down

## Trigger
Evening, wind down, step away, done for the day, log off, end of work,
stop working, screen time, too much screen

## Purpose
Remind the user to step away from screens in the evening. The goal is to create
a clean separation between work and rest — not to be a nag. One message, then done.

## Timing
Sent at 19:30 Bogota time (UTC-5) every day.

## Message Construction
Pick one message cyclically (day of year modulo pool size):

1. "Your evening. Do something you enjoy. The work will be there tomorrow."
2. "Evening time. Step away from the screens. You've done enough today."
3. "Wind-down time. Whatever makes you happy tonight."
4. "Evening. You've earned the rest. Do something for yourself."

## State
Track `evening_nudge_sent_at` in state.json. If already sent today, skip silently.
No follow-up messages.

## Quiet Hours
Respects evening quiet hours window. If quiet hours are active, the nudge
is suppressed. The wellbeing-nudge tag must be in the evening allowed list.

## Rules
- One message only — no follow-up if no reply
- Never reference work tasks or tomorrow's agenda
- Keep under 15 words
- No emoji in this message — it's a signal, not a notification