#!/usr/bin/env bash
# setup.sh
# ─────────────────────────────────────────────────────────────────────────────
# One-shot setup script for the modular-agents framework.
# Run this once after cloning to get the bot production-ready.
#
# What it does:
#   1. Verifies Python version (3.11+)
#   2. Creates a virtual environment
#   3. Installs dependencies
#   4. Validates .env exists and has required keys
#   5. Locks down file permissions (.env, sessions.db)
#   6. Creates required directories (memory/context, memory/solutions)
#   7. Checks for gh/railway CLIs (needed by the DevOps agent)
#   8. Installs and enables the systemd service (Linux only)
#   9. Installs and enables a daily backup timer (Linux only)
#   10. Runs the integration test suite
#   11. Prints a final status summary
#
# Usage:
#   chmod +x setup.sh
#   ./setup.sh
#
# To skip the systemd step (e.g. on WSL2 without systemd):
#   SKIP_SYSTEMD=1 ./setup.sh
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Colours ───────────────────────────────────────────────────────────────────
GREEN='\033[32m'
RED='\033[31m'
YELLOW='\033[33m'
BOLD='\033[1m'
RESET='\033[0m'

ok()   { echo -e "  ${GREEN}✓${RESET}  $1"; }
fail() { echo -e "  ${RED}✗${RESET}  $1"; }
warn() { echo -e "  ${YELLOW}!${RESET}  $1"; }
info() { echo -e "  ${BOLD}→${RESET}  $1"; }
section() { echo -e "\n${BOLD}$1${RESET}"; }

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
ENV_FILE="$PROJECT_DIR/.env"
SERVICE_NAME="modular-agents"
SKIP_SYSTEMD="${SKIP_SYSTEMD:-0}"

echo -e "\n${BOLD}Modular Agents — Setup${RESET}"
echo "Project: $PROJECT_DIR"
echo "User:    $(whoami)"


# ── 1. Python version ─────────────────────────────────────────────────────────
section "1. Python version"

PYTHON=$(command -v python3 || true)
if [ -z "$PYTHON" ]; then
    fail "python3 not found. Install Python 3.11+ and retry."
    exit 1
fi

PY_VERSION=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$($PYTHON -c "import sys; print(sys.version_info.major)")
PY_MINOR=$($PYTHON -c "import sys; print(sys.version_info.minor)")

if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]); then
    fail "Python 3.11+ required. Found: $PY_VERSION"
    exit 1
fi
ok "Python $PY_VERSION"


# ── 2. Virtual environment ────────────────────────────────────────────────────
section "2. Virtual environment"

if [ -d "$VENV_DIR" ]; then
    ok "Virtual environment already exists at .venv"
else
    info "Creating virtual environment..."
    $PYTHON -m venv "$VENV_DIR"
    ok "Virtual environment created at .venv"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"


# ── 3. Dependencies ───────────────────────────────────────────────────────────
section "3. Dependencies"

info "Installing from requirements.txt..."
"$VENV_PIP" install --quiet --upgrade pip
"$VENV_PIP" install --quiet -r "$PROJECT_DIR/requirements.txt"
ok "Dependencies installed"


# ── 4. Environment file ───────────────────────────────────────────────────────
section "4. Environment file"

LLM_KEYS=("KILO_API_KEY" "ANTHROPIC_API_KEY" "OPENROUTER_API_KEY" "OLLAMA_BASE_URL")
LLM_CONFIGURED=false
MISSING_KEYS=()

if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$PROJECT_DIR/.env.example" ]; then
        warn ".env not found — copying from .env.example"
        cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
        warn "Fill in your keys in .env before starting the bot"
    else
        fail ".env not found. Create it from .env.example and add your tokens."
        exit 1
    fi
fi

for key in "${LLM_KEYS[@]}"; do
    value=$(grep -E "^${key}=" "$ENV_FILE" | cut -d= -f2- | tr -d '"' | tr -d "'" || true)
    # Skip placeholder values
    if [[ "$value" == *"your_"* ]]; then
        continue
    fi
    # For OLLAMA_BASE_URL, 'localhost' is valid (default local setup)
    if [ "$key" = "OLLAMA_BASE_URL" ] && [[ "$value" == *"localhost"* ]]; then
        LLM_CONFIGURED=true
        break
    fi
    # For API keys, check if there's a value
    if [ -n "$value" ]; then
        LLM_CONFIGURED=true
        break
    fi
done

