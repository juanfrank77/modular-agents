# Known Failure Modes

Catalogue of failure modes observed in this deployment, documented so they can
be fixed locally and, where they generalize, upstreamed to the core project.
Each entry follows a fixed shape: **Symptom → Reproduction → Root Cause → Fix →
Core relevance**.

Evidence comes from the non-business agent status run of 2026-11-05 00:20–00:24
UTC (Telegram messages stored in `memory/sessions.db`, journal of
`modular-agents.service` PID 755245) unless otherwise noted.

Status legend: `verified` = reproduced/confirmed in the run or by code reading.
`pending` = observed once, root cause not yet investigated.

---

## F1 — Local file allowlist blocks agent context files

**Status:** fixed (2026-11-05, commit 00d7e38)

**Symptom:** Devops and projects agents cannot read `memory/context/projects.md`,
the file that declares which repos/projects they manage. Devops asked the user
to pick a remedy ("Option A: read projects.md") and the user's chosen remedy
could not work — the read was denied again.

**Reproduction:** Send "Status check" to devops or projects; agent issues
`READ_LOCAL_FILE` for `memory/context/projects.md`; tool returns
`Access denied: … not under any configured local_file_paths`.
Journal: `local_file_denied`, 2026-11-05T00:20:16Z.

**Root cause:** The `local_file_paths` allowlist in core config does not include
`memory/context/`, even though agent prompt assets live there.

**Fix:** Add the memory context directory to `local_file_paths` (narrowly — the
context dir, not `memory/` wholesale).

**Core relevance:** Allowlist defaults should cover the agent's own prompt/context
assets, or ship a startup check that warns when a configured context file is
unreadable. Otherwise every deployment must rediscover this.

## F2 — Failed reads are treated as empty data, producing confident false answers

**Status:** fixed (2026-11-05, commit 2f652ce)

**Symptom:** Projects agent answered "No projects are tracked, so there are no
updates or stale-project flags to report." — after its read of `projects.md`
had been *denied*, not after finding an empty file. A silent failure converted
into a confident claim of absence.

**Reproduction:** Same as F1; then observe the agent's reply. It did not error,
and it did not mention the denial.

**Root cause:** The agent's status-summary path treats "no data fetched" and
"data store is empty" identically. Tool denial surfaced as an empty result set
with no error marker carried into the agent's reasoning.

**Fix:** When a data source read fails or is denied, the agent must report that
failure mode explicitly rather than summarizing "nothing found". Likely a
prompt-level rule plus distinct tool-result framing for denied/failed reads.

**Core relevance:** The most dangerous failure class of the run: not a crash,
but a wrong answer built on invisible failure. Any agent that summarizes
external state must distinguish "empty" from "could not read".

## F3 — `BaseAgent.reply()` does not persist the assistant turn

**Status:** fixed (2026-11-05, commit e576102)

**Symptom:** Librarian answered instantly (canned "knowledge base is empty"
reply, sent to Telegram) but the session history contains only the user's
question — the assistant turn was never stored. Any later conversation-restart
or context rebuild will see a half-missing exchange.

**Reproduction:** Message librarian with an empty knowledge base; check
`messages` table for the session: user row present, assistant row absent.

**Root cause:** `BaseAgent.reply()` (agents/base.py) sends to the notifier and
returns the response, but never calls `storage.save_message` for the assistant
role. Agents that build replies through their own save path (e.g. focus) are
unaffected; anything using the shared `reply()` helper silently loses history.

**Fix:** Save the assistant turn inside `reply()` (after the thinking-tag strip,
before/after send).

**Core relevance:** Any shared reply helper that persists must persist all
parties' turns, or conversation history silently diverges between channels that
save and helpers that don't.

## F4 — Empty final reply after tool-call turns (devops)

**Status:** fixed (2026-11-05, commit 1e6de2f)

**Symptom:** Devops's second turn ("Option A") produced a sendMessage with
empty content — the user received a blank message and the DB stored nothing.

**Reproduction:** Multi-turn conversation where the model's final turn follows
tool use; observed when the model has just been denied a tool call.

**Root cause:** Not yet isolated — the completion path produced no user-visible
text after tool-call handling. Suspect the final-turn handling when tool results
end the turn without follow-up completion.

**Fix:** Root-cause the completion path; add a guard that a turn with tool
activity must end with either text or an explicit tool-only response, never a
silent/empty message to the user.

**Core relevance:** Frameworks with tool-calling loops need a contract: a turn
that executes tools must terminate in a user-visible response or a structured
"continuation" state — not an empty message.

## F5 — Multiple tool calls in one turn: only the first executes

**Status:** fixed (2026-11-05, commits 1c64164 + 1e6de2f)

**Symptom:** `tool_call_extra_ignored` warning ("executing first only") whenever
the model emits two tool calls in a single turn; the second call is silently
dropped. In this run devops attempted two calls in one turn and the reply made
sense only for the first.

**Reproduction:** Prompt an agent to do two independent tool actions in one
message (e.g. "check both X and Y"); model emits two `tool_call`s in one turn.

**Root cause:** The tool-execution loop executes only the first tool call per
turn, dropping the rest with a warning.

**Fix (decision, 2026-11-05):** Execute ALL tool calls in the turn, logging the
same warning as informational. Rationale: models will keep emitting parallel
calls; silent dropping produced the "tool worked but half did" confusion, and
executing all is closer to user intent than rejecting. After the fix, keep the
warning only as an observability marker, not a behavioural one.

**Core relevance:** If the framework supports a single-tool-call contract,
either enforce it in the prompt or support parallel calls — never drop them
silently. An explicitly rejected call would at least let the model re-issue.

