#!/usr/bin/env bash
# scripts/backup.sh
# ─────────────────────────────────────────────────────────────────────────────
# Automated backup for Modular Agents stateful files.
#
# Backs up:
#   - memory/sessions.db
#   - memory/context/
#   - memory/knowledge/ (if present)
#   - agents/*/state.json  (if present)
#
# Archives are timestamped tarballs stored in $BACKUP_DIR
# (default: /var/backups/modular-agents).
# Old backups beyond $RETENTION_DAYS are pruned.
#
# Usage:
#   sudo scripts/backup.sh [BACKUP_DIR]
#
# Environment:
#   BACKUP_DIR      override backup destination (default: /var/backups/modular-agents)
#   RETENTION_DAYS  keep backups newer than this (default: 7)
#   PROJECT_DIR     override project root (default: script's parent dir)
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
BACKUP_DIR="${BACKUP_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
ARCHIVE="$BACKUP_DIR/modular-agents_${TIMESTAMP}.tar.gz"

mkdir -p "$BACKUP_DIR"

TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT

mkdir -p "$TEMP_DIR/memory"

# ── Database ──────────────────────────────────────────────────────────────────
if [ -f "$PROJECT_DIR/memory/sessions.db" ]; then
    cp "$PROJECT_DIR/memory/sessions.db" "$TEMP_DIR/memory/sessions.db"
fi

# ── Context & knowledge ───────────────────────────────────────────────────────
if [ -d "$PROJECT_DIR/memory/context" ]; then
    cp -a "$PROJECT_DIR/memory/context" "$TEMP_DIR/memory/context"
fi

if [ -d "$PROJECT_DIR/memory/knowledge" ]; then
    cp -a "$PROJECT_DIR/memory/knowledge" "$TEMP_DIR/memory/knowledge"
fi

if [ -d "$PROJECT_DIR/memory/solutions" ]; then
    cp -a "$PROJECT_DIR/memory/solutions" "$TEMP_DIR/memory/solutions"
fi

# ── Agent state files ─────────────────────────────────────────────────────────
mkdir -p "$TEMP_DIR/agents"
shopt -s nullglob
for state_file in "$PROJECT_DIR"/agents/*/state.json; do
    agent_dir="$TEMP_DIR/agents/$(basename "$(dirname "$state_file")")"
    mkdir -p "$agent_dir"
    cp "$state_file" "$agent_dir/"
done
shopt -u nullglob

# ── Archive ───────────────────────────────────────────────────────────────────
tar -C "$TEMP_DIR" -czf "$ARCHIVE" .

# ── Retention ─────────────────────────────────────────────────────────────────
find "$BACKUP_DIR" -maxdepth 1 -name 'modular-agents_*.tar.gz' -type f -mtime +$RETENTION_DAYS -delete

echo "Backup created: $ARCHIVE"
