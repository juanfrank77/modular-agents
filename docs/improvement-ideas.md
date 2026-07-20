# Improvement Ideas — Modular Agents

> Full-project review, 2026-07-06. Based on a code sweep of core/, interfaces/, all six agents, ops scripts, and tests — after the librarian/projects additions. Grouped by category; each item cites evidence. Items marked ⭐ are the highest-leverage fixes.

---

## 1. Structural (fix these first)

### ✅ 1.1 Agents never execute tools from conversation — DONE (2026-07-18)
Both business and devops agents now map `ACTION:` lines to `ActionSpec.execute()` on `self.tools` after `safety.check_action` approval (`agents/business/agent.py:190-216`, devops equivalent). Landed in commit 282a247.

### ✅ 1.2 Business agent's tools aren't even wired — DONE (2026-07-18)
`agents/business/tools/__init__.py` now has a `build_tools()` factory constructing `GmailTool`/`CalendarTool` via `ComposioTool`; imported in `agent.py:32`. `composio-anthropic==0.18.0` added to `requirements.txt`. Landed in commit 282a247.

### ✅ 1.3 Message routing is content-blind and sticky — DONE (2026-07-18)
`bus._resolve_agent` (`core/bus.py:177-207`) now resolves in order: explicit `@tag` → LLM intent-classifier (`classify_agent()`) over agent `description` fields → sticky last-agent → first-registered. CLI also parses `@agent` (`interfaces/cli.py:57`, `parse_agent_tag`). The chat→agent map is persisted (`bus.py:78-79`, `load_chat_agent_map`), surviving restarts. Landed in commit b076fd7.

### ✅ 1.4 All operational state dies on restart — MOSTLY DONE (2026-07-18)
- Pairing + lockout counters + pending approvals: `core/safety.py` `PairingManager`/`ApprovalGate` now take a `state_store` and persist across restarts, with orphan-reload on startup (`safety.py:324-339`).
- Scheduler: `core/scheduler.py:77-90` `configure_jobstore()` swaps in `SQLAlchemyJobStore` (sqlite-backed), idempotency-guarded as of commit a33c395.
- Bus continuity map: persisted (see 1.3).
- Landed in commits 8b89673, a33c395.
- **Still open:** HTTP sessions (`interfaces/http.py:70` `_sessions` dict) remain in-memory only — not covered by this work. Agent-creator wizard sessions unchecked.

---

## 2. LLM layer

### ✅ 2.1 No structured tool-calling — DONE (2026-07-18)
`LLMProvider.complete()` now returns a structured `LLMResult` (text + `tool_calls`)
instead of a bare `str`. `AnthropicLLM`, `KiloLLM`, and `OpenRouterLLM` pass a
`tools=` list (built from each agent's `ActionSpec` registry via
`core/tool_schema.py:build_tool_defs()`) and parse native `tool_use`/function-calling
responses. `OllamaLLM` is unchanged (`supports_tools = False`) and keeps the
`ACTION:` text-parsing path as its only route. Business and DevOps agents branch on
`self.llm.supports_tools` in `_handle_message`; see
`docs/superpowers/specs/2026-07-18-structured-tool-calling-design.md` for the full
design. v1 is single-tool-call-per-turn; multi-step chaining is a future increment.

### ✅ 2.2 Provider class duplication — DONE (2026-07-19)
`summarize()` is copy-pasted verbatim in all four providers (`llm.py:107-114, 167-174, 226-233, 301-308`), and the OpenAI-style message assembly repeats in three of them. One `_OpenAICompatibleLLM` base collapses Kilo/OpenRouter/Ollama.

`_SummarizeMixin` dedups `summarize()` across all four providers; `_OpenAICompatibleLLM` collapses `KiloLLM`/`OpenRouterLLM`'s near-identical `complete()` bodies. `OllamaLLM` keeps its own `complete()` (different transport — raw `httpx` vs `AsyncOpenAI`, no tool-calling) but shares the mixin. `core/llm.py`.

### ✅ 2.3 One global model for everything — DONE (2026-07-19)
`settings.default_model` is used for every agent *and* for summarization/compaction. Add per-agent model config (`LIBRARIAN_MODEL=…`) and route `summarize()` to a cheap model — compaction runs often and doesn't need the flagship.

Per-agent model overrides (`BUSINESS_AGENT_MODEL` etc., `core/config.py`, consumed via `BaseAgent.model` in `agents/base.py`) and a dedicated `SUMMARIZE_MODEL` (falls back to the existing cheap `CLASSIFIER_MODEL`, `core/llm.py` `_SummarizeMixin`).

