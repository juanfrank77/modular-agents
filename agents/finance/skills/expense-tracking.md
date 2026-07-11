# SKILL: expense-tracking

## Trigger
Spent, expense, log expense, bought, paid, spending

## Purpose
Frictionless expense logging from chat. One message = one expense with an
amount and a category. All data is user-entered — no bank connections.

## Commands
- `spent 12.50 groceries` — amount + category
- `spent $40 on dinner` — "$" and "on" are optional
- Missing category → logged as `uncategorized`

## Behaviour
- Confirm the logged amount and category in one line
- If a category budget exists, show category month-to-date vs budget
- If a total budget exists, show month total vs budget and warn when over

## State
`expenses` — list of `{date, amount, category}` in state.json. Summaries
always operate on the current calendar month.

## Rules
- Never question or moralize a purchase — log it and show the numbers
- Amounts accept both `12.50` and `12,50`
