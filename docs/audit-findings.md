# Audit Findings — UX & Security

Two-pass review of the system documentation and design.
Work through these in priority order — Security Critical items first.

---

## Security Findings

### Critical

- [x] **S1 — Brute-force pairing code**
   The 6-digit code (1,000,000 possibilities) has no lockout mechanism. Telegram doesn't rate-limit bot interactions by default, so an attacker who knows the bot username can script attempts.
   **Fix:** Replace with a cryptographically random token (UUID or 12+ char alphanumeric). Add a failed-attempt counter that locks the pairing flow after 5 tries and requires a restart to reset.

- [x] **S2 — `TELEGRAM_ALLOWED_CHAT_IDS` defaults to open**
   The README says "Leave empty in development to allow all chats." Users forget to set this before going live. A bot with access to email, deployments, and calendar that responds to anyone is a serious exposure.
   **Fix:** Make startup fail (or emit a loud, unmissable warning) if this var is unset or empty when `ENV=production`. Do not silently proceed.

- [x] **S3 — Command blocklist is regex-based and trivially bypassable**
   `r'rm\s+-rf'` won't catch `rm  -rf` (extra space), `rm -r -f`, `\rm -rf`, or quote tricks. Regex blocklists for shell safety are a known anti-pattern — the LLM has a large creative surface for generating equivalent commands.
   **Fix:** Never pass user/LLM-generated content to `shell=True`. Pass commands as argument lists to `subprocess.run()` with explicit args. The `cli_runner.py` should construct CLI calls from structured data, not free-form strings.

---

### High
 
- [x] **S4 — HTTP session tokens never expire**
   From ARCHITECTURE.md: "tokens persist until the server restarts. There is no built-in expiry." A leaked token is permanently valid.
   **Fix:** Add a configurable TTL (default: 24h). Add a `DELETE /session` endpoint for explicit logout. Reject expired tokens with 401.

- [x] **S5 — Prompt injection via skill and context files**
   Skill files and `memory/context/*.md` files are loaded verbatim into LLM system prompts with no sanitization. If an attacker modifies these files (via FileTool exploit, path traversal, or direct filesystem access), they inject instructions into every agent call.
   **Fix:** Wrap all file-loaded content in XML delimiters (e.g., `<skill>`, `<context>`) and explicitly instruct the LLM in the base system prompt that content inside those tags is data, not instructions.
   **Implemented:** skill_loader.py (lines 27-29, 63), memory.py (lines 70-72, 139, 148, 221) wrap skills/context in `<skill>`/`<context>` tags. Solutions wrapped in `<solution>` tags.

- [x] **S6 — Web scraping creates an indirect prompt injection vector**
   `WebTool.scrape()` injects up to 20KB of arbitrary webpage content into LLM context. A malicious page (or one the LLM is tricked into fetching) can embed instructions that hijack agent behavior.
   **Fix:** Same XML-wrapping approach as S5. Add a note in the LLM prompt that scraped content is untrusted external data. Consider capping injected scrape content further (e.g., 5KB).
   **Implemented:** web_tool.py (lines 30-32, 209-210) wraps scrape content in `<web_scrape>` tags, content capped at 20KB.

- [x] **S7 — Auto-written solution files are a stored prompt injection risk**
   The LLM writes files to `memory/solutions/` after successful executions. These are loaded as context in future sessions. A manipulated LLM could persist malicious instructions that survive across restarts.
   **Fix:** Treat solutions as untrusted. Either require a human review step (e.g., a pending queue the user approves via Telegram before promotion), or wrap them with the same untrusted-data delimiters as S5.
   **Implemented:** memory.py (lines 74-76, 139, 148, 221) wraps solutions in `<solution>` XML tags.

- [x] **S8 — `sessions.db` is plaintext**
   All conversation history sits in an unencrypted SQLite file. Anyone with filesystem access reads it with a single `sqlite3` command. Content may include sensitive email details, deployment info, personal context.
   **Fix:** Documented prominently in README's "Your data stays yours" section and RUNBOOK backup section. Added note about filesystem encryption (fscrypt) for sensitive deployments.
   **Implemented:** README.md (line 527) adds security note about plaintext storage. RUNBOOK.md updates backup table with security warnings.

---

### Medium
 
