# SKILL: deploy-checklist

## Trigger
Deploy, release, ship, push to production, go live, rollout, upgrade version,
deploy to staging, deploy to prod, fly deploy, railway deploy

## Purpose
Ensure every deployment follows a consistent, safe process.
No deploy should happen without this checklist being surfaced first.

## Pre-Deploy Checklist
Before proposing any deploy action, verify and surface:

1. **Tests passing** — confirm CI is green on the target branch
2. **Branch is up to date** — no unmerged changes from main/master
3. **Migration check** — are there any pending DB migrations?
   If yes: migrations must run BEFORE the deploy, not after
4. **Environment variables** — any new env vars needed in production?
5. **Rollback plan** — what is the previous stable version to roll back to?
6. **Traffic / timing** — is this a low-traffic window? Avoid deploys Fri afternoon

## Deploy Steps (present in this order)
```
1. [ ] Run final test suite on target branch
2. [ ] Pull latest from main, confirm no conflicts
3. [ ] Run DB migrations (if any)
4. [ ] Deploy to staging → smoke test
5. [ ] Deploy to production
6. [ ] Confirm health check endpoint returns 200
7. [ ] Monitor error rate for 10 minutes post-deploy
```

## Proposing the Deploy
Always format as two separate ACTION lines — staging first, then prod:

    ACTION: DEPLOY_STAGING | Deploy [version/branch] to staging environment
    ACTION: DEPLOY_PROD | Deploy [version/branch] to production — post-staging check

Never propose DEPLOY_PROD without DEPLOY_STAGING first, unless user explicitly skips.

## Post-Deploy
After a successful deploy, ask:
- "Should I create a release note for this deployment?"
- "Want me to monitor error rates for the next 30 minutes?"

## Rollback
If rollback is requested:
    ACTION: DEPLOY_PROD | Rollback to [previous version] — reason: [reason]
State the rollback reason clearly in the description.