# Semi Autonomous Modular Agent System — Architecture & Design Document
> v1.0 · Python · Telegram · WSL2 / Linux  
> Compiled from: OpenClaw, NanoBot, ZeroClaw, Agent Zero, IronClaw, NanoClaw analysis

---

## Table of Contents

1. [Design Philosophy](#1-design-philosophy)
2. [Top-Level Architecture](#2-top-level-architecture)
3. [Core Layer](#3-core-layer)
4. [Agent Layer](#4-agent-layer)
5. [Skills System](#5-skills-system-skillmd)
6. [Memory System](#6-memory-system)
7. [Safety & Execution Control](#7-safety--execution-control)
8. [Folder Structure](#8-folder-structure)
9. [Feature Decisions from Framework Analysis](#9-feature-decisions-from-framework-analysis)
10. [Implementation Roadmap](#10-implementation-roadmap)

---

## 1. Design Philosophy

This framework is built on a simple premise: a single monolithic AI agent that tries to do everything is the wrong abstraction. Instead, we build purpose-specific agents that share a common infrastructure backbone — each expert in its own domain, independently deployable, but speaking the same internal language.

Three principles guide every decision:

**Modularity over completeness**  
Add a new agent by dropping a file. Remove one without touching anything else. The framework grows with you.

**Protocol-based everything**  
Every external dependency — LLM provider, notification channel, storage backend — is behind an interface. Swap Claude for GPT, or Telegram for Slack, in one file.

**Behavior via text, not code**  
Agent behavior is defined in `SKILL.md` markdown files. Improve how an agent works by editing text, not Python.

> **Inspiration sources**  
> - **NanoBot** — message bus + clean channel separation  
> - **ZeroClaw** — trait/interface contracts for all components  
> - **Agent Zero** — SKILL.md pattern + solution memory  
> - **NanoClaw** — session auto-compaction  
> - **IronClaw** — execution approval gates  

---

## 2. Top-Level Architecture

The system has four layers. Each layer has a single responsibility and communicates with adjacent layers through defined interfaces only.

```
┌─────────────────────────────────────────────────────────────┐
│                   TELEGRAM  (I/O Layer)                      │
│     Inbound: messages, voice, files                          │
│     Outbound: text, media, inline buttons                    │
└─────────────────────────────┬───────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────┐
│                MESSAGE BUS  (Event Layer)                    │
│   Typed events: UserMessage, ScheduledTask,                  │
│   HeartbeatTick, WebhookEvent                                │
│   Agents subscribe — no direct coupling                      │
└──────────────┬──────────────────────────┬───────────────────┘
               │                          │
┌──────────────▼──────────┐  ┌────────────▼────────────┐  ┌──────────────┐
│     Business Agent      │  │      DevOps Agent        │  │  Future...   │
│     (supervised)        │  │      (autonomous)        │  │              │
└──────────────┬──────────┘  └────────────┬────────────┘  └──────────────┘
               └──────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────┐
│                  CORE LAYER  (Shared)                        │
│   config · llm · notifier · storage                          │
│   scheduler · memory · safety · logger                       │
└─────────────────────────────────────────────────────────────┘
```

> **Key design decision: Message Bus over Direct Dispatch**  
> Agents subscribe to event types rather than being called directly by an orchestrator. This means the Business Agent never imports the DevOps Agent. Adding a third agent requires zero changes to existing code. The bus routes; agents handle.

---

## 3. Core Layer

The core layer is built once and never reimplemented per agent. It exposes clean Protocol-based interfaces so any component can be swapped without touching agent code.

### 3.1 Component Reference

| Module | Responsibility |
|---|---|
| `config.py` | Loads and validates `.env`. Exposes typed settings object. Fails fast if required keys are missing. Single source of truth for all configuration. |
| `llm.py` | Single shared LLM client. Wraps Kilo/OpenRouter/Ollama/Anthropic SDKs behind a Protocol. Configurable per-agent (model, temperature, max tokens). Swap providers in one place. |
| `notifier.py` | Telegram send/receive abstraction. Agents never import `python-telegram-bot` directly. Future channels (Slack, Discord) implement the same `Notifier` Protocol. |
| `storage.py` | SQLite wrapper for session history. Async interface. Handles all DB connection management. Agents call `save_message()` and `search_history()` only. |
| `memory.py` | Two-layer memory. Layer 1: SQLite sessions (queryable history). Layer 2: Markdown files (preferences, personal context, projects). Agents call `get_context()` and `save_solution()`. |
| `scheduler.py` | Wraps APScheduler. Agents declare their cron jobs at startup via `register_schedule()`. Includes heartbeat tick events every N minutes. |
| `safety.py` | Approval gates per agent. Dangerous command blocklist. `.env` permission checks. Three modes: `read_only`, `supervised`, `autonomous`. Configured per agent in `.env`. |
| `skill_loader.py` | Discovers relevant `SKILL.md` files for a given task. Injects their content into the LLM prompt context. |
| `bus.py` | Message bus and typed event definitions. Handles agent registration and event dispatch. |
| `logger.py` | Structured JSON logging. Consistent format across all agents. Includes agent name, event type, and duration on every entry. |

### 3.2 Protocol Definitions

Every replaceable component implements a Python Protocol — the contract each implementation must satisfy:

```python
class LLMProvider(Protocol):
    async def complete(
        self,
        messages: list[Message],
        system: str,
        model: str,
        max_tokens: int
    ) -> str: ...

class Notifier(Protocol):
    async def send(self, chat_id: str, text: str) -> None: ...
    async def send_media(self, chat_id: str, path: str) -> None: ...

class MemoryStore(Protocol):
    async def save_message(
        self, session_id: str, role: str, content: str, agent: str
    ) -> None: ...
    async def search_history(
        self, query: str, agent: str | None, limit: int
    ) -> list[Message]: ...
    async def get_context(self, key: str) -> str: ...
    async def save_solution(
        self, agent: str, topic: str, content: str
    ) -> None: ...
```

---

## 4. Agent Layer

Each domain agent is a self-contained unit. It owns its skills, its tools, its scheduled jobs, and its memory context. The only thing it shares is the core layer.

### 4.1 Base Agent Interface

Every agent implements this contract. The bus only ever calls methods on this interface:

```python
class BaseAgent(ABC):
    name: str            # e.g. 'business', 'devops'
    description: str     # used by the bus for routing
    autonomy_level: str  # 'read_only' | 'supervised' | 'autonomous'

    @abstractmethod
    async def handle(self, event: AgentEvent) -> AgentResponse:
        """Process an incoming event and return a response."""

    @abstractmethod
    async def register_schedules(self, scheduler: Scheduler) -> None:
        """Declare cron jobs and heartbeat handlers at startup."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the agent and its dependencies are healthy."""
```

### 4.2 Request Lifecycle

Every message from the user goes through the same pipeline inside each agent:

```
1. Receive AgentEvent from message bus

2. SkillLoader.find_relevant(task, agent_skills_dir)
   └─ Keyword/embedding match against skill file names + descriptions
   └─ Returns list of relevant SKILL.md content strings

3. Memory.get_context('preferences') + Memory.get_context('projects')

4. Memory.search_history(query, agent=self.name, limit=6)

5. Build LLM prompt:
   └─ System:  agent persona + injected skills + personal context
   └─ History: last N messages from SQLite
   └─ User:    current message

6. Safety.check(proposed_action, autonomy_level)
   └─ supervised:  send approval request to Telegram, wait for confirm
   └─ autonomous:  proceed immediately

7. Execute action / call tool

8. Memory.save_message(session_id, 'assistant', response, agent)

9. If solution found → Memory.save_solution(agent, topic, content)

10. Notifier.send(chat_id, response)
```

---

## 5. Skills System (`SKILL.md`)

Skills are markdown files that define how an agent should handle a specific type of task. They are loaded dynamically into context — no code changes needed to improve agent behavior.

> **Why Markdown, not code**  
> Changing agent behavior should never require a code deploy. A `SKILL.md` file is editable by anyone, version-controllable, and readable without a development environment. This is the core insight from Agent Zero's SKILL.md standard.

### 5.1 Skill File Structure

```markdown
# SKILL: morning-briefing

## Trigger
Morning briefings, daily summaries, day-start reports

## Steps
1. Pull calendar events for today and tomorrow from Google Calendar
2. Check email for messages marked urgent or from key contacts
3. Fetch weather for configured location
4. Check top 3 priorities from projects.md

## Output Format
- Lead with weather + one-line day summary
- Calendar: list events with time, location, prep notes if any
- Email: show subject + sender only (no body) for urgent items
- Priorities: numbered list, most important first
- Keep total length under 300 words

## Edge Cases
- If no calendar events: mention it, suggest a focus block
- If no urgent email: confirm inbox is clear
```

### 5.2 Skills vs Solutions

These are two different things serving different purposes:

| | Skills | Solutions |
|---|---|---|
| **Written by** | You (human) | Agent (auto-generated) |
| **Purpose** | Define how to approach a task | Record what actually worked |
| **Scope** | Agent-specific | Cross-agent, shared |
| **Location** | `agents/<name>/skills/` | `core/solutions/<agent>/` |
| **Updated** | Manually, when you want to change behavior | Automatically, after successful executions |

### 5.3 Folder Layout

```
agents/
  business/
    skills/
      morning-briefing.md     ← daily digest
      email-triage.md         ← how to prioritize and respond
      calendar-blocking.md    ← time-blocking rules and logic
      weekly-review.md        ← Friday review generation
  devops/
    skills/
      deploy-checklist.md     ← pre/post deploy steps
      incident-response.md    ← alerting and triage steps
      pr-review.md            ← code review criteria

core/
  solutions/                  ← cross-agent, auto-generated
    business/
      briefing-format-v2.md
    devops/
      db-migration-fix.md
```

### 5.4 Skill Loader Logic

```python
class SkillLoader:
    async def find_relevant(
        self,
        task: str,
        skills_dir: str,
        max_skills: int = 3
    ) -> list[str]:
        # 1. Read all .md files in skills_dir
        # 2. Score each by keyword overlap with task
        # 3. Optional: semantic similarity via embeddings
        # 4. Return top-N as strings for prompt injection
        ...
```

---

## 6. Memory System

Memory is split across two storage types based on what each type is genuinely good at. The split is deliberate.

### 6.1 The Two-Layer Model

**Layer 1 — SQLite: Session History**

Best for structured, queryable data that accumulates over time:
- Full conversation history per session
- Cross-session search: *"what did we discuss last Tuesday?"*
- Agent-specific filtering: business vs devops logs
- Aggregated queries: *"all DevOps incidents this month"*

```sql
-- Schema
messages(id, session_id, agent, role, content, ts)
sessions(id, agent, started_at, summary)
```

**Layer 2 — Markdown: Context & Knowledge**

Best for semi-structured info you want to read and edit directly:

| File | Contents |
|---|---|
| `preferences.md` | Timezone, name, tone, notification preferences |
| `personal.md` | Background context always injected into prompts |
| `projects.md` | Ongoing work, current status, priorities |
| `solutions/<agent>/` | Agent-learned patterns from past executions |

> The key advantage: you can open `preferences.md` and edit it directly. No UI, no database client. The agent reads it fresh on every relevant call.

### 6.2 Session Auto-Compaction

Inspired by NanoClaw. When a session exceeds a configurable token threshold, the memory layer automatically summarizes the oldest portion. The agent never hits context limits on long-running conversations.

```python
COMPACTION_THRESHOLD = 8000  # tokens

async def get_session_context(session_id: str) -> list[Message]:
    messages = await fetch_all(session_id)
    if token_count(messages) > COMPACTION_THRESHOLD:
        old = messages[:-20]           # keep last 20 messages intact
        summary = await llm.summarize(old)
        await save_summary(session_id, summary)
        return [summary_message(summary)] + messages[-20:]
    return messages
```

---

## 7. Safety & Execution Control

Agents that touch real systems — email, calendar, deployments — need guardrails. The safety layer provides them without requiring agents to implement their own checks.

### 7.1 Autonomy Levels

| Mode | Can Do Freely | Requires Approval | Default For |
|---|---|---|---|
| `read_only` | Read files, search, query APIs | Any write, send, or execute action | — |
| `supervised` | Read + low-risk writes (e.g. draft email) | Send email, calendar changes, API POSTs | Business Agent |
| `autonomous` | All read + write actions | Destructive ops (delete, deploy to prod) | DevOps Agent |

### 7.2 Approval Flow (Supervised Mode)

```
Agent proposes action: "Send email to client@company.com"
         │
         ▼
Safety layer detects: action_type = SEND_EMAIL
                      autonomy_level = supervised
         │
         ▼
Telegram message sent to user:
  "⚠️ Approval needed: Send email to client@company.com?"
  [Approve ✓]  [Reject ✗]  [See Draft]
         │
         ▼
User taps Approve → action executes
User taps Reject  → agent explains and stops
Timeout (5 min)   → action cancelled, user notified
```

### 7.3 Dangerous Command Blocklist

Inspired by PicoClaw and NanoBot. Always blocked regardless of autonomy level:

```python
BLOCKED_PATTERNS = [
    r'rm\s+-rf',           # recursive delete
    r'dd\s+if=',           # disk wipe
    r'mkfs\.',             # format filesystem
    r'chmod\s+777',        # world-writable
    r'curl.+\|\s*bash',    # curl-pipe-bash
    r'sudo\s+rm',          # sudo delete
    r'shutdown|reboot',    # system restart
]
```

---

## 8. Folder Structure

```
framework/
  ├── core/
  │   ├── config.py           ← env loading, typed settings
  │   ├── llm.py              ← LLM provider Protocol + implementations
  │   ├── notifier.py         ← Telegram adapter (Notifier Protocol)
  │   ├── storage.py          ← SQLite session store
  │   ├── memory.py           ← two-layer memory interface
  │   ├── scheduler.py        ← APScheduler + heartbeat
  │   ├── safety.py           ← approval gates + blocklist
  │   ├── skill_loader.py     ← SKILL.md discovery + injection
  │   ├── bus.py              ← message bus + event types
  │   └── logger.py           ← structured JSON logging
  ├── agents/
  │   ├── base.py             ← BaseAgent ABC
  │   ├── business/
  │   │   ├── __init__.py
  │   │   ├── agent.py        ← BusinessAgent implementation
  │   │   ├── tools/          ← calendar, email, asana, etc.
  │   │   └── skills/         ← SKILL.md files
  │   ├── devops/
  │   │   ├── __init__.py
  │   │   ├── agent.py        ← DevOpsAgent implementation
  │   │   ├── tools/          ← github, deploy, monitor, etc.
  │   │   └── skills/         ← SKILL.md files
  │   ├── librarian/
  │   │   ├── agent.py        ← knowledge ingestion + weekly digest
  │   │   ├── skills/         ← ingest-resource, knowledge-query, knowledge-digest
  │   │   └── state.json      ← per-note surfacing metadata
  │   └── projects/
  │       ├── agent.py        ← chief of staff: progress log + weekly kickoff
  │       ├── skills/         ← progress-update, weekly-kickoff, project-status
  │       └── state.json      ← per-project momentum tracking
  ├── memory/
  │   ├── sessions.db         ← SQLite (auto-created on first run)
  │   ├── context/
  │   │   ├── preferences.md
  │   │   ├── personal.md
  │   │   └── projects.md     ← includes agent-maintained "## Progress log"
  │   ├── knowledge/          ← distilled notes from ingested resources + INDEX.md
  │   ├── inbox/              ← raw downloaded Telegram files (gitignored)
  │   └── solutions/
  ├── main.py                 ← startup, wires everything together
  ├── .env                    ← secrets (chmod 600)
  └── requirements.txt
```

---

## 9. Feature Decisions from Framework Analysis

### 9.1 Adopted Patterns

| Pattern | Source | Why Adopted |
|---|---|---|
| Message Bus Architecture | NanoBot | Decouples agents completely. Zero-touch extension. |
| Protocol / Trait Contracts | ZeroClaw | Swap any provider without touching agent code. |
| SKILL.md Files | Agent Zero | Behavior improvement without code changes. |
| Solution Memory | Agent Zero | Agents compound knowledge over time automatically. |
| Session Auto-Compaction | NanoClaw | Essential for long-running business conversations. |
| Two-Layer Memory | NanoBot / PicoClaw | SQLite for queries, Markdown for human-editable context. |
| Execution Approval Gates | IronClaw | Business Agent needs human-in-the-loop for sensitive actions. |
| Dangerous Command Blocklist | PicoClaw / NanoBot | Baseline safety, no configuration required. |
| Heartbeat System | OpenClaw / PicoClaw | Proactive agent behavior, not just reactive. |

### 9.2 Deferred Patterns

| Pattern | Source | Reason for Deferral |
|---|---|---|
| WASM Sandbox | IronClaw | Overkill for personal productivity. Adds build complexity. |
| Docker Isolation | Agent Zero | WSL2 provides sufficient isolation for this use case. |
| Agent Team Collaboration | TinyClaw | Unnecessary for 2-agent setup. Revisit at 4+ agents. |
| Voice Wake Word | OpenClaw | Telegram voice messages cover this use case sufficiently. |
| Multi-Provider AI Failover | IronClaw | Implemented — Kilo/OpenRouter/Ollama/Anthropic providers with automatic fallback. |
| Local LLM (Ollama) | Multiple | Implemented — supports self-hosted models like Llama and No-Lama. |
| Webhook Triggers | OpenClaw / ZeroClaw | Not needed for current automation scope. |

---

## 10. Implementation Roadmap

Recommended build order. Each phase produces working, testable output before the next begins.

### Phase 1 — Core Layer
> **Deliverable:** A working Python project that can load config, connect to Telegram, send a message, and write a log line.

- [x] `config.py` with `.env` loading and validation
- [x] `logger.py` with structured JSON output
- [x] `notifier.py` Telegram adapter
- [x] `storage.py` SQLite session store
- [x] `bus.py` event types and routing
- [x] "Hello world" agent that echoes Telegram messages — validates the full stack

### Phase 2 — Memory & Skills
> **Deliverable:** Agents that remember context across sessions and load skills dynamically.

- [x] `memory.py` two-layer implementation (SQLite + Markdown)
- [x] `skill_loader.py` with keyword matching
- [x] `scheduler.py` with cron + heartbeat
- [x] `safety.py` blocklist + approval gates
- [x] Initial `memory/context/` markdown files (`preferences.md`, `personal.md`, `projects.md`)

### Phase 3 — Business Agent
> **Deliverable:** Morning briefing, email triage, and calendar access via Telegram.

- [x] `BusinessAgent` with `supervised` autonomy mode
- [x] First 3 SKILL.md files: `morning-briefing.md`, `email-triage.md`, `calendar-blocking.md`
- [ ] Google Calendar + Gmail tools
- [x] Approval gate integration with Telegram inline buttons
- [x] Cron job: daily morning briefing

### Phase 4 — DevOps Agent
> **Deliverable:** GitHub monitoring, deploy pipeline, and incident alerts via Telegram.

- [x] `DevOpsAgent` with `autonomous` autonomy mode
- [x] GitHub tools — `tools/github.py` wrapping `gh` CLI (PRs, issues, CI runs, health summary)
- [x] Railway tools — `tools/railway.py` wrapping `railway` CLI (status, deploy, logs, rollback, env vars)
- [x] `tools/cli_runner.py` — shared async subprocess runner with retry-once logic
- [x] Cron-based health checks and heartbeat alerts
- [x] SKILL.md files: `deploy-checklist.md`, `incident-response.md`, `pr-review.md`

> **Repo layout for tools:**
> ```
> agents/devops/tools/
>   __init__.py       ← build_tools() factory
>   cli_runner.py     ← shared async CLI runner, ToolError, retry logic
>   github.py         ← GitHubTool wrapping gh CLI
>   railway.py        ← RailwayTool wrapping railway CLI
> ```
> Repos resolved from `memory/context/projects.md` at runtime — no restart needed to add a repo.
> Railway project/service/environment also resolved from `projects.md`.

---

### Phase 5 — Knowledge & Project Momentum
> **Deliverable:** Saved resources become actionable knowledge; projects get per-project momentum tracking.

- [x] `LibrarianAgent` — ingests PDFs, voice notes, audio, and URLs sent over Telegram
  - Telegram interface downloads document/voice/audio files to `memory/inbox/` and routes them to the librarian
  - Extraction: `pypdf` for PDFs, OpenAI Whisper for audio (optional `OPENAI_API_KEY`), `WebTool` for URLs
  - LLM distills each resource into a structured note (summary, key ideas, next actions, related projects) in `memory/knowledge/`
  - A short version is saved via `memory.save_solution()` so other agents surface it by keyword
  - Saturday-morning digest resurfaces the least-seen notes with pending actions (anti-staleness loop)
  - Optional graphify integration (`agents/librarian/tools/graphify.py`): if the `graphify` CLI is installed (`uv tool install graphifyy`), each ingest triggers a background `graphify update` over `memory/knowledge/`, and queries add graph-traversal context. Degrades to keyword search when absent.
- [x] `ProjectsAgent` — chief of staff for the project portfolio
  - `update: <project> — <what happened>` messages are parsed by the LLM and logged to `state.json` + a `## Progress log` section in `memory/context/projects.md`
  - Momentum tracking flags projects with no logged update in 7+ days
  - Monday-morning kickoff proposes the week's top 3 priorities with stale-project nudges
- [x] `@agent` message prefix for explicit routing (e.g. `@librarian`, `@projects`) — complements bus continuity routing

> **Starting point recommendation**  
> Begin with Phase 1 + the "hello world" echo agent. This validates the entire stack — config, bus, notifier, logger — before adding any domain logic. Then complete Phase 2 fully before writing either real agent. A well-built core makes Phases 3 and 4 straightforward.

## HTTP API Interface

The HTTP interface (`interfaces/http.py`) exposes the agent bus as a REST API using FastAPI + uvicorn. It runs simultaneously with Telegram and CLI from a single `python main.py`.

### Use Cases

- **Remote / VM deployment**: run the stack headless on a server, call agents from any client on the network via HTTP
- **External agent integration**: an AI orchestrator (LangChain, CrewAI, custom Claude agent) can `POST /message` to delegate sub-tasks to specialised agents
- **Scripts & automation**: cron jobs or CI pipelines send messages and receive structured text responses
- **Multi-stack composition**: multiple instances of this stack can call each other's HTTP APIs to coordinate across agent sets

### Pairing Flow

The HTTP API reuses the same pairing code printed at startup. Callers must first `POST /pair` with the code to receive a session token (UUID). The token is then sent as `Authorization: Bearer <token>` on all subsequent requests. Tokens are in-memory only and cleared on restart.

### Security Considerations

- **Default bind to `127.0.0.1`**: prevents remote access unless `HTTP_HOST=0.0.0.0` is set in `.env`. Only expose on a trusted or firewalled network.
- **Pairing code as shared secret**: transmit it securely (SSH, encrypted channel) — never over plain HTTP on an untrusted network.
- **Token lifetime**: tokens expire after `SESSION_TTL_HOURS` (default 24h) and are rejected with 401. `DELETE /session` endpoint allows explicit logout.
- **No HTTPS by default**: for local use, HTTP is fine. For network-exposed deployments, run behind a reverse proxy (nginx, Caddy) with TLS.
- **Rate limiting**: not included in v1. For network-exposed deployments, add rate limiting at the reverse proxy layer.

### Interface Routing

Agents and `Safety` receive a `RouterNotifier` that dispatches notifications to the correct interface based on `chat_id` prefix:

| chat_id pattern | Interface | Notifier |
|---|---|---|
| All digits (e.g. `987654321`) | Telegram | `TelegramNotifier` |
| `"cli"` | Terminal REPL | `CLINotifier` |
| `"http_<token[:8]>"` | HTTP API | `HTTPNotifier` |
