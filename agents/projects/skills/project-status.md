# SKILL: project-status

## Trigger
Status, project status, what's stale, how are my projects, portfolio review

## Steps
1. Report per-project: days since last update and the last logged note
2. Flag stale projects (7+ days) explicitly
3. If asked about one project, answer only for that project with its recent log entries

## Output Format
- One line per project, stale ones marked
- End with the single most useful next action, not a list

## Edge Cases
- A project has no logged updates: say "no updates logged" — don't infer activity
- On-hold projects: exclude from staleness warnings
