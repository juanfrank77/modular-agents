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

### ✅ 1.4 All operational state dies on restart — DONE (2026-07-22)
- Pairing + lockout counters + pending approvals: `core/safety.py` `PairingManager`/`ApprovalGate` now take a `state_store` and persist across restarts, with orphan-reload on startup (`safety.py:324-339`).
- Scheduler: `core/scheduler.py:77-90` `configure_jobstore()` swaps in `SQLAlchemyJobStore` (sqlite-backed), idempotency-guarded as of commit a33c395.
- Bus continuity map: persisted (see 1.3).
- Landed in commits 8b89673, a33c395.
- **HTTP sessions:** Now pruned on access (`http.py:111-128`) and have admin unlock endpoint (`http.py:159-168`). Agent-creator wizard sessions remain in-memory only but auto-expire after 10 minutes.

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

### ✅ 2.4 Smaller items
- `~~get_llm_provider() calls sys.exit(1) in library code~~` — **DONE (2026-07-19)**: now raises `LLMProviderNotConfiguredError`, caught in `main.py`; also added `LLM_PROVIDER=` override.
- `~~Anthropic parsing assumes response.content[0] is text~~` — **already resolved** by the 2.1 tool-calling work: `AnthropicLLM.complete()` iterates every block in `response.content` (`core/llm.py`), not just the first.
- `~~OllamaLLM's httpx.AsyncClient is never closed on shutdown~~` — **DONE (2026-07-19)**: `main.py` now calls `llm.close()` in its shutdown `finally` block when the provider defines one.

---

## 3. Security & safety

- **Trust inferred from chat_id shape.** Non-numeric chat_ids are auto-approved (`safety.py:241`) and the Router dispatches by prefix — two modules agreeing on an implicit string convention. Make interface trust an explicit property carried on the event.
  - *Follow-up tied to the agent creator (see item below):* a stronger trust model for `/newagent` would queue generated files for an *owner/admin-only* approver rather than letting the wizard chat self-approve, since the generated code runs with full process privileges on the next restart. The current validate + user-preview + explicit `yes` gate (item below, DONE) addresses accidental LLM breakage; an admin gate would address a *malicious or compromised* LLM and presumably lives here as part of the trust-model fix rather than in the creator.
- **Approval callbacks aren't authenticated.** `_on_callback` resolves any valid `approval_id` without checking the resolver is the chat that was asked (`interfaces/telegram.py:269`); `resolve()` also has a set-before-registered race (`safety.py:285-296`).
- **HTTP auth = one shared code, unlimited tokens.** Anyone with the startup code mints sessions forever (`http.py:84-95`). Expired sessions are now pruned on access (`http.py:111-128`). Still open: token revocation (beyond delete) and per-chat limits.
- **~~Agent creator executes unvalidated LLM-generated Python.~~** — **DONE (2026-07-29)**: `/newagent` no longer writes LLM output straight to disk. `core/agent_creator.py` now does, in order: `_validate_generated()` runs `ast.parse()` on `agent_py` (and on `tools_stub` when `has_tools`) to catch syntax errors without executing, shape-checks required string fields, enforces `agent.module_name` matches the wizard-chosen name, and rejects skill filenames that aren't lowercase `*.md` basenames (no path separators/dirs) — killing the `../`/absolute-path traversal vector. `_write_agent_files()` keeps an independent basename + regex guard so a bypassed validator still can't escape `skills/`. The wizard gained a `confirm` step: after successful validation it parks the session (`WizardSession.generated`), shows a **preview** (file list + counts + a truncated `agent.py`, capped at 60 lines / 2000 chars) and asks the user to reply `yes` (write) or `no`/`cancel` (discard, nothing on disk). Refuses anything but yes/no at that step. A *real diff* was intentionally not used since generated files are all new (name conflicts rejected up front), so a unified diff would be the whole file marked `+++`; the preview is the honest equivalent. Covered by `tests/test_agent_creator.py`. Sandbox-level safety against a *malicious* LLM is still out of scope here — that's noted as a follow-up under the trust-model item above.
- **No way to clear a pairing lockout.** — **DONE (2026-07-19)**: `PairingManager.MAX_FAILED_ATTEMPTS` (5) locked a chat_id permanently once persistence landed (§1.4) — `is_locked()` now survives restarts, and `delete_failed_attempts()` is only ever called from the success branch of `try_pair()`, which is unreachable once locked (`core/safety.py`). Added `PairingManager.unlock()` method and `POST /admin/unlock` endpoint (HTTP) to clear lockouts. Telegram interface can call `safety.pairing.unlock()` as well.
- **Rate limiter uses wall-clock time.time()** — **DONE (2026-07-19)**: `RateLimiter.is_allowed()`/`wait_time()` now use `time.monotonic()` (`core/safety.py`), immune to NTP jumps/clock changes mid-window.
- **Unencrypted DB is silent** — **DONE (2026-07-19)**: `main.py`'s `bootstrap()` now logs a warning at startup when `DB_ENCRYPTION_KEY` is unset. Log-only, not fatal in any environment — a visibility fix, not policy enforcement.
- **systemd hardening absent** — **DONE (2026-07-19)**: `StartLimitInterval`/`StartLimitBurst` moved to `[Unit]`; added `NoNewPrivileges`, `ProtectSystem=strict`, `PrivateTmp`, a scoped `ReadWritePaths` for the app's `memory/` dir, and `MemoryMax=512M` (`modular-agents.service`). `setup.sh`'s human-user install is unchanged — a dedicated-user install is a separate, larger operational change, explicitly out of scope for this pass.

