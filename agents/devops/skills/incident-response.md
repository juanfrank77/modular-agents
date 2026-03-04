# SKILL: incident-response

## Trigger
Incident, outage, down, error spike, high latency, alert, pager, something is broken,
site is slow, users can't log in, 500 errors, service unavailable, CPU spike, memory leak

## Purpose
Guide fast, structured incident response. The goal is: detect → diagnose → mitigate → resolve.
Never skip steps. Speed matters but so does not making things worse.

## Severity Levels
Classify every incident before responding:

| Level | Criteria | Response Target |
|---|---|---|
| **P1 — Critical** | Full outage, data loss risk, security breach | Immediate — drop everything |
| **P2 — Major** | Partial outage, >20% users affected, performance degraded >50% | Within 15 minutes |
| **P3 — Minor** | Single feature broken, <20% users affected | Within 1 hour |
| **P4 — Low** | Cosmetic, non-blocking, internal tooling | Next business day |

## Response Steps

### 1. Detect & Classify
- State the severity level immediately
- Describe what is failing in one sentence
- Note when it started (if known)

### 2. Diagnose
Run diagnostics in this order:
1. Check health endpoints / uptime monitor
2. Check error logs for the last 15 minutes
3. Check recent deploys — was anything shipped in the last 2 hours?
4. Check infrastructure metrics: CPU, memory, DB connections, queue depth
5. Check external dependencies (third-party APIs, payment processors, CDN)

Present findings as:
```
🔍 Diagnosis:
- Error rate: [X%] (baseline: [Y%])
- Last deploy: [time] — [version]
- Likely cause: [hypothesis]
```

### 3. Mitigate (stop the bleeding)
Options in priority order:
1. Rollback last deploy if correlated → `ACTION: DEPLOY_PROD | Rollback to [version]`
2. Restart failing service → `ACTION: RESTART_SERVICE | Restart [service name]`
3. Enable maintenance mode if full outage
4. Scale up resources if load-related

Always state what the mitigation does AND what it risks.

### 4. Resolve & Document
Once resolved:
- Confirm health checks are green
- Note root cause and fix
- Save as a solution:
    "What happened, why, how we fixed it, how to prevent it"
- Suggest a follow-up task: post-mortem, monitoring alert, or code fix

## Communication Template
For P1/P2 incidents, send this update every 15 minutes until resolved:

```
🚨 Incident Update [HH:MM]
Status: [Investigating / Mitigating / Resolved]
Impact: [what users are seeing]
Current action: [what we are doing right now]
ETA: [estimate or "unknown"]
```