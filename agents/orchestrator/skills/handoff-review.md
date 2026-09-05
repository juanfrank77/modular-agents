# Handoff Review

## Trigger

Use this skill when a worker agent hands off a completed subtask to the
orchestrator.

## Steps

1. Read the handoff payload and attached results.
2. Verify the handoff against the acceptance criteria from the mission plan.
3. Check for missing artifacts, incomplete steps, or data quality issues.
4. Request revision or approve the handoff.
5. Record the review outcome in `mission-state.md`.

## Output Format

```markdown
# Handoff Review: <milestone_name>

Status: APPROVED | NEEDS_REVISION
Findings:
- <finding 1>
- <finding 2>
```

## Validation Contract

- [ ] Review references the original acceptance criteria.
- [ ] Status is either APPROVED or NEEDS_REVISION.
- [ ] Findings are actionable, not vague.
