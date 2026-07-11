# SKILL: budget-review

## Trigger
Budget, budget status, spending status, set budget, weekly report
(scheduled task `finance_weekly_report`, Sunday 18:00)

## Purpose
Show the month's spending against budgets — per category and in total —
in a compact, judgement-light summary.

## Commands
- `set budget groceries 400` — monthly budget for one category
- `set budget 2000` — total monthly budget (stored under key `total`)
- `budget` — on-demand summary

## Message Construction
- One line per category, highest spend first: `- groceries: 240.00 / 400.00`
- Mark overspent categories with `(over)`
- Total line shows remaining amount, or "over budget"

## Schedule
Weekly report every Sunday at 18:00 (outside evening quiet hours start of
19:30). Includes savings goal progress when goals exist.

## Rules
- Numbers first; at most one sentence of commentary
- Never send the report if quiet hours block the `finance-report` tag