if [ "$LLM_CONFIGURED" = false ]; then
    warn "No LLM provider configured — set at least one of:"
    echo "       - KILO_API_KEY"
    echo "       - ANTHROPIC_API_KEY"
    echo "       - OPENROUTER_API_KEY"
    echo "       - OLLAMA_BASE_URL"
    warn "Add your preferred LLM provider to .env before starting the bot"
fi

# Check Telegram token specifically
value=$(grep -E "^TELEGRAM_BOT_TOKEN=" "$ENV_FILE" | cut -d= -f2- | tr -d '"' | tr -d "'" || true)
if [ -z "$value" ] || [[ "$value" == *"your_"* ]]; then
    MISSING_KEYS+=("TELEGRAM_BOT_TOKEN")
fi

if [ ${#MISSING_KEYS[@]} -gt 0 ]; then
    fail "Missing or placeholder values in .env:"
    for k in "${MISSING_KEYS[@]}"; do
        echo "       - $k"
    done
    warn "Add your tokens to .env and re-run setup.sh"
    exit 1
fi

ok ".env found with required keys"


# ── 5. File permissions ───────────────────────────────────────────────────────
section "5. File permissions"

chmod 600 "$ENV_FILE"
ok ".env locked to 600"

DB_FILE="$PROJECT_DIR/memory/sessions.db"
if [ -f "$DB_FILE" ]; then
    chmod 600 "$DB_FILE"
    ok "sessions.db locked to 600"
fi

# Warn if DB_ENCRYPTION_KEY is not set
if ! grep -q "^DB_ENCRYPTION_KEY=" "$ENV_FILE" 2>/dev/null || grep -q "^#.*DB_ENCRYPTION_KEY" "$ENV_FILE" 2>/dev/null; then
    warn "DB_ENCRYPTION_KEY not set — conversation data is stored unencrypted"
    warn "Set DB_ENCRYPTION_KEY in .env to enable SQLCipher encryption"
fi


# ── 6. Required directories ───────────────────────────────────────────────────
section "6. Directory structure"

DIRS=(
    "memory/context"
    "memory/solutions"
    "agents/business/skills"
    "agents/devops/skills"
    "agents/devops/tools"
)

for dir in "${DIRS[@]}"; do
    mkdir -p "$PROJECT_DIR/$dir"
    ok "$dir"
done

# Seed personal context files from templates if they don't exist.
# Each installation keeps its own personal.md / preferences.md / projects.md /
# reader_profile.md; these files are gitignored once created. See
# memory/context/README.md.
CONTEXT_FILES=("preferences" "personal" "projects" "reader_profile")
for f in "${CONTEXT_FILES[@]}"; do
    TARGET="$PROJECT_DIR/memory/context/${f}.md"
    TEMPLATE="$PROJECT_DIR/memory/context/${f}.md.template"
    if [ ! -f "$TARGET" ]; then
        if [ -f "$TEMPLATE" ]; then
            cp "$TEMPLATE" "$TARGET"
            warn "Created memory/context/${f}.md from template — review and personalise before running"
        else
            echo "# ${f^}" > "$TARGET"
            echo "" >> "$TARGET"
            echo "<!-- Fill in your ${f} here. This file is read by the agents on every call. -->" >> "$TARGET"
            warn "Created empty memory/context/${f}.md — fill this in before running"
        fi
    else
        ok "memory/context/${f}.md exists (left untouched)"
    fi
done


# ── 7. External CLIs (optional — DevOps agent) ────────────────────────────────
section "7. External CLIs (optional)"

if command -v gh &>/dev/null; then
    ok "gh CLI found"
else
    warn "gh CLI not found — the DevOps agent's GitHub tools will fail at runtime"
    warn "Install: https://cli.github.com/  then run: gh auth login"
fi

if command -v railway &>/dev/null; then
    ok "railway CLI found"
else
    warn "railway CLI not found — the DevOps agent's deployment tools will fail at runtime"
    warn "Install: https://docs.railway.app/develop/cli  then run: railway login"
fi


# ── 8. Systemd service ────────────────────────────────────────────────────────
section "8. Systemd service"

if [ "$SKIP_SYSTEMD" = "1" ]; then
    warn "SKIP_SYSTEMD=1 set — skipping service installation"
    warn "To run manually: source .venv/bin/activate && python main.py"
elif ! command -v systemctl &>/dev/null; then
    warn "systemctl not available (WSL2 without systemd, or non-Linux OS)"
    warn "To enable systemd on WSL2: add 'systemd=true' to /etc/wsl.conf and restart WSL"
    warn "To run manually: source .venv/bin/activate && python main.py"
else
    SERVICE_FILE="$PROJECT_DIR/modular-agents.service"
    SYSTEMD_TARGET="/etc/systemd/system/${SERVICE_NAME}.service"

    if [ ! -f "$SERVICE_FILE" ]; then
        warn "modular-agents.service not found in project root — this file should be included in the repo"
    else
        # Substitute real username and path into the service file
        USERNAME="$(whoami)"
        sed \
            -e "s|YOUR_USERNAME|$USERNAME|g" \
            -e "s|/home/YOUR_USERNAME/modular-agents|$PROJECT_DIR|g" \
            "$SERVICE_FILE" > /tmp/${SERVICE_NAME}.service

        sudo cp /tmp/${SERVICE_NAME}.service "$SYSTEMD_TARGET"
        sudo systemctl daemon-reload
        sudo systemctl enable "$SERVICE_NAME"
        ok "Service installed and enabled: $SYSTEMD_TARGET"
        ok "Will auto-start on boot"
        info "For better security: create a dedicated user with:"
        echo "         sudo useradd -r -s /usr/sbin/nologin modular-agents"
        echo "         sudo chown -R modular-agents:modular-agents $PROJECT_DIR"
        echo "       Then update modular-agents.service to use User=modular-agents"
        info "Start now with: sudo systemctl start $SERVICE_NAME"
    fi
fi


# ── 9. Backup timer ────────────────────────────────────────────────────────────
section "9. Backup timer"

BACKUP_DIR="$PROJECT_DIR/backups"
mkdir -p "$BACKUP_DIR"

BACKUP_SERVICE_FILE="$PROJECT_DIR/modular-agents-backup.service"
BACKUP_TIMER_FILE="$PROJECT_DIR/modular-agents-backup.timer"

if [ ! -f "$BACKUP_SERVICE_FILE" ] || [ ! -f "$BACKUP_TIMER_FILE" ]; then
    warn "Backup timer files not found in project root — skipping backup timer installation"
else
    if [ "$SKIP_SYSTEMD" = "1" ] || ! command -v systemctl &>/dev/null; then
        warn "SKIP_SYSTEMD=1 or systemctl unavailable — skipping backup timer"
    else
        USERNAME="$(whoami)"
        sed \
            -e "s|YOUR_USERNAME|$USERNAME|g" \
            -e "s|/home/YOUR_USERNAME/modular-agents|$PROJECT_DIR|g" \
            "$BACKUP_SERVICE_FILE" > /tmp/${SERVICE_NAME}-backup.service

        sudo cp /tmp/${SERVICE_NAME}-backup.service "/etc/systemd/system/${SERVICE_NAME}-backup.service"
        sudo cp "$BACKUP_TIMER_FILE" "/etc/systemd/system/${SERVICE_NAME}-backup.timer"
        sudo systemctl daemon-reload
        sudo systemctl enable --now "${SERVICE_NAME}-backup.timer"
        ok "Backup timer installed and enabled: ${SERVICE_NAME}-backup.timer"
        ok "Backups run daily to: $BACKUP_DIR"
        info "Run now with: sudo systemctl start ${SERVICE_NAME}-backup.service"
    fi
fi


# ── 10. Integration tests ──────────────────────────────────────────────────────
section "10. Integration tests"

TEST_FILE="$PROJECT_DIR/tests/test_integration.py"
if [ ! -f "$TEST_FILE" ]; then
    warn "tests/test_integration.py not found — skipping"
else
    info "Running integration tests..."
    if "$VENV_PYTHON" "$TEST_FILE"; then
        ok "All integration tests passed"
    else
        fail "Some integration tests failed — check output above"
        warn "Fix failing tests before starting the bot in production"
    fi
fi


# ── 11. Summary ───────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}─────────────────────────────────────────────${RESET}"
echo -e "${BOLD}  Setup complete${RESET}"
echo -e "${BOLD}─────────────────────────────────────────────${RESET}"
echo ""
echo "  Next steps:"

if [ ${#MISSING_KEYS[@]} -gt 0 ]; then
    echo "  1. Fill in missing keys in .env"
fi

echo "  • Fill in memory/context/preferences.md"
echo "  • Fill in memory/context/personal.md"
echo "  • Fill in memory/context/projects.md  (add repo: org/name and railway-service: ...)"
echo ""

if command -v systemctl &>/dev/null && [ "$SKIP_SYSTEMD" != "1" ]; then
    echo "  Start:   sudo systemctl start $SERVICE_NAME"
    echo "  Stop:    sudo systemctl stop $SERVICE_NAME"
    echo "  Logs:    journalctl -u $SERVICE_NAME -f"
else
    echo "  Start:   source .venv/bin/activate && python main.py"
    echo "  Logs:    stdout / stderr directly"
fi

echo ""