### 2.4 Smaller items
- `~~get_llm_provider() calls sys.exit(1) in library code~~` — **DONE (2026-07-19)**: now raises `LLMProviderNotConfiguredError`, caught in `main.py`; also added `LLM_PROVIDER=` override.
- `~~Anthropic parsing assumes response.content[0] is text~~` — **already resolved** by the 2.1 tool-calling work: `AnthropicLLM.complete()` iterates every block in `response.content` (`core/llm.py`), not just the first.
- `~~OllamaLLM's httpx.AsyncClient is never closed on shutdown~~` — **DONE (2026-07-19)**: `main.py` now calls `llm.close()` in its shutdown `finally` block when the provider defines one.

---

## 3. Security & safety

- **Trust inferred from chat_id shape.** Non-numeric chat_ids are auto-approved (`safety.py:241`) and the Router dispatches by prefix — two modules agreeing on an implicit string convention. Make interface trust an explicit property carried on the event.
- **Approval callbacks aren't authenticated.** `_on_callback` resolves any valid `approval_id` without checking the resolver is the chat that was asked (`interfaces/telegram.py:269`); `resolve()` also has a set-before-registered race (`safety.py:285-296`).
- **HTTP auth = one shared code, unlimited tokens.** Anyone with the startup code mints sessions forever (`http.py:84-95`); expired sessions are never pruned. Add token revocation + GC.
- **Agent creator executes unvalidated LLM-generated Python.** `/newagent` writes model output straight to `agents/<name>/agent.py` (`core/agent_creator.py:256`) with no `ast.parse`, no review gate — it runs with full process privileges on next restart. At minimum: syntax-check, show a diff, require explicit approval before writing.
- **No way to clear a pairing lockout.** `PairingManager.MAX_FAILED_ATTEMPTS` (5) locks a chat_id permanently once persistence landed (§1.4) — `is_locked()` now survives restarts, and `delete_failed_attempts()` is only ever called from the success branch of `try_pair()`, which is unreachable once locked (`core/safety.py`). The only recovery today is deleting that chat_id's row from the state DB's `failed_attempts` table directly. Found while fixing RUNBOOK.md's now-inaccurate "restart the service to reset" guidance (2026-07-19). Needs a design decision: an admin unlock command, a TTL-based auto-expiry, or something else.
- `~~Rate limiter uses wall-clock time.time()~~` — **DONE (2026-07-19)**: `RateLimiter.is_allowed()`/`wait_time()` now use `time.monotonic()` (`core/safety.py`), immune to NTP jumps/clock changes mid-window.
- `~~Unencrypted DB is silent~~` — **DONE (2026-07-19)**: `main.py`'s `bootstrap()` now logs a warning at startup when `DB_ENCRYPTION_KEY` is unset. Log-only, not fatal in any environment — a visibility fix, not policy enforcement.
- `~~systemd hardening absent~~` — **DONE (2026-07-19)**: `StartLimitInterval`/`StartLimitBurst` moved to `[Unit]`; added `NoNewPrivileges`, `ProtectSystem=strict`, `PrivateTmp`, a scoped `ReadWritePaths` for the app's `memory/` dir, and `MemoryMax=512M` (`modular-agents.service`). `setup.sh`'s human-user install is unchanged — a dedicated-user install is a separate, larger operational change, explicitly out of scope for this pass.

---

## 4. Reliability & error handling

- **Notifier failures are invisible to callers.** `send`/`send_media`/`send_with_buttons` log and return `None` (`core/notifier.py:52-55, 99-100, 122-125`). If the approval-button message fails to send, `ApprovalGate` waits on an event that can never fire → guaranteed timeout-deny with no diagnosis. Return success/raise, and handle Telegram `RetryAfter` (flood control) with backoff instead of dropping.
- **Railway health parsing → hourly alert spam.** `healthy` requires status ∈ `("ACTIVE","SUCCESS","DEPLOYED")` parsed by substring-matching free-text CLI output (`railway.py:292, 301-317`). If the CLI output format shifts, the hourly `incident_watchdog` fires forever. Use `railway --json` where available; test the parsers.
- **`telegram_allowed_chat_ids[0]` as the universal scheduled-message target** — empty list → `chat_id=""` and scheduled sends silently vanish. Every agent copy-pastes this landmine (business `agent.py:310`, devops `:455`, wellbeing, librarian, projects). Fix once in `BaseAgent` (see 7.1).
- **Storage opens a fresh connection (and re-derives the SQLCipher key) on every query** (`storage.py:80-170`) — move to one long-lived connection; also fix the `get_or_create_session` check-then-insert race with `ON CONFLICT DO NOTHING`.
- **Timezones are inconsistent:** APScheduler uses server-local, `quiet_hours` uses naive `datetime.now()`, Storage uses UTC. Add one `USER_TIMEZONE` setting consumed by scheduler + quiet hours; today cron firing and quiet-hours gating can disagree.
- **Logger config is an ordering trap.** `get_logger` lazily configures with defaults; if any module logs before `main.py` calls `configure_logging`, real settings become a silent no-op (`core/logger.py:153-155`). Redaction only covers the field literally named `content` — `error`/`text` fields leak in full.