## F6 — Raw `LLMResult` repr leaks to the user

**Status:** verified

**Symptom:** Finance's reply begins with a literal Python repr:
`LLMResult(text='…')`. The user receives debug output over Telegram.

**Reproduction:** Any finance reply; check the assistant message content.

**Root cause:** The agent (or a shared path) stringifies the `LLMResult` object
instead of taking `.text`.

**Fix:** Persist/send `result.text`, never `str(result)`.

**Core relevance:** Type discipline on LLM result objects — a single shared
formatter/serializer prevents per-agent repr leaks.

## F7 — Command-only agents silently drop unmatched input

**Status:** verified

**Symptom:** Focus received "Status check" and "What's on"; both were dropped
in ~12 ms with no LLM call and no reply (working as coded, per its fallback
comment). From the outside the agent reads as dead.

**Reproduction:** Send any non-matching message to focus; no response arrives.

**Root cause:** `focus/_handle_interactive` returns empty text for unmatched
input by design ("don't respond to unrelated messages").

**Fix:** Add a one-line fallback reply ("I speak focus/stats/done — send 'help'")
so unmatched input at least gets a capabilities hint.

**Core relevance:** UX contract for command-only agents: unmatched input should
never vanish without a trace; a minimal fallback or a routing-level "no agent
handles this" notice is needed.

## F8 — Missing Settings attribute fails agent discovery entirely

**Status:** verified (2026-11-04 23:54 boot; fixed by 23:56 boot)

**Symptom:** Librarian failed to instantiate at boot:
`'Settings' object has no attribute 'librarian_agent_autonomy'` — the whole
agent was out of service (routed messages would have failed) until a settings
change was picked up on restart.

**Reproduction:** Boot with a Settings class lacking a newly-referenced agent
setting; the agent's discovery fails with a generic ERROR and `startup_degraded`.

**Root cause:** Agents access new settings attributes directly (`settings.foo`)
with no default; discovery treats the exception as fatal for that agent.

**Fix:** Prefer `getattr(settings, 'x', default)` for agent-level settings, and
make discovery errors actionable (include which attribute is missing).

**Core relevance:** Adding an agent-level setting should never break a previously
working agent at boot. Defaults for new settings, and precise error reporting,
protect against upgrade-by-addition regressions.

## F9 — `startup_degraded` on every boot, cause unlogged

**Status:** root-caused + fixed (2026-11-05). The failing agent is the
orchestrator: its health-check LLM probe used `max_tokens=4`, which a
reasoning model spends entirely on its thinking block, so `result.text` was
always empty and the check returned False on every boot. The `startup_degraded`
warning itself is now understood to be that single failing agent; the
`health_check_diagnostic` log lines (added during triage) name the agent and
reason. Fix: probe budget raised to 1024 tokens so a reasoning model can
finish thinking and emit visible text.

**Symptom:** Every boot this night logged
`WARNING … startup_degraded: Some agents failed health checks` followed by
`startup_complete` with all agents listed. The actual failing check is not in
the log line.

**Reproduction:** Any boot; warning fires every time.

**Root cause:** Unknown — the warning does not name the failing agents or checks.

**Fix:** Log which agents/checks failed and why. Then fix the underlying check
if it's a real failure, or silence it if it's a benign known condition (it has
fired on every boot while everything worked, which suggests a check that can
never pass).

**Core relevance:** A health warning that always fires is noise that trains
operators to ignore startup warnings — the opposite of what health checks are
for.

## F10 — Ops hygiene: conflicting unit definitions and uncommitted deploy config

**Status:** partially verified (details pending)

**Findings:**

1. The live stack runs from a system-scope unit
   (`/etc/systemd/system/modular-agents.service`, as exedev, pointing at
   `~/modular-agents-live`). Correct and intentional — but it is marked in the
   file as "derived from the repo template" and lives only on the host.
2. The user-level unit (`~/.config/systemd/user/modular-agents.service`) is a
   stale duplicate that points at the **master tree**
   (`/home/exedev/modular-agents`), dead since 2026-11-01. Footgun: starting it
   manually would run master alongside live.
3. The daily backup timer (`modular-agents-backup.timer`) runs
   `scripts/backup.sh` from the **master tree**. Whether it backs up the
   worktree's stateful data (the live stack's DBs) is unverified.
4. The `deploy/` directory in the worktree (likely containing the deployed unit
   variant) is untracked — the deployed configuration exists only on the host.

**Fix:** Remove or disable the stale user unit. Verify `backup.sh` targets the
worktree's live data. Commit the deploy variant (unit file + backup script) to
the repo so host state is reproducible.

**Core relevance:** Deployed-variant configs must be committed; runbook docs
should state which unit scopes (system vs user) are legitimate so stale
duplicates get pruned rather than started.

## F11 — Plaintext session storage

**Status:** verified (boot warnings, every boot)

**Symptom:** Boot log warns that messages are stored in plaintext
(`DB_ENCRYPTION_KEY` unset) for every boot.

**Fix:** Either set the key or accept and document the trade-off. Not a priority
for this round, but tracked here so the warning isn't normalized away.

---

## Run context (2026-11-05)

Stack: system-scope `modular-agents.service`, PID 755245, worktree
`~/modular-agents-live`. Crash-loop of four restarts 23:56→00:11 before
stabilizing. Agents in the run: devops, projects, finance, focus, librarian
(wellbeing was up but not exercised).

## Decisions recorded

- 2026-11-05: F5 fix direction = execute all parallel tool calls + log a
  warning (operator decision).
- 2026-11-05: Work order = catalogue first, then fixes F1→F10.
