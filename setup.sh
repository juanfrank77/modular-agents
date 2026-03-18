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
#   7. Renames skill-loader.py if the old hyphenated name exists
#   8. Installs and enables the systemd service (Linux only)
#   9. Runs the integration test suite
#   10. Prints a final status summary
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

REQUIRED_KEYS=("TELEGRAM_BOT_TOKEN" "ANTHROPIC_API_KEY")
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

for key in "${REQUIRED_KEYS[@]}"; do
    value=$(grep -E "^${key}=" "$ENV_FILE" | cut -d= -f2- | tr -d '"' | tr -d "'" || true)
    if [ -z "$value" ] || [[ "$value" == *"your_"* ]]; then
        MISSING_KEYS+=("$key")
    fi
done

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

# Seed empty context files if they don't exist
CONTEXT_FILES=("preferences" "personal" "projects")
for f in "${CONTEXT_FILES[@]}"; do
    TARGET="$PROJECT_DIR/memory/context/${f}.md"
    if [ ! -f "$TARGET" ]; then
        echo "# ${f^}" > "$TARGET"
        echo "" >> "$TARGET"
        echo "<!-- Fill in your ${f} here. This file is read by the agents on every call. -->" >> "$TARGET"
        warn "Created empty memory/context/${f}.md — fill this in before running"
    else
        ok "memory/context/${f}.md exists"
    fi
done


# ── 7. Rename skill-loader.py if needed ──────────────────────────────────────
section "7. File naming"

OLD="$PROJECT_DIR/core/skill-loader.py"
NEW="$PROJECT_DIR/core/skill_loader.py"

if [ -f "$OLD" ] && [ ! -f "$NEW" ]; then
    mv "$OLD" "$NEW"
    ok "Renamed core/skill-loader.py → core/skill_loader.py"
elif [ -f "$NEW" ]; then
    ok "core/skill_loader.py already correctly named"
elif [ ! -f "$OLD" ] && [ ! -f "$NEW" ]; then
    warn "core/skill_loader.py not found — ensure it exists before running the bot"
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
        warn "modular-agents.service not found in project root — skipping"
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
        info "Start now with: sudo systemctl start $SERVICE_NAME"
    fi
fi


# ── 9. Integration tests ──────────────────────────────────────────────────────
section "9. Integration tests"

TEST_FILE="$PROJECT_DIR/test_integration.py"
if [ ! -f "$TEST_FILE" ]; then
    warn "test_integration.py not found — skipping"
else
    info "Running integration tests..."
    if "$VENV_PYTHON" "$TEST_FILE"; then
        ok "All integration tests passed"
    else
        fail "Some integration tests failed — check output above"
        warn "Fix failing tests before starting the bot in production"
    fi
fi


# ── 10. Summary ───────────────────────────────────────────────────────────────
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