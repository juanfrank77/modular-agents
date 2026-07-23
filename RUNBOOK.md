# Modular Agents — Runbook

Operational reference for running the bot in production on WSL2/Linux.  
For architecture and design decisions, see [ARCHITECTURE.md](./ARCHITECTURE.md).

---

## Quick reference

| Task | Command |
|---|---|
| Start | `sudo systemctl start modular-agents` |
| Stop | `sudo systemctl stop modular-agents` |
| Restart | `sudo systemctl restart modular-agents` |
| Status | `sudo systemctl status modular-agents` |
| Live logs | `journalctl -u modular-agents -f` |
| Last 100 lines | `journalctl -u modular-agents -n 100` |
| Logs since boot | `journalctl -u modular-agents -b` |
| Run manually | `source .venv/bin/activate && python main.py` |

---

## First-time setup

```bash
chmod +x setup.sh
./setup.sh
```

`setup.sh` performs a full automated setup: Python venv, dependencies, directory creation, `.env` validation, systemd service installation (the `modular-agents.service` template is bundled in the repo), and integration tests.

If you're on WSL2 without systemd:
```bash
SKIP_SYSTEMD=1 ./setup.sh
```

---

## Starting and stopping

### With systemd (recommended for production)
```bash
sudo systemctl start modular-agents    # start
sudo systemctl stop modular-agents     # stop gracefully
sudo systemctl restart modular-agents  # restart (e.g. after config change)
```

The service restarts automatically on crash with a 10-second delay.  
It will not restart if it fails 5 times within 2 minutes — this protects against
config errors causing a restart loop. Check logs if this happens.

### Without systemd (WSL2 / development)
```bash
source .venv/bin/activate
python main.py
```

Use `Ctrl+C` to stop. The bot handles shutdown gracefully.

To run in the background without systemd:
```bash
nohup python main.py >> logs/bot.log 2>&1 &
echo $! > bot.pid          # save PID for later
kill $(cat bot.pid)         # stop it
```

---

## Reading logs

Logs are structured JSON (or pretty-printed in dev mode).  
Every log line includes: `ts`, `level`, `agent`, `event`, `msg`.

```bash
# Follow live
journalctl -u modular-agents -f

# Filter to a specific agent
journalctl -u modular-agents -f | grep '"agent":"devops"'

# Filter to errors only
journalctl -u modular-agents -f | grep '"level":"ERROR"'

# Filter to a specific event type
journalctl -u modular-agents | grep '"event":"heartbeat_alert"'

# Pretty-print JSON logs (requires jq)
journalctl -u modular-agents -f | jq '.'
```

To switch to human-readable logs during debugging, set in `.env`:
```
LOG_FORMAT=pretty
```
Then restart the service.

### Log rotation

Under systemd, all output goes to **journald**, which manages its own retention and caps.

For manual background runs via `nohup`, logs are written to `logs/bot.log`. `setup.sh` can install a logrotate config (`/etc/logrotate.d/modular-agents-bot`) that rotates this file daily, keeps 7 compressed archives, and uses `copytruncate` so the running process doesn't need a restart.

To trigger a manual rotation:
```bash
sudo logrotate -f /etc/logrotate.d/modular-agents-bot
```

To inspect rotated archives:
```bash
ls -lh logs/bot.log*
```

---

## Updating the bot

After pulling new code:

```bash
git pull

# Install any new dependencies
 source .venv/bin/activate
 uv pip install -r requirements.txt

# Run tests before restarting
python test_integration.py

# Restart the service
sudo systemctl restart modular-agents

# Confirm it came back up
sudo systemctl status modular-agents
journalctl -u modular-agents -n 20
```

---

## Updating agent behaviour

Agent behaviour is controlled by SKILL.md files and markdown context files.
**No restart required** for skill changes — they are loaded on every call.

| What to change | File | Restart needed? |
|---|---|---|
| How Business Agent handles email | `agents/business/skills/email-triage.md` | No |
| How DevOps Agent handles deploys | `agents/devops/skills/deploy-checklist.md` | No |
| Your preferences and timezone | `memory/context/preferences.md` | No |
| Your active projects and repos | `memory/context/projects.md` | No |
| Personal background context | `memory/context/personal.md` | No |
| Bot tokens or API keys | `.env` | Yes |
| Scheduled job times | `agents/*/agent.py` | Yes |
| New cron jobs | `agents/*/agent.py` | Yes |

---

## Managing the pairing code

The pairing code is printed to the console (and to journald) on every startup.
It's a 32-character cryptographically random token that regenerates each time the bot restarts.

To find it after startup:
```bash
journalctl -u modular-agents | grep "PAIRING TOKEN"
# or check the service output
sudo systemctl status modular-agents
```

To add a chat to the permanent allowlist instead (no pairing code needed):
1. Get the chat ID by sending a message and reading it from logs
2. Add it to `.env`: `TELEGRAM_ALLOWED_CHAT_IDS=123456789`
3. Restart the service

---

## Memory and sessions

### View conversation history
The SQLite database lives at `memory/sessions.db`.

