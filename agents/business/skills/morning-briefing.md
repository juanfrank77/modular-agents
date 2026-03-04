# SKILL: morning-briefing

## Trigger
Morning briefing, daily summary, day-start report, what's on today, good morning

## Purpose
Generate a concise, useful morning briefing the user can read in under 2 minutes.
Priority order: urgent blockers first, then calendar, then email, then projects.

## Steps
1. Start with a one-line summary of the day (weather tone if available, otherwise just the date)
2. List today's calendar events in chronological order with time and any prep notes
3. Flag any urgent emails — subject and sender only, no body content
4. Pull the top 3 active priorities from projects.md
5. Close with one concrete suggestion for the most important thing to focus on first

## Output Format
Use this structure exactly:

**📅 [Day, Date]**

**Calendar**
- HH:MM — Event name (location or link if relevant)
- HH:MM — Event name

**📬 Urgent Email** *(skip section if inbox is clear)*
- From: [sender] — [subject]

**🎯 Top Priorities**
1. [Priority from projects.md]
2. [Priority from projects.md]
3. [Priority from projects.md]

**Focus:** [One sentence — the single most important thing to do first]

## Rules
- Keep total length under 300 words
- Never include email body content — subject and sender only
- If calendar is empty, say "No meetings today — protected focus time" and suggest a deep work block
- If inbox is clear, say "Inbox clear ✓" — don't skip the section
- Times should be in the user's configured timezone (check preferences.md)
- Do not add motivational filler or greetings