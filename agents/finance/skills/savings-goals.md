# SKILL: savings-goals

## Trigger
Goal, savings, save, goals, savings progress

## Purpose
Set specific savings goals and track contributions toward them, with
progress percentages and a small celebration on completion.

## Commands
- `goal vacation 5000` — create (or re-target) a goal
- `save 200 vacation` — add a contribution ("to"/"for" optional)
- `save 200` — allowed only when exactly one goal exists
- `goals` — progress on all goals

## Behaviour
- Show `saved/target (pct%)` after every contribution
- When a goal is reached, say so plainly: "Goal reached. Well done."
- Re-creating an existing goal updates the target but keeps the saved amount

## State
`goals` — `{name: {target, saved}}` in state.json.

## Rules
- Suggest micro-adjustments only in the weekly report or when asked
- Keep confirmations to one line