---

## 4. Reliability & error handling

- **Notifier failures are invisible to callers.** — **DONE (2026-07-28)**: `core/protocols.py` now defines `NotificationError` and the `Notifier` protocol documents that delivery methods raise on failure. `core/notifier.py` `_send_with_retry()` retries on Telegram `RetryAfter` with backoff and raises `NotificationError` for unrecoverable errors or excessive flood waits. `TelegramNotifier.send`/`send_media`/`send_with_buttons`/`send_and_get_id`/`delete_message` now raise instead of swallowing. `ApprovalGate.request_approval()` (`core/safety.py`) catches delivery failures, returns `False` immediately, cleans up the orphan event, and attempts a plain-text fallback so the user sees the failure instead of a silent timeout-deny.
- **Railway health parsing → hourly alert spam.** — **DONE (2026-07-28)**:  `healthy` requires status ∈ `("ACTIVE","SUCCESS","DEPLOYED")` parsed by substring-matching free-text CLI output (`railway.py:292, 301-317`). If the CLI output format shifts, the hourly `incident_watchdog` fires forever. Use `railway --json` where available; test the parsers.
- **`telegram_allowed_chat_ids[0]` as the universal scheduled-message target**  — **DONE (2026-07-28)**: — empty list → `chat_id=""` and scheduled sends silently vanish. Every agent copy-pastes this landmine (business `agent.py:310`, devops `:455`, wellbeing, librarian, projects). Fix once in `BaseAgent` (see 7.1).
- **Storage opens a fresh connection (and re-derives the SQLCipher key) on every query** — **DONE (2026-07-28)**: `Storage` now keeps a single long-lived `aiosqlite.Connection` opened during `init()` and reused for every query (`core/storage.py`). The SQLCipher key is applied once at connection setup, and `PRAGMA busy_timeout = 5000` avoids transient lock contention with `StateStore`'s per-query connections. `get_or_create_session` uses `INSERT OR IGNORE` to close the old check-then-insert race. `Storage.close()` is called from `main.py`'s shutdown `finally` block.
- **~~Timezones are inconsistent~~** — **DONE (2026-07-28)**: Added `USER_TIMEZONE` setting (`core/config.py`) defaulting to UTC. `core/timezone.py` provides `load_timezone()`, `now_in_user_timezone()`, and `as_user_timezone()`. `core/quiet_hours.py` now evaluates windows in the user's timezone; `core/scheduler.py` passes the timezone to `CronTrigger.from_crontab()` so cron jobs fire in the same zone. `main.py` calls `_scheduler.set_timezone(settings.user_timezone)` at startup. `agents/wellbeing/agent.py` also uses the user timezone for "already sent today" checks and date-based state so scheduled nudges and internal state agree. 
- **~~Logger config is an ordering trap.~~** — **DONE (2026-07-28)**: `get_logger` lazily configured with defaults, and if any module logged before `main.py` called `configure_logging`, real settings became a silent no-op (`core/logger.py:153-155`). `configure_logging()` now passes `force=True` (clearing `root.handlers` first) so it always applies as authoritative, even after an earlier lazy default `_configure()`. Redaction now covers any field in `_REDACTED_FIELD_NAMES` (`{"content", "text"}`) instead of only the literal `content`; covered by `tests/test_logger_config.py`.

