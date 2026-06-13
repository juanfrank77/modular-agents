# Semi Autonomous Modular Agents System

A system for building your own personal AI assistant with modular, domain-specific agents — each expert in their own area, sharing a common infrastructure backbone.

Built for people who want a capable AI assistant running on their own machine, connected to their own tools, without handing their data to a third-party service.

---

## What it does

You run a Telegram bot that connects to one or more AI agents. Each agent handles a specific domain. These are the first fully-functional agents:

**Business Agent** — your productivity assistant
- Morning briefings with your priorities for the day
- Email triage and draft responses
- Calendar management and time-blocking
- Weekly reviews

**Wellbeing Agent** — your wellbeing assistant
- Morning nudges with weather-based activity suggestions (run or yoga)
- Evening wind-down reminders to step away from screens
- Bedtime reminders for better sleep
- Weekly check-ins tracking morning routine consistency
- Respects quiet hours with emergency override support

**DevOps Agent** — your infrastructure assistant
- GitHub digest: open PRs, failing CI, stale branches
- Deployment pipeline via Railway CLI
- Incident detection and response guidance
- Automated health checks every hour

Each agent has its own skills (defined in plain Markdown files), its own memory, and its own autonomy level — the Business Agent asks before sending anything, the DevOps Agent acts autonomously on safe operations and asks only for destructive ones.

---

## How it works

```
You (Telegram) → Message Bus → Agent → LLM → Response
                                  ↓
                            Memory (SQLite + Markdown)
                            Skills (SKILL.md files)
                            Tools (gh CLI, railway CLI)
```

- **Agents** are Python classes that each handle a domain
- **Skills** are Markdown files that define how agents approach tasks — edit them without touching code
- **Memory** is enhanced two-layer: SQLite for conversation history, Markdown files for your preferences and context, with learned patterns
- **Tools** are thin wrappers around CLI tools you already have installed and authenticated
- **Safety** is built in — supervised agents ask for approval before consequential actions
- **Reliability** includes LLM retry logic and typing indicators during processing

---

## Requirements