```bash
# Open with sqlite3
sqlite3 memory/sessions.db

# View recent messages
SELECT agent, role, substr(content, 1, 80), ts
FROM messages ORDER BY ts DESC LIMIT 20;

# View sessions
SELECT id, agent, started_at, substr(summary, 1, 60)
FROM sessions ORDER BY started_at DESC LIMIT 10;

# Search by content
SELECT agent, role, content FROM messages
WHERE content LIKE '%deploy%' ORDER BY ts DESC;

.quit
```

### Clear a session (force fresh start)
```bash
sqlite3 memory/sessions.db \
  "DELETE FROM messages WHERE session_id = 'business_YOUR_CHAT_ID';"
```

### View saved solutions
```bash
ls memory/solutions/
ls memory/solutions/devops/
cat memory/solutions/devops/some-incident-fix.md
```

---

## Common failure scenarios

### Bot not responding before pairing (pre-pair verification)

If you've started the service but the bot doesn't respond to any messages, verify pairing first:

1. Check if the service is running:
   ```bash
   sudo systemctl status modular-agents
   ```
2. Get the pairing code:
   ```bash
   journalctl -u modular-agents | grep "PAIRING TOKEN"
   ```
3. Send the pairing token to the bot on Telegram before expecting any response

The bot will not respond to messages until pairing is complete. After 5 failed pairing attempts, the chat locks. This lockout is now persisted (`core/state_store.py`) and survives a restart. Clear it via HTTP:

```bash
curl -X POST http://localhost:8080/admin/unlock \
  -H "Content-Type: application/json" \
  -d '{"code": "<PAIRING_TOKEN>", "chat_id": "<YOUR_CHAT_ID>"}'
```

Or send `/newagent` to start the agent creator wizard; it will guide you through re-pairing.

### Bot not responding to messages

1. Check if the service is running:
   ```bash
   sudo systemctl status modular-agents
   ```
2. Check for errors in logs:
   ```bash
   journalctl -u modular-agents -n 50 | grep ERROR
   ```
3. Common causes:
   - Telegram token expired or revoked → generate a new one from @BotFather
   - Anthropic API key exhausted → check usage at console.anthropic.com
   - Network connectivity issue → `ping api.telegram.org`

### Service fails to start (restart loop)

```bash
# Check what the error is
journalctl -u modular-agents -n 30

# Common causes:
# - .env missing or malformed → check file exists and has no typos
# - Python import error → run manually to see the traceback:
source .venv/bin/activate && python main.py
```

If it fails 5 times in 2 minutes, systemd stops retrying. Reset with:
```bash
sudo systemctl reset-failed modular-agents
sudo systemctl start modular-agents
```

### GitHub tool errors

```bash
# Check gh is authenticated
gh auth status

# Re-authenticate if needed
gh auth login

# Test a basic command
gh repo list --limit 5
```

### Railway tool errors

```bash
# Check railway is authenticated
railway whoami

# Re-authenticate if needed
railway login

# Test status
railway status
```

### Health check failures at startup

The agents run health checks at startup and log warnings for failures.
These are warnings, not fatal errors — the bot will still start.

```bash
# See which agents failed health checks
journalctl -u modular-agents | grep "health_fail"
```

Common reasons:
- `github`: `gh` not installed or not authenticated
- `railway`: `railway` CLI not installed or not authenticated
- `storage`: permissions issue on `memory/sessions.db`

---

## Backup

The stateful files are:

| File | Contents | Security considerations |
|---|---|---|
| `memory/sessions.db` | All conversation history | **Plaintext SQLite** — anyone with filesystem access can read via `sqlite3`. Contains sensitive data (emails, deployments, personal context). Protect via filesystem permissions or encryption. |
| `memory/context/*.md` | Your preferences and projects | Commit to git (no secrets) |
| `memory/solutions/**` | Agent-learned patterns | Commit to git |
| `.env` | Secrets | Keep secure copy — do NOT commit |

Automated daily backups are configured during setup via a systemd timer. Backups are stored in `$PROJECT_DIR/backups/` as timestamped tarballs and pruned after 7 days.

To trigger a manual backup:
```bash
sudo systemctl start modular-agents-backup.service
```

To inspect recent backups:
```bash
ls -lh backups/
```

To check backup timer status:
```bash
systemctl list-timers | grep backup
```

Manual alternative (without systemd):
```bash
cp memory/sessions.db memory/sessions.db.bak.$(date +%Y%m%d)
```

---

## WSL2-specific notes

### Enabling systemd on WSL2

Add to `/etc/wsl.conf`:
```ini
[boot]
systemd=true
```

Then in PowerShell:
```powershell
wsl --shutdown
wsl
```

After restarting, `systemctl` will be available and you can install the service normally.

### Keeping the bot running when WSL2 is closed

With systemd enabled, the service runs as a system daemon and survives WSL2 terminal sessions closing. It will not survive a full `wsl --shutdown` — restart the service after that.

For always-on operation, consider running the bot on a small Linux VPS (e.g. DigitalOcean, Hetzner, Fly.io) rather than WSL2.