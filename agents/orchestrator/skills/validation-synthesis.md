# Validation Synthesis

## Trigger

Use this skill when the orchestrator needs to aggregate validation results
from multiple workers or tools.

## Steps

1. Collect all validation reports.
2. Normalize results to PASS or FAIL.
3. Weight results by criticality if needed.
4. Produce a single consolidated validation report.
5. Update mission state with the final verdict.

## Output Format

```markdown
# Validation Report

Overall: PASS | FAIL

| Check | Result | Notes |
|-------|--------|-------|
| <check> | PASS/FAIL | <notes> |
```

## Validation Contract

- [ ] Every reported check has a PASS or FAIL result.
- [ ] FAILs include a remediation suggestion.
- [ ] The overall verdict matches the individual checks (any FAIL => overall FAIL).
