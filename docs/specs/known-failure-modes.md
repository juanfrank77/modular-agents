# Known Failure Modes

Failure modes observed while building and operating a modular-agents
deployment. Each entry is written so it can be fixed locally and, where it
generalizes, prevented upstream in this repo. Symptoms and root causes are
deployment-agnostic; fixes reference the pattern, not any one install.

## F1 — Local file allowlist blocks agent context files

**Symptom:** Agents cannot read their own context assets
(`memory/context/*.md`), so a status-check agent claims it has no project
context and asks the user how to unblock itself.

**Repro:** Ask any agent that declares context files in `memory/context/` for
a status check while `LOCAL_FILE_PATHS` doesn't include the memory dir.

**Root cause:** `Settings.local_file_paths` is built solely from
`LOCAL_FILE_PATHS`; the memory context dir — which the framework itself owns
— is not part of the default allowlist.

**Fix:** `Settings` always appends `memory_context_dir` (env:
`MEMORY_CONTEXT_DIR`, default `memory/context`) to the allowlist roots;
`LOCAL_FILE_PATHS` adds extra roots on top.

**Core relevance:** Any tool with a path allowlist must automatically allow
the framework's own state directories. Framework-owned paths should never
depend on operator configuration to be readable.

## F2 — Failed reads are treated as empty data, producing confident false answers

**Symptom:** An agent reports "nothing tracked / no data" with full
confidence when the context file exists but is unreadable (permissions,
encoding, mount fault).

**Repro:** Make a context file unreadable to the service user, then ask for a
status summary.

**Root cause:** Context readers conflate "file missing" (legitimate empty
state) with "file unreadable" (failure) — both return the same empty string.

**Fix:** Missing file → `""`. Existing-but-unreadable file → an explicit
`[CONTEXT READ ERROR: ...]` marker that agents can surface instead of
summarizing absence.

**Core relevance:** Distinguishing "no data" from "read failed" is a core
memory-layer contract. Every read API must have a failure channel that cannot
be mistaken for an empty result.

## F3 — Shared reply helper does not persist the assistant turn

**Symptom:** The user receives a reply but the session history contains only
their own message; later context building sees a half-empty conversation.

**Repro:** Any agent whose reply path uses the shared helper instead of the
storage-aware path.

**Root cause:** The helper sends the message but never opens a session or
persists the assistant turn; persistence was left to each agent.

**Fix:** The shared helper persists the assistant turn (and warns on storage
failure instead of breaking delivery). Persistence of both parties' turns is
the helper's job, not each agent's.

**Core relevance:** Any shared send/reply helper must persist all parties'
turns, or conversation history silently diverges between channels that save
and helpers that don't.

## F4 — Empty final reply after tool-call turns

**Symptom:** A turn that executed tools ends with an empty message to the
user — a blank bubble, nothing stored.

**Repro:** Multi-turn conversation where the model's final turn follows tool
use, especially right after a denied tool call.

**Root cause:** The completion path can end a turn with tool activity and no
user-visible text.

**Fix:** A turn with tool activity must end in either text or an explicit
tool-only response; a completion returning empty text after tool execution
falls back to a visible "action executed" summary.

**Core relevance:** Tool-calling loops need a turn-termination contract: every
turn terminates in a user-visible response or a structured continuation —
never a silent empty message.

## F5 — Multiple tool calls in one turn: only the first executes

**Symptom:** The model emits two tool calls in one turn; the second is
silently dropped and the reply only reflects the first.

**Repro:** Prompt an agent to do two independent tool actions in one message.

**Root cause:** The execution loop ran only the first tool call per turn and
the continuation request could only carry a single tool result, so the API
contract itself forbade parallel calls.

**Fix:** Execute every tool call in the turn; the continuation request sends
one tool result per tool use (OpenAI `tool` messages, Anthropic `tool_result`
blocks). `parallel_tool_calls` is enabled and `disable_parallel_tool_use`
disabled.

**Core relevance:** "Half-done turns" are how users lose trust in tool use.
The framework's LLM layer must support N results per continuation turn, not
one.

## F6 / F9 — Health-check probe budget starves reasoning models; startup always degraded

**Symptom:** Every boot logs `startup_degraded` with no indication of which
agent failed or why.

**Repro:** Any reasoning model that emits a thinking block before text, given
a health-check probe with a tiny token budget.

**Root cause:** The orchestrator's boot-time LLM probe used a minimal
`max_tokens`; the reasoning model spent the entire budget on its thinking
block, returned no visible text, and the check failed on every boot.

**Fix:** Probe budget raised high enough for reasoning headroom plus a short
visible reply. Diagnostic logging now names the failing agent and reason
(`health_check_diagnostic`), so a degraded startup is actionable.

**Core relevance:** Health probes must account for reasoning models: any
token budget below the model's thinking requirement will produce
deterministic false negatives. Probes should also always log *what* failed.

## F8 — Missing Settings attribute fails agent discovery entirely

**Symptom:** One agent fails to instantiate at boot and disappears from
routing; all others come up fine.

**Repro:** Add an agent that reads a settings attribute which doesn't exist
in `Settings` (e.g. after renaming or forgetting to add a per-agent option).

**Root cause:** Agents read their settings attributes directly in the
constructor; one missing attribute raises during discovery and kills that
agent.

**Fix:** Constructor-level settings reads use
`getattr(settings, '<attr>', <class default>)` so a missing per-agent setting
falls back to the class default. Discovery already logs the failing
attribute, which stays the actionable signal.

**Core relevance:** A missing optional setting must degrade that one agent's
behaviour, never remove the agent from the system. Constructor-time settings
access needs a failure mode short of instantiation death.

## F7 — Command-only agents silently drop unmatched input

**Symptom:** The user messages a command-based agent in natural language; the
agent processes nothing and says nothing — it looks dead.

**Repro:** Send "status check" to a keyword-triggered agent.

**Root cause:** The agent's unmatched-input fallback returns an empty string.

**Fix:** Unmatched input gets a one-line capabilities hint (what the agent
does, how to trigger it), so the user always gets an acknowledgement.

**Core relevance:** Frameworks that route per-agent should define a
fallback-response convention: an agent that cannot act on a message must
still acknowledge it. Silence looks like an outage.

## F10 — Backups silently archive the wrong tree

**Symptom:** Daily backups succeed every night but contain a stale/empty
database; the live system's data is never backed up.

**Repro:** Develop the project in one checkout and run the service from
another. The backup script defaults `PROJECT_DIR` to its own parent — the
*development* tree — while the service writes to the live tree.

**Root cause:** The shipped systemd templates and `backup.sh` assume the
deployment tree is the tree the script ships in. Nothing verifies that.

**Fix (three layers):**
1. `backup.sh` detects any running modular-agents process, compares its
   working directory against `PROJECT_DIR`, and warns loudly when they
   differ.
2. `backup.sh` logs what it actually archived (message-row count for
   `sessions.db`, or a warning when the DB is absent) — an empty backup is
   surfaced at backup time, not at restore time.
3. Deployment units that run from a non-default worktree must set
   `PROJECT_DIR` (and `ExecStart`) explicitly to the live tree; host-specific
   unit variants should be version-controlled by the operator.

**Core relevance:** Any repo shipping systemd timers or cron-based backups
must treat "the tree you develop in" and "the tree you run in" as distinct
deployments. The backup tooling must verify it is copying the data actually
in use — silently archiving the wrong tree is worse than no backup at all.
