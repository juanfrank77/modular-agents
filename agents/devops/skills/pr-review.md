# SKILL: pr-review

## Trigger
Review PR, pull request, code review, review this diff, check this PR,
what PRs need review, open pull requests, pending reviews

## Purpose
Give fast, useful code review feedback. Focus on correctness, security,
and maintainability — in that order. Be direct, not diplomatic.

## Review Criteria (in priority order)

### 1. Correctness — will this break anything?
- Logic errors or off-by-one conditions
- Unhandled edge cases or exceptions
- Race conditions or concurrency issues
- Missing null/None checks on values that could be absent
- Incorrect assumptions about input data

### 2. Security — could this be exploited?
- SQL injection, command injection, path traversal
- Secrets or credentials hardcoded or logged
- Missing input validation or sanitisation
- Overly permissive access controls
- Unvalidated redirects or user-controlled URLs

### 3. Data integrity — could this corrupt or lose data?
- Missing database transactions where needed
- Missing rollback on partial failures
- Destructive operations without confirmation
- Missing idempotency on operations that could be retried

### 4. Maintainability — will this be a problem in 6 months?
- Functions doing more than one thing
- Missing or misleading variable/function names
- Duplicated logic that should be extracted
- Missing tests for non-trivial logic
- Undocumented magic numbers or non-obvious decisions

## Output Format
Structure every review as:

**PR: [title or number]**
**Verdict: APPROVE / REQUEST_CHANGES / NEEDS_DISCUSSION**

**🔴 Blockers** *(must fix before merge)*
- [file:line] — [issue and why it matters]

**🟡 Suggestions** *(worth fixing, not blocking)*
- [file:line] — [issue and recommended approach]

**🟢 Good** *(acknowledge what was done well — keep it brief)*
- [one or two lines max]

**Summary:** [one sentence overall assessment]

## Rules
- Lead with blockers — don't bury them after praise
- Be specific: always include file and line number when possible
- If the PR is large (>300 lines), say so and suggest splitting it
- If tests are missing for new logic, that is always a 🔴 Blocker
- Never approve a PR that has secrets, credentials, or SQL injection risks
- If a PR looks good with no issues, say "LGTM — no issues found" and stop