- Python 3.11+
- A [Telegram bot token](https://core.telegram.org/bots#botfather) (free, takes 2 minutes)
- At least one LLM provider configured (see below)
- For DevOps agent: [`gh` CLI](https://cli.github.com/) and [`railway` CLI](https://docs.railway.app/develop/cli) installed and authenticated

#### What you'll need by feature

| Feature | Required tools/services | Setup |
|---------|----------------------|-------|
| Core bot | Telegram bot token | [Create bot with @BotFather](https://core.telegram.org/bots#botfather) |
| LLM inference | Any provider API key | See LLM Provider Options table below |
| DevOps agent | `gh` CLI, `railway` CLI | `gh auth login` and `railway login` |
| Web search | `TAVILY_API_KEY` (optional) | Get key from [tavily.com](https://tavily.com) |
| External apps (Gmail, Calendar, etc.) | `COMPOSIO_API_KEY` + OAuth | `composio login` and `composio link <service>` |

### LLM Provider Options

| Provider | Environment Variable | Description |
|----------|---------------------|-------------|
| **Anthropic** | `ANTHROPIC_API_KEY` | Claude models (most common) |
| **OpenRouter** | `OPENROUTER_API_KEY` | Access to many models via one API |
| **Kilo** | `KILO_API_KEY` | Primary provider (default) |
| **Ollama** | `OLLAMA_BASE_URL` | Local/self-hosted models (Llama, No-Lama, etc.) |

Configure at least one provider. Provider priority: Kilo → OpenRouter → Ollama → Anthropic.

---

## Getting started

### 1. Clone and set up

> **Important**: Fill out your `.env` file before running `setup.sh` so the script can validate your configuration.

```bash
git clone https://github.com/juanfrank77/modular-agents.git
cd modular-agents
cp .env.example .env
chmod 600 .env
nano .env   # add your tokens and provider configuration, then save before continuing
chmod +x setup.sh
./setup.sh
```

`setup.sh` handles everything: creates a virtual environment, installs dependencies, validates your `.env`, sets up directories, installs the systemd service file (now bundled in the repo), and runs the test suite.

On WSL2 without systemd:
```bash
SKIP_SYSTEMD=1 ./setup.sh
```

### 2. Configure your environment

Required keys:
```
TELEGRAM_BOT_TOKEN=your_token_here
```

LLM Provider (configure at least one):
```
KILO_API_KEY=your_key_here              # Kilo (default primary)
# OR
OPENROUTER_API_KEY=your_key_here          # OpenRouter
# OR
OLLAMA_BASE_URL=http://localhost:11434  # Ollama for local models
# OR
ANTHROPIC_API_KEY=your_key_here         # Anthropic (fallback)
```

Telegram access control:
```
TELEGRAM_ALLOWED_CHAT_IDS=your_chat_id   # get this from @userinfobot on Telegram
```

#### Using Ollama (self-hosted models)

If you want to use local/self-hosted models like Llama or No-Lama:

1. **Install Ollama** from https://ollama.com or run:
   ```bash
   curl -fsSL https://ollama.com/install.sh | sh
   ```

2. **Pull your model**:
    ```bash
    ollama pull llama3.2      # recommended
    ollama pull mistral       # alternative option
    ```

3. **Configure in `.env`** (default URL works for local installation):
   ```
   OLLAMA_BASE_URL=http://localhost:11434
   DEFAULT_MODEL=llama3    # or your preferred model name
   ```

4. **Start the Ollama service** (if not auto-started):
   ```bash
   ollama serve
   ```

The system will connect to Ollama automatically. No API key required.

### 3. Fill in your context

The agents read these files on every call — the more accurate they are, the better the responses:

```
memory/context/preferences.md   ← your timezone, communication style, notification rules
memory/context/personal.md      ← background context about you and your work
memory/context/projects.md      ← active projects, repos, priorities
```

See [Context files guide](#context-files) below for what to put in each one.

### 4. Run

```bash
# With systemd (auto-starts on boot, restarts on crash)
sudo systemctl start modular-agents

# Without systemd
source .venv/bin/activate
python main.py
```

#### First time setup — pairing your chat

**Using systemd?** The pairing code is in the logs, not the terminal:
```bash
journalctl -u modular-agents | grep "PAIRING CODE"
```

When the bot starts, it prints a cryptographically random pairing token to the console (32 characters):

```
================================================================================
  PAIRING TOKEN:  a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6
  Send this token to the bot on Telegram to pair.
  Or POST it to /pair on the HTTP API.
================================================================================
```

**To pair:**
1. Open Telegram and find the bot you created with @BotFather
2. Send it a message — any message — and it will reply asking for the pairing token
3. Type the 32-character token exactly as shown in the console and send it
4. The bot will reply: `✅ Paired. You can now use the bot.`
5. After 5 failed attempts, the bot locks — restart the service to reset the pairing flow

You only need to do this once per chat. The pairing persists across restarts.

**Can't find your bot on Telegram?** Search by the username you set in @BotFather (e.g. `@myagentbot`). If you haven't created a bot yet, open Telegram, search for `@BotFather`, send `/newbot`, and follow the prompts — it takes about 2 minutes.

### 5. Verify

Send any message to your bot. It should respond. Check logs if it doesn't:

```bash
journalctl -u modular-agents -f        # with systemd
# or just watch stdout if running manually
```

---

## Context files

These are plain Markdown files in `memory/context/`. Edit them any time — no restart needed.

### `preferences.md`
Your operational preferences: timezone, communication style, notification rules, work hours. Example:
```markdown
# Preferences
- Timezone: America/New_York (UTC-5)
- Communication style: concise, bullets over paragraphs
- Morning briefing: weekdays only
- Alerts outside working hours: P1 incidents only
```

### `personal.md`
Background context that gets injected into every system prompt. Keep it to a few short paragraphs — it's read on every call so length has a cost. Cover: what you do, how you work, what the agents should always know about your situation.

### `projects.md`
Your active projects and the repos/services associated with them. The DevOps agent uses this to know which GitHub repos to monitor and which Railway services to manage:
```markdown
## My project
- Status: In progress
- Priority: High
- repo: your-org/your-repo
- railway-service: api
- railway-environment: production
```

---

## Customizing agent behaviour

### Editing skills
Skills are Markdown files in `agents/<name>/skills/`. They define how agents approach specific tasks — triggers, steps, output format, edge cases. Edit them directly to change agent behaviour without touching Python.

```
agents/business/skills/
  morning-briefing.md    ← what to include in the daily briefing
  email-triage.md        ← how to categorize and respond to email
  calendar-blocking.md   ← time-blocking rules and logic

agents/devops/skills/
  deploy-checklist.md    ← pre/post deploy steps
  incident-response.md   ← severity levels and response steps
  pr-review.md           ← code review criteria
```

To add a new skill: create a new `.md` file in the relevant skills folder following the existing format. The skill loader picks it up automatically.

### Adding a new agent

**Option 1: Interactive Wizard (Extending a running system)**

Use the `/newagent` command in Telegram to create a new agent interactively. This is the fastest way to extend your running system — the wizard guides you through defining the agent's purpose, autonomy level, skills, and generates the necessary code and files automatically. Available after the initial setup.

**Option 2: Manual Creation (First-time setup)**

For initial setup or full control, create the agent manually:

See [ARCHITECTURE.md](./ARCHITECTURE.md) for the full design. The short version:

1. Create `agents/youragent/agent.py` and subclass `BaseAgent`
2. Implement `handle()`, `register_schedules()`, and `health_check()`
3. Add skill files in `agents/youragent/skills/`
4. Register the agent in `main.py`

The echo agent in `agents/echo/` is a minimal working example.

### Adjusting autonomy levels
Set in `.env`:
```
BUSINESS_AGENT_AUTONOMY=supervised   # asks before sending/writing
DEVOPS_AGENT_AUTONOMY=autonomous     # acts freely, asks for destructive ops
```

Options: `read_only` | `supervised` | `autonomous`

### Quiet hours configuration

The Wellbeing Agent respects quiet hours to avoid sending nudges at inconvenient times. Configure in `.env`:

```
QUIET_HOURS_ENABLED=true                    # toggle quiet hours on/off
QUIET_HOURS_MORNING_START=07:00             # morning routine window start
QUIET_HOURS_MORNING_END=09:30               # morning routine window end
QUIET_HOURS_MORNING_ALLOWED=wellbeing-nudge # allowed during morning quiet hours
QUIET_HOURS_EVENING_START=19:30             # evening wind-down start
QUIET_HOURS_EVENING_END=07:00               # overnight window (spans midnight)
QUIET_HOURS_EVENING_ALLOWED=wellbeing-nudge,emergency  # allowed during evening
EMERGENCY_KEYWORDS=server_down,security,data_loss,payment_failure  # bypass all quiet hours
```

The Wellbeing Agent is autonomous but will skip nudges if they fall within quiet hours. Emergency keywords (e.g., `server_down`) always bypass quiet hours.

### Giving agents access to local files

Set `LOCAL_FILE_PATHS` in `.env` to a comma-separated list of directories agents are allowed to read and write:

```
LOCAL_FILE_PATHS=/home/user/projects/drafts,/home/user/notes
```

Then pass a `FileTool` instance to your agent:

```python
from core.file_tool import FileTool
from pathlib import Path

file_tool = FileTool(allowed_paths=[Path("/home/user/projects/drafts")])
# pass file_tool to your agent's __init__
```

`FileTool` exposes `list_files(folder, pattern)`, `read_file(path)`, and `write_file(path, content)`. All paths are validated against `allowed_paths` — attempts to access files outside the allowed roots raise `PermissionError`.

### Giving agents web access

`WebTool` provides web search (via Tavily) and page scraping.

Set `TAVILY_API_KEY` in `.env` for search (scraping works without a key):

```
TAVILY_API_KEY=tvly-...
```

Use it in an agent:

```python
from core.web_tool import WebTool

web_tool = WebTool(search_api_key=settings.tavily_api_key)
results = await web_tool.search("latest Python releases", max_results=5)
page_text = await web_tool.scrape("https://example.com")
```

`search()` returns a list of `{title, url, content}` dicts. `scrape()` returns plain text capped at 20 KB with scripts and styles stripped.

### Configuring approval timeouts

When a supervised agent requests approval for a consequential action, it waits for you to tap Approve or Deny in Telegram. The timeout before auto-denying is configurable per action type:

```
APPROVAL_TIMEOUTS=WRITE_HIGH=120,EXECUTE=300,DESTRUCTIVE=600
```

| Action type | Default timeout | When it applies |
|-------------|----------------|-----------------|
| `WRITE_HIGH` | 120s | Send email, post message, calendar write |
| `EXECUTE` | 300s | Run scripts, CLI commands |
| `DESTRUCTIVE` | 600s | Delete, DB migrate, force deploy |

If you don't set `APPROVAL_TIMEOUTS`, the defaults above apply. Any action type not listed falls back to 300s.

---

## Connecting external apps (Composio)

[Composio](https://composio.dev) gives agents access to 1000+ integrations — Gmail, Google Calendar, Slack, Notion, and more — through a single Python SDK.

### Setup

```bash
uv pip install composio-anthropic
composio login
composio link gmail        # one-time OAuth per service
composio link googlecalendar
```

Set `COMPOSIO_API_KEY` and optionally `COMPOSIO_USER_ID` in `.env`:
```
COMPOSIO_API_KEY=your_composio_api_key_here
# Optional: for multi-user deployments, specify the user entity ID
COMPOSIO_USER_ID=your_composio_user_id_here
```

### Using ComposioTool in an agent

```python
from core.composio_tool import ComposioTool

composio = ComposioTool(api_key=settings.composio_api_key)
result = await composio.execute("GMAIL_FETCH_EMAILS", max_results=10)
```

The Business Agent includes ready-made `GmailTool` and `CalendarTool` wrappers in `agents/business/tools/` that map to common Composio slugs. Instantiate them with a `ComposioTool` instance.

---

## Permissions and safety

The system enforces four independent layers of access control:

**1. Chat-level** — `TELEGRAM_ALLOWED_CHAT_IDS`

Only the listed chat IDs can interact with the bot. Any other chat receives no response. **In production (ENV=production), this must be set — startup will fail if empty to prevent accidental open access.**

**2. Agent-level** — autonomy mode

Each agent runs in one of three autonomy modes (set per-agent in `.env`):

| Mode | Behaviour |
|------|-----------|
| `read_only` | May only perform read actions — no writes, no execution |
| `supervised` | Read and low-risk writes are automatic; high-risk actions require inline Approve/Deny |
| `autonomous` | Acts freely; only checks the hardcoded command blocklist |

**3. Command-level** — blocklist (defense-in-depth)

Commands are executed via `asyncio.create_subprocess_exec()` with explicit argument lists,
not through a shell. This prevents shell injection attacks regardless of special characters.
The hardcoded blocklist provides an additional layer by catching obviously destructive patterns
in action descriptions, but it is NOT a security boundary — commands can always be bypassed
via obfuscation. All CLI execution through `cli_runner.py` is inherently safe.

Extend it without code changes using `EXTRA_BLOCKED_PATTERNS` in `.env`:
```
EXTRA_BLOCKED_PATTERNS=my-org/secret-repo,DROP TABLE
```
Values are compiled as case-insensitive regex patterns and merged with the hardcoded list at startup.

**4. Action-level** — `ActionType`

Actions are classified by risk. In `supervised` mode, anything above `WRITE_LOW` requires explicit approval:

| ActionType | Risk | Example |
|------------|------|---------|
| `READ` | None | Fetch emails, list files, search |
| `WRITE_LOW` | Low | Save a draft, update a local note |
| `WRITE_HIGH` | Medium | Send email, write calendar event |
| `EXECUTE` | High | Run a script, deploy a service |
| `DESTRUCTIVE` | Critical | Delete data, force-push, drop table |

---

## Project structure

```
modular-agents/
├── agents/
│   ├── base.py                  ← BaseAgent — inherit this for new agents
│   ├── echo/                    ← minimal example agent
│   ├── business/                ← productivity agent
│   │   ├── agent.py
│   │   ├── tools/               ← GmailTool, CalendarTool (via Composio)
│   │   └── skills/              ← Markdown skill files
│   ├── wellbeing/               ← wellbeing agent
│   │   └── agent.py             ← scheduled nudges (morning, evening, bedtime)
│   └── devops/                  ← infrastructure agent
│       ├── agent.py
│       ├── skills/
│       └── tools/               ← gh and railway CLI wrappers
├── core/                        ← shared infrastructure (don't modify unless extending)
│   ├── agent_creator.py         ← interactive agent creation wizard
│   ├── bus.py                   ← message routing
│   ├── composio_tool.py         ← Composio SDK wrapper (external app integrations)
│   ├── config.py                ← environment and settings
│   ├── file_tool.py             ← local filesystem access with path allowlist
│   ├── llm.py                   ← LLM providers (Kilo/OpenRouter/Ollama/Anthropic)
│   ├── memory.py                ← enhanced two-layer memory system
│   ├── quiet_hours.py           ← quiet hours gating logic
│   ├── safety.py                ← approval gates and blocklist
│   ├── scheduler.py             ← cron jobs and heartbeat
│   ├── skill_loader.py          ← discovers and injects skill files
│   └── web_tool.py              ← web search (Tavily) and page scraping
├── memory/
│   ├── context/                 ← your preferences, personal context, projects
│   └── solutions/               ← agent-learned patterns (auto-generated)
├── main.py                      ← entry point
├── setup.sh                     ← one-shot setup script
├── ARCHITECTURE.md              ← full design document
└── RUNBOOK.md                   ← operational reference
```

---

## Operations

See [RUNBOOK.md](./RUNBOOK.md) for the full operational reference. Quick commands:

```bash
sudo systemctl status modular-agents    # is it running?
journalctl -u modular-agents -f         # live logs
sudo systemctl restart modular-agents   # restart after config change
python test_integration.py              # run the test suite
```

### Telegram Commands

| Command | Description |
|---------|-------------|
| `/model <model-id>` | Change the default LLM model for this session |
| `/newagent` | Start interactive wizard to create a new agent |
| `/planmode [agent]` | Toggle plan mode: agent shows a numbered action plan and waits for Approve/Deny before executing. Optionally target a specific agent by name. |
| `/help` | Show available commands |

---

## Design principles

This system is built on three ideas:

**Modular over monolithic** — each agent is expert in one domain. Add a new agent by dropping in a file. Remove one without touching anything else.

**Behaviour via text, not code** — agent behaviour is defined in Markdown skill files. Improving how an agent works means editing text, not deploying Python.

**Your data stays yours** — everything runs on your machine. Conversation history is in a local SQLite database. Context files are plain Markdown. No third-party service sees your data except the LLM API calls themselves.

For the full architecture and design decisions behind, see [ARCHITECTURE.md](./ARCHITECTURE.md).

---

## Acknowledgements

Framework design informed by analysis of: OpenClaw, NanoBot, ZeroClaw, Agent Zero, IronClaw, NanoClaw, TinyClaw, and PicoClaw.