---

## 5. Memory & knowledge

- **`search_history` is `LIKE '%q%'`** — **DONE (2026-07-25)**: table scan, no ranking, unescaped wildcards (`storage.py:146`). SQLite **FTS5** is a drop-in upgrade and would improve every agent's recall.
- **No retention/pruning** — **DONE (2026-07-25)**: `Settings.message_retention_days` (default 90, 0 disables) now prunes messages older than the window on session access (`Memory.get_session_context`), via `Storage.delete_messages_older_than` with an `AFTER DELETE` trigger keeping `messages_fts` in sync. Hard delete only — the archive half of the original idea was deliberately descoped as unneeded complexity; see `docs/superpowers/specs/2026-07-25-message-retention-design.md`.
- **Skill/solution matching is naive bag-of-words** - **DONE (2026-07-25)**: (`skill_loader.py:57`, `memory._get_relevant_solutions`): no stemming ("meeting" ≠ "meetings"), no stopwords, re-reads every file per message. Cache file contents; consider embeddings when the library grows.
- **Topic keywords are hardcoded** (`memory.py:64-68`) — **DONE (2026-07-25)** :only `personal` and `projects` exist; adding a topic file means editing core. Make it a frontmatter/config declaration per file.
- **Empty-task fallback loads *all* context files** — **DONE (2026-07-25)**: `build_context` (`memory.py`) now always calls `get_relevant_context(task)`, which loads only the index and `topic-always-load` files when task is empty, instead of every context file unconditionally. All current agent call sites already pass a non-empty task; see `docs/superpowers/specs/2026-07-25-build-context-empty-task-design.md`.
- **Librarian follow-ups** (self-critique of the new code):
  - Duplicate detection: re-sending the same PDF creates a second note; hash sources and offer "update existing note" instead.
  - Note lifecycle: actions are checklists that nothing ever marks done — let `@librarian done <note>` check items off and drop completed notes from the digest.
  - Graph quality: without `GEMINI_API_KEY` graphify's extraction is structural only; document/decide on a semantic-extraction key.
  - Whisper is the only transcription path; consider local `faster-whisper` for privacy/cost.
- **Projects agent dual-write drift** — **DONE (2026-07-26)**: `projects.md`'s `## Progress log` section is now the sole source of truth; `state.json` and its read/write machinery were removed. Staleness and last-note data are derived from the file itself via a new `_parse_progress_log` helper. The user-owns-headings/agent-owns-progress-log split is now a tested invariant; see `docs/superpowers/specs/2026-07-26-projects-dual-write-design.md`.

---

## 6. Interfaces & UX

