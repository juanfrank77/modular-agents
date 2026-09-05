# SKILL: validation-contract

## Trigger
Validation contract, validate deployment, check assertions, verify infrastructure

## Steps
1. Parse the validation contract assertions
2. Run each check against the deployment
3. Aggregate results into a pass/fail report

## Output Format
A numbered list of PASS/FAIL results with one-line reasons.

## Validation Contract
Assertions this task must satisfy before it is considered complete:
- [ ] All services return 200 on health check
- [ ] CI pipeline is green
- [ ] No secrets or API keys appear in committed code
- [ ] Deployment rollback procedure is documented