---

## 5. Memory & knowledge

- **`search_history` is `LIKE '%q%'`** — table scan, no ranking, unescaped wildcards (`storage.py:146`). SQLite **FTS5** is a drop-in upgrade and would improve every agent's recall.
- **No retention/pruning** — messages grow unbounded; compaction summarizes but never trims the underlying rows. Add a retention window + archive.
- **Skill/solution matching is naive bag-of-words** (`skill_loader.py:57`, `memory._get_relevant_solutions`): no stemming ("meeting" ≠ "meetings"), no stopwords, re-reads every file per message. Cache file contents; consider embeddings when the library grows.
- **Topic keywords are hardcoded** (`memory.py:64-68`) — only `personal` and `projects` exist; adding a topic file means editing core. Make it a frontmatter/config declaration per file.
- **Empty-task fallback loads *all* context files** (`memory.build_context`, `memory.py:504-519`), which can blow the prompt for agents that call it without a task.
- **Librarian follow-ups** (self-critique of the new code):
  - Duplicate detection: re-sending the same PDF creates a second note; hash sources and offer "update existing note" instead.
  - Note lifecycle: actions are checklists that nothing ever marks done — let `@librarian done <note>` check items off and drop completed notes from the digest.
  - Graph quality: without `GEMINI_API_KEY` graphify's extraction is structural only; document/decide on a semantic-extraction key.
  - Whisper is the only transcription path; consider local `faster-whisper` for privacy/cost.
- **Projects agent dual-write drift:** progress lives in both `state.json` and the `## Progress log` in projects.md; a hand-edit to one desyncs the other. Pick one source of truth (probably projects.md) and derive the other.

---

## 6. Interfaces & UX

- **CLI can't target agents.** `_make_event` hardcodes `agent_name=""` (`interfaces/cli.py:57-63`) — port the Telegram `@agent` prefix parser (telegram.py:142); same for HTTP examples in docs.
- **Pairing/rate-limit logic is triplicated** across telegram/http/cli with diverging UX (only Telegram shows attempts remaining). Extract a shared guard the interfaces call.
- **`/model` mutates the global default for everyone** (`telegram.py:293`) — scope it per-chat or per-agent.
- **Duplicate `_on_message` registration** in groups 0 and 1 (`telegram.py:55-79`) — the group-1 private-chat handler looks redundant and risks double-processing; verify and remove.
- **Telegram reaches into `pairing._failed_attempts`** (private attr, `telegram.py:126`) — add a public `attempts_remaining()`.
- **No HTTP streaming/SSE** and the `notifier.get_and_clear()` out-of-band capture (`http.py:139-148`) is race-prone across concurrent requests for one chat.
- **Echo agent is still registered in production** (`main.py:129-130`) and can receive stray routed messages — gate it behind a debug flag.
- **No "which agent am I talking to?"** indicator anywhere; with sticky routing this is disorienting. Cheap fix: prefix replies with the agent emoji/name, or add `/agents` listing who's registered and who's active for this chat.

---

## 7. Code architecture & hygiene

- **⭐ Declarative schedules in `BaseAgent`.** Every agent copy-pastes the same `register_schedules` prologue (import singleton, `chat_ids[0] if … else ""`, N× `add_cron_job`). Replace with a class-level `SCHEDULES = [("task_key", "0 8 * * 1-5"), …]` materialized by the base class — removes 5 copies of boilerplate and centralizes the empty-chat-list handling.
- **Agent auto-discovery instead of main.py surgery.** Registration is manual in `main.py`, and `/newagent` literally string-patches `main.py` looking for the echo import line (`agent_creator.py:181-227`) — a formatter run breaks it. Scan `agents/*/agent.py` for `BaseAgent` subclasses at startup; delete `_patch_main` entirely.
- **`async` functions doing blocking I/O:** `skill_loader` and `file_tool` declare async but call sync `read_text`/`write_text`, blocking the event loop — wrap in `asyncio.to_thread` or drop the `async`.
- **Scheduler job-ID collisions:** `f"{agent}_{task}"` with `replace_existing=True` silently overwrites (`scheduler.py:102`); no error isolation around fired jobs.
- **Config-worthy hardcodes:** `MAX_FAILED_ATTEMPTS`, approval default timeout, `_MAX_READ_BYTES`, skill `_MIN_SCORE`, heartbeat default, quiet-hours window names (only two, hardwired in `quiet_hours.py:40-43`).
- **FileTool:** write path has no size cap (reads capped at 100 KB), non-atomic writes (temp-file+rename would survive crashes), unbounded read cache.
- Dead cruft: `setup.sh` step 7 renames a long-gone `skill-loader.py`; `_snake` alias in agent_creator; `_send_progress` is a no-op so `/newagent` looks frozen during generation.

