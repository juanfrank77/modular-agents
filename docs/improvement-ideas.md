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

### 2.2 Provider class duplication
`summarize()` is copy-pasted verbatim in all four providers (`llm.py:107-114, 167-174, 226-233, 301-308`), and the OpenAI-style message assembly repeats in three of them. One `_OpenAICompatibleLLM` base collapses Kilo/OpenRouter/Ollama.

### 2.3 One global model for everything
`settings.default_model` is used for every agent *and* for summarization/compaction. Add per-agent model config (`LIBRARIAN_MODEL=…`) and route `summarize()` to a cheap model — compaction runs often and doesn't need the flagship.

### 2.4 Smaller items
- `get_llm_provider()` calls `sys.exit(1)` in library code (`llm.py:322-331`); provider priority is hardcoded — add `LLM_PROVIDER=` override.
- Anthropic parsing assumes `response.content[0]` is text (`llm.py:152`) — breaks on thinking/tool blocks.
- `OllamaLLM`'s `httpx.AsyncClient` is never closed on shutdown.

---

## 3. Security & safety

- **Trust inferred from chat_id shape.** Non-numeric chat_ids are auto-approved (`safety.py:241`) and the Router dispatches by prefix — two modules agreeing on an implicit string convention. Make interface trust an explicit property carried on the event.
- **Approval callbacks aren't authenticated.** `_on_callback` resolves any valid `approval_id` without checking the resolver is the chat that was asked (`interfaces/telegram.py:269`); `resolve()` also has a set-before-registered race (`safety.py:285-296`).
- **HTTP auth = one shared code, unlimited tokens.** Anyone with the startup code mints sessions forever (`http.py:84-95`); expired sessions are never pruned. Add token revocation + GC.
- **Agent creator executes unvalidated LLM-generated Python.** `/newagent` writes model output straight to `agents/<name>/agent.py` (`core/agent_creator.py:256`) with no `ast.parse`, no review gate — it runs with full process privileges on next restart. At minimum: syntax-check, show a diff, require explicit approval before writing.
- **Rate limiter uses wall-clock `time.time()`** (`safety.py:183`) — use `time.monotonic()`.
- **Unencrypted DB is silent.** `db_encryption_key=""` disables SQLCipher with no startup warning (`storage.py:34`).
- **systemd hardening absent** — the service file lacks `NoNewPrivileges`, `ProtectSystem`, `PrivateTmp`, memory caps; `StartLimitBurst` sits under `[Service]` where modern systemd ignores it (`modular-agents.service:20-21`), defeating restart-loop protection. `setup.sh` prints dedicated-user advice but installs as the human user.

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

- **⭐ No unit-test suite for core.** `quiet_hours`, `notifier._split_message`, `safety` pairing/lockout/approval-timeout, `bus._resolve_agent` (the stickiness bug is a 3-line test), `file_tool._validate_path`, and the brittle Railway/GitHub output parsers all have zero coverage. These are pure functions — highest ROI in the codebase. Move to a `tests/` dir under pytest (the hand-rolled `print(PASS/FAIL)` script and `pytest.ini` currently coexist unreconciled).
- **Untested new surface:** Telegram `_on_file` ingestion path and `@agent` routing have no interface-level tests.
- **No CI.** A GitHub Actions workflow running `pytest` on push would catch most of the above regressions; the repo already lives on GitHub.
- **Backups are manual** — one `cp` example in the RUNBOOK. Add a cron/systemd-timer backing up `sessions.db` + `memory/context` + `memory/knowledge` + agent `state.json` files.
- **No log rotation** for the `nohup >> logs/bot.log` path; configure journald caps or logrotate.
- **RUNBOOK drift:** says grep `"PAIRING CODE"` in one place and `"PAIRING TOKEN"` in another (lines 148 vs 211) — one won't match. Document that restarts de-pair users and drop approvals (until 1.4 is fixed).
- `setup.sh` doesn't check for `gh`/`railway` CLIs that the devops agent hard-requires.

---

## 9. Suggested sequencing

| Phase | Theme | Items | Status |
|---|---|---|---|
| A — make it real | Tools actually execute | 1.1, 1.2, 2.1 | 1.1, 1.2 ✅ done; **2.1 next up** |
| B — make it survive | Restart persistence + notifier honesty | 1.4, 4 (notifier, chat_ids[0]), storage connection reuse | 1.4 ✅ mostly done (HTTP sessions still open); rest of 4 open |
| C — make it usable | Routing + interface parity | 1.3, 6 (CLI @agent, agent indicator), echo removal | 1.3 ✅ done incl. CLI @agent; rest of 6 open |
| D — make it safe | Trust model + creator gate + systemd hardening | 3 | open |
| E — make it last | Tests, CI, backups, FTS5, retention | 8, 5 | open |

Quick wins doable in an afternoon: `attempts_remaining()` accessor, `time.monotonic()` in rate limiter, unencrypted-DB warning, RUNBOOK grep fix, `StartLimitBurst` → `[Unit]`, echo debug-gate, ~~composio-anthropic in requirements~~ (done), ~~CLI @agent parsing~~ (done), duplicate Telegram handler removal, declarative `SCHEDULES` on BaseAgent.

## Current focus: Phase A complete

With items 1.1–1.4 and 2.1 all done (as of 2026-07-18), Phase A ("make it real" — tools actually execute with native LLM tool-calling) is complete. Next phase: Phase B (make it survive — restart persistence, notifier honesty, storage efficiency).
