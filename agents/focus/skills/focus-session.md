# SKILL: focus-session

## Trigger
Focus, deep work, start focus, pomodoro, focus session, done, stop focus,
cancel focus, concentration block

## Purpose
Help the user start, end, and track deep work sessions. One session = one
uninterrupted block of focused work with an optional minutes goal.

## Commands
- `focus` or `focus 50` — start a session (default goal: 50 minutes)
- `done` / `stop focus` — end the active session and log the real duration
- `cancel focus` — discard the active session without logging

## Behaviour
- Only one active session at a time. If one is running, remind the user to
  finish it first instead of silently replacing it.
- Duration is measured from actual start to actual end — not the goal.
- Falling short of the goal still counts. Never shame the user for a short
  session; a logged 20 minutes beats an abandoned 50.

## State
- `active_session` — `{started_at, goal_minutes}`, removed when ended/cancelled
- `sessions` — list of `{date, minutes}`, pruned to the last 8 weeks

## Rules
- Confirmation messages stay under 25 words
- Encourage, never lecture
