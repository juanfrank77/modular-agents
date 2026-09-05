# Mission Planning

## Trigger

Use this skill when the orchestrator receives a new mission or a major
course correction is needed.

## Steps

1. Break the mission into sequential milestones.
2. Assign each milestone to a specific agent or to the orchestrator.
3. Define acceptance criteria for each milestone.
4. Set checkpoints for review and handoff.
5. Document the plan in `mission-state.md`.

## Output Format

```markdown
# Mission Plan

## Milestone 1: <name>
- Owner: <agent_name>
- Acceptance: <criteria>

## Milestone 2: <name>
- Owner: <agent_name>
- Acceptance: <criteria>
```

## Validation Contract

- [ ] Each milestone has exactly one owner.
- [ ] Acceptance criteria are testable.
- [ ] No milestone depends on a future milestone.
