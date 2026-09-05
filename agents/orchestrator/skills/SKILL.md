# Orchestrator Skills

## Trigger

The orchestrator activates when:
- A user asks for multi-step or multi-agent work.
- A worker agent hands off a result for review.
- A scheduled mission requires coordination across agents.

## Steps

1. Read `mission-state.md` to understand current mission context.
2. Plan the mission with milestones using `mission-planning.md`.
3. Delegate tasks to worker agents via the message bus.
4. Review handoffs using `handoff-review.md`.
5. Aggregate results using `validation-synthesis.md`.
6. Update `mission-state.md` with progress and outcomes.

## Output Format

Responses should include:
- Mission status summary.
- Next action or delegated task.
- Validation pass/fail summary.
- Any markdown state updates inside:

```markdown
# Mission State
...
```

## Validation Contract

- [ ] All delegated tasks have a response.
- [ ] Validation results are aggregated, not ignored.
- [ ] Mission state is updated after every significant action.