- `~~CLI can't target agents~~` — **DONE** (already fixed, found stale 2026-07-25): `core/routing.py`'s `parse_agent_tag()` is already wired into all three interfaces — `interfaces/cli.py:57`, `interfaces/telegram.py:133`, `interfaces/http.py:197` — evidently landed alongside item 1.3's routing work; this entry just never got updated. Added a `POST /message` `@agent` example to `ARCHITECTURE.md`'s HTTP API section, which was the one part still genuinely missing.
- `~~Pairing/rate-limit logic is triplicated~~` — **DONE (2026-07-25)**: pairing turned out to be three intentionally different models (Telegram: conversational, tracks attempts remaining; HTTP: token-exchange via `POST /pair`; CLI: auto-paired as trusted) — not unified, since collapsing them would lose real UX differences. What *was* genuinely duplicated: rate-limit check logic (`is_allowed()` + `wait_time()` + message formatting), now `RateLimiter.check()` (`core/safety.py`) used by both `telegram.py` and `http.py`. Also deduped Telegram's own internal "not paired" guard — it was repeated 3x across `_on_model`/`_on_planmode`/`_on_command` with two different messages/delivery paths — into one `TelegramInterface._require_paired()` helper, standardized on the more informative "send the pairing token" message via `bus.send_notification`.
- `~~/model mutates the global default for everyone~~` — **DONE (2026-07-25)**: scoped per-chat rather than per-agent — per-agent runtime overrides would have duplicated the `<AGENT>_AGENT_MODEL` env vars item 2.3 already shipped. New `chat_model_map` table (`core/state_store.py`), `MessageBus.get_chat_model()`/`set_chat_model()`/`clear_chat_model()` (mirrors the `chat_agent_map` persistence pattern), and `BaseAgent.resolve_model(chat_id)` giving precedence: chat override → agent's env var → global default. `/model` in `telegram.py` now calls `bus.set_chat_model()` instead of mutating `settings.default_model`; added `/model reset` to clear the override. Bundled with the item below since both trace back to the same routing ambiguity.
- `~~Duplicate _on_message registration in groups 0 and 1~~` — **DONE (2026-07-19)**: confirmed as a live bug, not just a smell — python-telegram-bot evaluates handler groups independently, so every private-chat text message was processed twice (double rate-limit consumption, double agent dispatch, two replies per message). Removed the redundant group=1 registration; handler registration extracted into `_register_handlers()` for testability (`interfaces/telegram.py`, `tests/test_telegram_handler_registration.py`).
- `~~Telegram reaches into pairing._failed_attempts~~` — **DONE (2026-07-19)**: added `PairingManager.attempts_remaining()` (`core/safety.py`), `interfaces/telegram.py` now calls it instead of the private attr. Also fixed a stale message found alongside it: the locked-chat reply said "Restart the bot to try again" — no longer true since §1.4's lockout persistence; now points to the administrator instead.
- `~~No HTTP streaming/SSE~~` and the `~~notifier.get_and_clear() out-of-band capture~~` (`http.py:139-148`) race condition — **DONE (2026-07-25)**: added `GET /message/stream` SSE endpoint to `interfaces/http.py` that streams agent responses in real-time as notifications arrive. Fixed the race condition by adding per-chat_id asyncio.Queue and per-chat_id Lock to `HTTPNotifier` (`core/notifier.py`) — notifications are now streamed via the queue while the agent processes, and `get_and_clear()` is protected by the lock for concurrent requests. `notify_done()` signals stream completion; `stream_queue()` helper yields events for SSE consumption. Added to `RouterNotifier` for proper dispatch.
- `~~Echo agent is still registered in production~~` — **DONE (2026-07-19)**: added `DEBUG_ECHO_AGENT` (`core/config.py`, default `false`) — `main.py` only constructs/registers `EchoAgent` when set. Before this, `routable=False` only excluded it from the LLM content-classifier's candidate pool; it was still reachable via explicit `@echo` tagging (`bus.registered_agents` included it unconditionally) and even via classifier hallucination, since `_resolve_agent()` only checks `picked in self._agents`, not membership in the offered candidate set.
- `~~No "which agent am I talking to?" indicator~~` — **DONE (2026-07-25)**: routing turned out to be even less predictable than "sticky" implies — `bus._resolve_agent()` runs explicit `@tag` → LLM content-classifier (can silently switch agents based on message content alone) → sticky last-agent → first-registered, so the active agent can change without the user doing anything. `BaseAgent.reply()` (`agents/base.py`) — the single choke point every agent's conversational response passes through — now prefixes the delivered text with `[{agent_name}]`. `AgentResponse.text` itself (used for memory and HTTP's JSON) stays unprefixed; HTTP already returns a separate structured `"agent"` field, which is the more appropriate indicator for an API consumer than a text prefix.

---

## 7. Code architecture & hygiene

