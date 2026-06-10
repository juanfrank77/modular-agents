# SKILL: bedtime-reminder

## Trigger
Bedtime, sleep, go to bed, too late, should sleep, sleeping, rest,
good night, sleep quality, tired

## Purpose
Signal that it's late and the user should head to bed. Like the evening wind-down
— one message, no follow-up. The goal is to end the day cleanly, not to be a parent.

## Timing
Sent at 23:00 Bogota time (UTC-5) every day.

## Message Construction
Pick one message cyclically (day of year modulo pool size):

1. "Bedtime. Sleep is the best investment. Good night."
2. "Time to wind down. Good night."
3. "Bed now = full sleep. Good night."
4. "Sleep. Tomorrow is a new day."

## State
Track `bedtime_nudge_sent_at` in state.json. If already sent today, skip silently.
Separate from evening_nudge — both can fire on the same day.

## Quiet Hours
Bedtime reminder is NOT suppressed by evening quiet hours — it is tagged
differently and uses emergency override logic if needed. If evening quiet
hours are active, the bedtime nudge still fires normally.

## Rules
- One message only — no follow-up if no reply
- Keep under 10 words
- Direct, not motivational