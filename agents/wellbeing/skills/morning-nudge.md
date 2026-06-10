# SKILL: morning-nudge

## Trigger
Morning routine, good morning, start of day, time to wake up, morning motivation,
daily check-in, how am I doing, did I sleep well

## Purpose
Start the day with a brief, non-intrusive nudge. No alarm, no lecture — just a gentle
prompt that respects the user's morning routine. The goal is to support consistency,
not to police it.

## Timing
- Weekdays: sent at 7:00 AM (Bogota time, UTC-5)
- Weekend: sent at 8:00 AM (Bogota time, UTC-5)
- Follow-up nudge: 8:30 AM weekdays only

## Message Construction
Pick from the correct pool based on whether it's a weekday or weekend.

Weekday pool:
- "Morning. [temp]C, [condition]. Good day for [run/yoga]."
- "Morning. [temp]C, [condition]. Time to move."
- "Morning. [condition]. Start as you mean to continue."

Weekend pool:
- "Morning. [temp]C, [condition]. Routine when you're ready. Enjoy the day."
- "Morning. [condition]. Enjoy the day."
- "Morning. No rush today. [temp]C, [condition]."

Weather fallback (when wttr.in is unavailable):
- "Morning. Good day for [run/yoga]."
- "Morning. Routine when you're ready."

## Activity Suggestion Logic
- If rainy or temp < -5°C: suggest yoga
- Otherwise: suggest running
- If no weather data: default to "run or yoga"

## Follow-up Message
Send once at 8:00 AM on weekdays only if no reply to the morning nudge:
- "Time to move."
Keep it minimal — the user already knows they missed it.

## State
Track `morning_nudge_sent_at` in state.json. If already sent today, skip silently.
Track `morning_followup_sent_at` separately.

## Quiet Hours
Respects morning_routine quiet hours window. The wellbeing-nudge tag must be
in the allowed list for the morning nudge to fire. Emergency override via
`is_emergency=True` bypasses quiet hours.

## Rules
- Never guilt-trip or lecture
- If the user already replied, skip the follow-up
- Messages are stateless — don't reference yesterday's performance
- Keep under 20 words