- **~~⭐ Declarative schedules in `BaseAgent`.~~** — **DONE (2026-07-24)**...
- **~~Agent auto-discovery instead of main.py surgery.~~** — **DONE (2026-07-25)**: `core/agent_discovery.py` scans `agents/*/agent.py` for `BaseAgent` subclasses at startup; removed `_patch_main()` from `core/agent_creator.py` since new agents are auto-detected on restart.
- **~~`async` functions doing blocking I/O~~** — **DONE (2026-07-25)**: `SkillLoader.find_relevant()`/`load_all()` (`core/skill_loader.py`) now run their `Path.glob`/`read_text` scans via `asyncio.to_thread()` instead of blocking the event loop directly. `file_tool`'s methods turned out not to be `async` in the first place (`read_file`/`write_file` are plain sync methods) — that half of the original claim was stale.
- **~~Scheduler job-ID collisions:~~** — **DONE (2026-07-25)**: Job IDs now use `f"{agent}_{task}_{cron}"` instead of just `f"{agent}_{task}"` to prevent collisions when multiple agents use the same task name. Also added error isolation in `_fire_cron_job()` to log errors without crashing.
- `~~Config-worthy hardcodes: MAX_FAILED_ATTEMPTS, approval default timeout, _MAX_READ_BYTES, skill _MIN_SCORE, heartbeat default~~` — **DONE (2026-07-19)**: `PAIRING_MAX_FAILED_ATTEMPTS`, `APPROVAL_DEFAULT_TIMEOUT`, `SKILL_MIN_SCORE` added to `Settings`/`.env.example`, threaded through `Safety`→`PairingManager`/`ApprovalGate` and `main.py`'s `SkillLoader()` construction. `_MAX_READ_BYTES` became a `FileTool.__init__(max_read_bytes=...)` constructor override rather than a `Settings` field — `FileTool` has zero production callers currently (only tests/docstring), so a `Settings` field would be unreachable config; revisit if it's ever wired into an agent. Heartbeat default was already a working `Settings` field (`heartbeat_interval_minutes`) — the actual fix was `main.py` reaching into `scheduler._heartbeat_minutes` (private attr) directly; added a public `Scheduler.set_heartbeat_minutes()`, matching the existing `set_bus()` pattern.
- **~~Quiet-hours window names (only two, hardwired)~~** — **DONE (2026-07-25)**: `Settings.quiet_hours_windows` (`core/config.py`) replaces the two fixed fields with a `list[dict]` of `{name, start, end, allowed}`, driven by `QUIET_HOURS_WINDOWS` (comma-separated names) plus per-name `QUIET_HOURS_<NAME>_{START,END,ALLOWED}` env vars — same convention as the existing `APPROVAL_TIMEOUTS` parser. `core/quiet_hours.py`'s `is_quiet_hours()`/`should_notify()` now iterate the list generically instead of a hardcoded 2-entry dict; adding a third window (e.g. `focus_block`) needs zero code changes. Env var names for the two defaults changed (`QUIET_HOURS_MORNING_START` → `QUIET_HOURS_MORNING_ROUTINE_START`) — updated in `README.md`; wasn't in `.env.example` before this pass, now documented there.
- **~~FileTool: no write cap, non-atomic writes, unbounded read cache~~** — **DONE (2026-07-25)**: `write_file()` now rejects content over `max_write_bytes` (default 100 KB, same as the read cap) and writes via `tempfile.mkstemp()` + `os.replace()` in the target's own directory, so a crash mid-write can't leave a corrupt/truncated file. The read cache (`core/file_tool.py`) is now an `OrderedDict` bounded by `max_cache_entries` (default 100) with LRU eviction instead of growing unbounded.
- `~~Dead cruft: setup.sh step renames a long-gone skill-loader.py; _snake alias in agent_creator~~` — **DONE (2026-07-19)**: removed the stale rename step from `setup.sh` (subsequent steps renumbered) and the redundant `_snake()` wrapper in `core/agent_creator.py` (its two call sites now call `_to_snake()` directly).
- **~~`_send_progress` is a no-op so `/newagent` looks frozen during generation~~** — **DONE (2026-07-25)**: `AgentCreator.__init__` now takes an optional `notifier` parameter; `_send_progress()` sends through it when provided. `main.py` passes the `RouterNotifier`, so progress messages reach Telegram, CLI, and HTTP users.
- **`WellbeingAgent` skill files loaded but never read.** — **DONE (2026-07-28)**: Found 2026-07-25 while fixing `test_wellbeing.py`'s `_build_morning_message`/`_pick_message` gap (PR #22): `_do_morning()` called `self._load_skill(_SKILL_MORNING)` only to check its truthiness — the skill markdown content was never actually used to build the message, and both branches produced identical text, so it was dead weight and got removed along with `_SKILL_MORNING`. The same pattern still exists in `_do_evening()` (`_SKILL_EVENING`, `agents/wellbeing/agent.py:257`) and `_do_bedtime()` (`_SKILL_BEDTIME`, `agents/wellbeing/agent.py:275`) — `skills/evening-wind-down.md` and `skills/bedtime-reminder.md` are read from disk but their content isn't threaded into the message, same as morning was. All the reading `_load_skill` machinery from the agent was removed from the agent. 
---

## 8. Testing & ops