---

## 8. Testing & ops

- `~~⭐ No unit-test suite for core~~` — **DONE (2026-07-19)**: `quiet_hours` and `bus._resolve_agent` turned out to already have coverage (`tests/test_wellbeing.py`, `tests/test_bus_routing.py::TestResolveAgent`) — that part of this item was stale. The four genuine gaps are now closed: `notifier._split_message` (`tests/test_notifier_split_message.py`), `file_tool._validate_path` (`tests/test_file_tool_validate_path.py`, including symlink-escape and sibling-prefix cases), the Railway output parsers (`tests/test_railway_output_parsers.py`, including a pinned contract test for the exact `ACTIVE`/`SUCCESS`/`DEPLOYED` strings `get_health_summary()`'s healthy check depends on), and `safety` lockout/approval-timeout (`tests/test_safety_core.py`, distinct from `test_safety_persistence.py`'s StateStore coverage). `test_projects.py` was also moved from repo root into `tests/` and its stale `LLMResult` mock fixed, so `pytest tests/` now collects it. `test_integration.py`'s hand-rolled `print(PASS/FAIL)` script and `pytest.ini` still coexist unreconciled — that reconciliation is a separate, larger item.
- **Untested new surface:** Telegram `_on_file` ingestion path and `@agent` routing have no interface-level tests.
- **No CI.** A GitHub Actions workflow running `pytest` on push would catch most of the above regressions; the repo already lives on GitHub.
- **Backups are manual** — one `cp` example in the RUNBOOK. Add a cron/systemd-timer backing up `sessions.db` + `memory/context` + `memory/knowledge` + agent `state.json` files.
- **No log rotation** for the `nohup >> logs/bot.log` path; configure journald caps or logrotate.
- **RUNBOOK drift:** says grep `"PAIRING CODE"` in one place and `"PAIRING TOKEN"` in another (lines 148 vs 211) — one won't match. Document that restarts de-pair users and drop approvals (until 1.4 is fixed).
- `~~setup.sh doesn't check for gh/railway CLIs~~` — **DONE (2026-07-19)**: added step 7 ("External CLIs (optional)") — warns (doesn't fail) if either is missing, with install links. Also fixed a related bug found while touching this file: step 10's integration-test runner still pointed at `test_integration.py` in the repo root, which moved to `tests/test_integration.py` — it was silently no-op'ing on every run.

---

## 9. Suggested sequencing

| Phase | Theme | Items | Status |
|---|---|---|---|
| A — make it real | Tools actually execute | 1.1, 1.2, 2.1 | 1.1, 1.2, 2.1 ✅ all done — Phase A complete |
| B — make it survive | Restart persistence + notifier honesty | 1.4, 4 (notifier, chat_ids[0]), storage connection reuse | 1.4 ✅ mostly done (HTTP sessions still open); rest of 4 open |
| C — make it usable | Routing + interface parity | 1.3, 6 (CLI @agent, agent indicator), echo removal | 1.3 ✅ done incl. CLI @agent; rest of 6 open |
| D — make it safe | Trust model + creator gate + systemd hardening | 3 | open |
| E — make it last | Tests, CI, backups, FTS5, retention | 8, 5 | open |

Quick wins doable in an afternoon: `attempts_remaining()` accessor, RUNBOOK grep fix, echo debug-gate, ~~composio-anthropic in requirements~~ (done), ~~CLI @agent parsing~~ (done), duplicate Telegram handler removal, declarative `SCHEDULES` on BaseAgent, §2 (LLM layer) fully closed, ~~time.monotonic() in rate limiter~~ (done), ~~unencrypted-DB warning~~ (done), ~~StartLimitBurst → [Unit]~~ (done), §8's core unit-test suite gap closed.
