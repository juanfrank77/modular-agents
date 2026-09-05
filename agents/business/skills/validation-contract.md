# SKILL: validation-contract

## Trigger
Validation contract, validate work, check assertions, verify completion

## Steps
1. Parse the validation contract assertions
2. Run each check against the implementation
3. Aggregate results into a pass/fail report

## Output Format
A numbered list of PASS/FAIL results with one-line reasons.

## Validation Contract
Assertions this task must satisfy before it is considered complete:
- [ ] All new endpoints return 200 on smoke test
- [ ] `pytest` passes with zero failures
- [ ] `ruff check` reports no errors
- [ ] No secrets or API keys appear in committed code