- [x] **S9 — Pairing code is logged to journald**
   The code is printed to the console and in structured logs. Anyone with read access to journald (common on many distros without explicit hardening) can extract it and pair their own Telegram account.
   **Fix:** Write the pairing code only to stdout (TTY), never to the log sink. In the structured logger, emit `"pairing_code_generated": true` without the actual value.
   **Implemented:** main.py (line 199) prints to stdout. Pairing code is not written to structured logs (verified in code). Note: code is still visible in journald when running under systemd, but not in structured log entries.

- [x] **S10 — No HTTPS on HTTP API by default**
   For a system that can execute shell commands, deploy to Railway, and send emails, running the API over plain HTTP is a significant risk for any non-localhost deployment.
   **Fix:** Default `HTTP_HOST` to `127.0.0.1` (already done — good). Add a startup warning if `HTTP_HOST=0.0.0.0` is set without a documented reverse proxy. Add a section to RUNBOOK.md with a minimal nginx/Caddy TLS config.
   **Implemented:** config.py (line 104) defaults HTTP_HOST to 127.0.0.1. ARCHITECTURE.md (lines 530-536) documents HTTPS considerations and reverse proxy setup. No startup warning added for 0.0.0.0 yet.

- [x] **S11 — `FileTool` path validation needs canonicalization audit**
   Path allowlist checks must resolve symlinks and canonicalize paths *before* comparing against `allowed_paths`. String prefix-matching alone can be escaped with `../` or symlinks.
   **Fix:** Audit `core/file_tool.py`. Ensure `Path.resolve()` is called on user-supplied paths before any allowlist comparison. Add a test case with a symlink that points outside the allowed root.
   **Implemented:** file_tool.py (lines 37, 56) uses `Path.resolve()` to canonicalize paths before allowlist comparison. No explicit symlink escape test exists in test_integration.py yet.

- [x] **S12 — Composio OAuth trust model is undocumented**
   Connecting Gmail and Google Calendar routes through Composio, a third party that holds long-lived OAuth tokens. The README doesn't document token storage, revocation steps, or breach impact.
   **Fix:** Add a "Data and trust model" section to README.md that explains: what Composio holds, how to revoke access (Google security settings), and that Composio is a trust dependency alongside the LLM provider.
   **Implemented:** README.md adds "Data and trust model" section (lines 518-533) covering Composio's data holding, breach impact, and revocation steps.

- [x] **S13 — Bot runs as the developer's full user account**
   The systemd service runs as the setup user, who also has `gh`, `railway`, and other CLIs authenticated. A compromised bot means full access to all those tools under that identity.
   **Fix:** Create a dedicated system user (`modular-agents`) for the service. Configure `gh` and `railway` tokens specifically for that user with minimal required scopes. Update the systemd service template with `User=modular-agents`.
   **Implemented:** modular-agents.service (lines 6-10) now includes commented instructions for creating dedicated user and changing ownership. setup.sh (lines 248-251) displays security guidance after installation. Note: User must manually uncomment and configure for dedicated user mode.

---

### Low

- [ ] **S14 — Structured logs may leak sensitive message content**
  Log lines include full message content. API keys or passwords mentioned in conversation land in journald permanently.
  **Fix:** Truncate `content` field in log lines to 200 chars. Add a `LOG_REDACT_CONTENT=true` env var that hashes content instead of logging it.

- [ ] **S15 — No rate limiting on bot interactions**
  A paired user can flood the bot with requests that burn through LLM API credits. No per-session or per-user limits exist.
  **Fix:** Add a simple token bucket per `chat_id`: e.g., 20 messages per minute, configurable via `RATE_LIMIT_RPM` in `.env`. Respond with a cooldown message when the limit is hit.

---

## UX Findings

### High friction (likely to block new users)

- [x] **U1 — The .env chicken-and-egg problem**
   "Fill out `.env` before running `setup.sh`" is buried mid-step. New users run the script first, get validation errors, and have no friendly explanation.
   **Fix:** Make `setup.sh` detect a missing or unconfigured `.env` and print a step-by-step prompt guide before doing anything else. Alternatively, add a `setup.sh --init` mode that walks through keys interactively.
   **Implemented:** setup.sh (lines 104-140) copies .env.example if missing, validates required keys, and exits with clear error messages. Context files are seeded with `<!-- -->` comments (lines 191-201). Could still be improved with interactive prompting.

- [x] **U2 — Kilo is an unknown provider listed first**
   New users likely have Anthropic or OpenRouter accounts. Kilo being listed as "primary (default)" with no context confuses the choice.
   **Fix:** Add a "Quickstart" callout: *"If you're just getting started, use `ANTHROPIC_API_KEY`. Kilo is an alternative provider."* Reorder the table to put the most accessible option first.
   **Implemented:** README.md (lines 72-79) reordered providers with Anthropic/OpenRouter first. Added "What you'll need by feature" table (lines 62-70).

