# SKILL: calendar-blocking

## Trigger
Block time, schedule focus, add to calendar, when am I free, reschedule,
move a meeting, book time for [task], protect my mornings

## Purpose
Manage calendar intelligently — protect focus time, resolve conflicts,
and schedule tasks based on the user's energy and priority patterns.

## Time-Blocking Rules
Apply these rules unless the user explicitly overrides them:

- **Morning (8am–12pm)**: Deep work only. No meetings unless marked critical.
- **Afternoon (1pm–4pm)**: Meetings, calls, collaborative work.
- **Buffer blocks**: Always leave one 30-min buffer before and after back-to-back meetings.
- **Focus blocks**: Minimum 90 minutes. Never schedule focus blocks under 60 min — not worth it.
- **End of day (4pm–5pm)**: Admin, email, async tasks.

## Priority Scheduling
When blocking time for a task, use priority from projects.md:
- **High priority**: morning slot, earliest available
- **Medium priority**: afternoon slot
- **Low priority**: end-of-day or next available morning if afternoon is full

## Steps for "block time for [task]"
1. Check calendar for available slots that match the task's priority
2. Propose a specific slot:
    ACTION: CALENDAR_WRITE | Block [duration] on [date] [time] for "[task name]"
3. Wait for approval before writing

## Steps for "when am I free"
1. List available slots for the requested day/week
2. Highlight which slots are best for deep work vs meetings
3. Ask what they want to schedule — don't assume

## Steps for conflict resolution
1. Identify the conflicting events
2. Determine which has lower priority (check projects.md if task-related)
3. Propose a reschedule:
    ACTION: CALENDAR_WRITE | Move "[event]" from [original time] to [new time]
4. Wait for approval

## Rules
- Never write to calendar without an ACTION: CALENDAR_WRITE line and approval
- Never delete a calendar event without ACTION: CALENDAR_DELETE and explicit confirmation
- If unsure about priority, ask — don't guess
- Always confirm the user's timezone before scheduling (check preferences.md)