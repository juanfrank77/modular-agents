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

**DevOps Agent** — your infrastructure assistant
- GitHub digest: open PRs, failing CI, stale branches
- Deployment pipeline via Railway CLI
- Incident detection and response guidance
- Automated health checks every 15 minutes

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
- **Memory** is two-layer: SQLite for conversation history, Markdown files for your preferences and context
- **Tools** are thin wrappers around CLI tools you already have installed and authenticated
- **Safety** is built in — supervised agents ask for approval before consequential actions

---

## Requirements

- Python 3.11+
- A [Telegram bot token](https://core.telegram.org/bots#botfather) (free, takes 2 minutes)
- An [Anthropic API key](https://console.anthropic.com/) or a [Kilo API key](https://app.kilo.ai/users/sign_in?callbackPath=/profile)
- For DevOps agent: [`gh` CLI](https://cli.github.com/) and [`railway` CLI](https://docs.railway.app/develop/cli) installed and authenticated

---

## Getting started

### 1. Clone and set up

```bash
git clone https://github.com/juanfrank77/modular-agents.git
cd modular-agents
chmod +x setup.sh
./setup.sh
```

`setup.sh` handles everything: creates a virtual environment, installs dependencies, validates your `.env`, sets up directories, and runs the test suite.

On WSL2 without systemd:
```bash
SKIP_SYSTEMD=1 ./setup.sh
```

### 2. Configure your environment

```bash
cp .env.example .env
chmod 600 .env
nano .env   # add your tokens
```

Required keys:
```
TELEGRAM_BOT_TOKEN=your_token_here
ANTHROPIC_API_KEY=your_key_here
TELEGRAM_ALLOWED_CHAT_IDS=your_chat_id   # get this from @userinfobot on Telegram
```

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

The pairing code will be printed to the console. Send it to your bot on Telegram to authenticate.

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

---

## Project structure

```
modular-agents/
├── agents/
│   ├── base.py                  ← BaseAgent — inherit this for new agents
│   ├── echo/                    ← minimal example agent
│   ├── business/                ← productivity agent
│   │   ├── agent.py
│   │   └── skills/              ← Markdown skill files
│   └── devops/                  ← infrastructure agent
│       ├── agent.py
│       ├── skills/
│       └── tools/               ← gh and railway CLI wrappers
├── core/                        ← shared infrastructure (don't modify unless extending)
│   ├── bus.py                   ← message routing
│   ├── config.py                ← environment and settings
│   ├── llm.py                   ← LLM provider (Anthropic)
│   ├── memory.py                ← two-layer memory system
│   ├── safety.py                ← approval gates and blocklist
│   ├── scheduler.py             ← cron jobs and heartbeat
│   └── skill_loader.py          ← discovers and injects skill files
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