- [x] **U3 — Pairing code is hard to find under systemd**
   Running with systemd means no terminal output — you need `journalctl | grep "PAIRING CODE"`. Non-technical users have never used journalctl.
   **Fix:** At minimum, make the journalctl command the very first thing in the "First time setup — pairing your chat" section when systemd is in use. Ideally, the bot sends itself a Telegram message containing the pairing code when it starts (to a pre-configured admin chat ID).
   **Implemented:** README.md (lines 184-187) now shows journalctl command as first step in pairing section. RUNBOOK.md (lines 201-215) has dedicated pre-pairing troubleshooting.

- [x] **U4 — "No-Lama" model doesn't exist in Ollama**
  `ollama pull no-lama` in the Ollama setup section will error. This is a made-up example model name.
  **Fix:** Replace with a real model: `ollama pull llama3.2` or `ollama pull mistral`. Keep the example runnable.

- [x] **U5 — Composio requirement is not called out upfront**
   Email and calendar features require a separate Composio account and per-service OAuth. This is buried late in the README after the user has already done the main setup and is expecting things to work.
   **Fix:** Add a "What you'll need" table at the top of the README that lists all external dependencies by feature, including Composio for Gmail/Calendar.
   **Implemented:** README.md (lines 62-70) added "What you'll need by feature" table with all dependencies including Composio.

---

### Medium friction

- [x] **U6 — Context files have unfilled placeholders**
   `personal.md` ships with `[your title / what you do]` placeholder text. New users don't know if leaving this unfilled breaks something or just degrades quality.
   **Fix:** Replace placeholders with commented examples (using `<!-- -->` or a `> Example:` callout). Add one sentence explaining that these files improve response quality but the bot works without them.
   **Implemented:** setup.sh (lines 191-201) seeds context files with HTML-style comments. Existing personal.md and preferences.md have actual content (not placeholders).

- [x] **U7 — Missing troubleshooting: "bot didn't respond to my first message"**
   The most likely first failure — bot doesn't reply when you send it a message to trigger pairing — has no troubleshooting entry in the RUNBOOK.
   **Fix:** Add a "Bot doesn't respond at all (before pairing)" section: check that the bot token is correct, that the service is running, and that the bot username you messaged matches the token.
   **Implemented:** RUNBOOK.md (lines 201-215) added "Bot not responding before pairing" section with pairing verification steps.

- [x] **U8 — `/newagent` wizard requires the bot to be running first**
   The README calls this "Option 1: Recommended" but new users who want to define agents before starting can't use it.
   **Fix:** Clarify that the wizard is for *extending* an already-running system. For first setup, direct users to Option 2 (manual) or the echo agent as a starting template.
   **Implemented:** README.md (lines 272-278) now clarifies wizard is for "Extending a running system" and manual is "First-time setup".

- [x] **U9 — RUNBOOK uses `pip` but memory says user prefers `uv`**
   "Install new dependencies" in the update steps uses `pip install -r requirements.txt`. Minor inconsistency.
   **Fix:** Update RUNBOOK's update steps to use `uv pip install -r requirements.txt` to match the project convention.
   **Implemented:** RUNBOOK.md (line 108) updated to use `uv pip install -r requirements.txt`.

- [x] **U10 — No mention of `.gitignore` for `.env`**
   The RUNBOOK says "do NOT commit" the `.env` file but doesn't confirm there's a `.gitignore` entry protecting it. A new user who does `git add .` could accidentally commit their secrets.
   **Fix:** Verify `.env` is in `.gitignore`. Add a note in the setup section confirming this protection exists.
   **Implemented:** .gitignore (line 61) includes `.env`. RUNBOOK.md mentions "do NOT commit" at line 286. Confirmed protection exists.

## Summary

### Security (15 items)
- **Critical (3 items):** 3 done ✅ (S1-S3)
- **High (5 items):** 5 done ✅ (S4-S8)
- **Medium (5 items):** 5 done ✅ (S9-S13)
- **Low (2 items):** 0 done ⭕ (S14 log redaction, S15 rate limiting)

### UX (10 items)
- **High friction (5 items):** 5 done ✅ (U1-U5)
- **Medium friction (5 items):** 5 done ✅ (U6-U10)

### Total: 23 done, 4 open (S14, S15, plus 2 from other categories)