- `~~⭐ No unit-test suite for core~~` — **DONE (2026-07-19)**: `quiet_hours` and `bus._resolve_agent` turned out to already have coverage (`tests/test_wellbeing.py`, `tests/test_bus_routing.py::TestResolveAgent`) — that part of this item was stale. The four genuine gaps are now closed: `notifier._split_message` (`tests/test_notifier_split_message.py`), `file_tool._validate_path` (`tests/test_file_tool_validate_path.py`, including symlink-escape and sibling-prefix cases), the Railway output parsers (`tests/test_railway_output_parsers.py`, including a pinned contract test for the exact `ACTIVE`/`SUCCESS`/`DEPLOYED` strings `get_health_summary()`'s healthy check depends on), and `safety` lockout/approval-timeout (`tests/test_safety_core.py`, distinct from `test_safety_persistence.py`'s StateStore coverage). `test_projects.py` was also moved from repo root into `tests/` and its stale `LLMResult` mock fixed, so `pytest tests/` now collects it. `test_integration.py`'s hand-rolled `print(PASS/FAIL)` script and `pytest.ini` still coexist unreconciled — that reconciliation is a separate, larger item.
- `~~Untested new surface: Telegram _on_file ingestion path~~` — **STALE**: no `_on_file` handler exists in the codebase. `@agent` routing already has interface-level coverage in `tests/test_interface_routing.py`; remaining Telegram handlers are covered by `tests/test_telegram_interface.py`.
- `~~No CI~~` — **DONE (2026-07-22)**: `.github/workflows/pytest.yml` runs `pytest tests/ -q` on push to `master` and PRs, using Python 3.12 with pip cache.
- `~~Backups are manual~~` — **DONE (2026-07-22)**: `scripts/backup.sh` backs up `sessions.db`, `memory/context`, `memory/knowledge`, and agent `state.json` files. `modular-agents-backup.service` / `.timer` installed by setup.sh run daily with 7-day retention.
- `~~No log rotation~~` — **DONE (2026-07-22)**: `scripts/logrotate.conf` rotates the manual `logs/bot.log` path daily with 7 compressed archives. Installed to `/etc/logrotate.d/modular-agents-bot` by setup.sh.
- `~~RUNBOOK drift~~` — **DONE (2026-07-22)**: outdated lockout recovery guidance updated to document admin unlock endpoint.
- `~~setup.sh doesn't check for gh/railway CLIs~~` — **DONE (2026-07-19)**: added step 7 ("External CLIs (optional)") — warns (doesn't fail) if either is missing, with install links. Also fixed a related bug found while touching this file: step 10's integration-test runner still pointed at `test_integration.py` in the repo root, which moved to `tests/test_integration.py` — it was silently no-op'ing on every run.

---

## 9. Suggested sequencing

| Phase | Theme | Items | Status |
|---|---|---|---|
| A — make it real | Tools actually execute | 1.1, 1.2, 2.1 | 1.1, 1.2, 2.1 ✅ all done — Phase A complete |
| B — make it survive | Restart persistence + notifier honesty | 1.4, 4 (notifier, chat_ids[0]), storage connection reuse | 1.4, notifier, chat_ids[0], storage connection reuse ✅ all done — Phase B complete |
| C — make it usable | Routing + interface parity | 1.3, 6 (CLI @agent, agent indicator), echo removal | 1.3 ✅ done incl. CLI @agent |
| D — make it safe | Trust model + creator gate + systemd hardening | 3 (lockout recovery done) | open |
| E — make it last | Tests, CI, backups, FTS5, retention | 8, 5 | done |

Quick wins doable in an afternoon: ~~declarative `SCHEDULES` on BaseAgent~~, ~~composio-anthropic in requirements~~ (done), ~~CLI @agent parsing~~ (done), ~~time.monotonic() in rate limiter~~ (done), ~~unencrypted-DB warning~~ (done), ~~StartLimitBurst → [Unit]~~ (done), ~~attempts_remaining() accessor~~ (done), ~~RUNBOOK grep fix~~ (done), ~~duplicate Telegram handler removal~~ (done), ~~echo debug-gate~~ (done), ~~admin unlock endpoint~~ (done), ~~agent auto-discovery~~ (done), ~~scheduler job-ID collisions~~ (done), §2 (LLM layer) fully closed, §8's core unit-test suite gap closed.
