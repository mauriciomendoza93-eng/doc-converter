#!/bin/bash
# Called by the Finder Quick Action with selected files/folders as "$@".
# Output files (.csv / .md) are written next to each original.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$PROJECT_DIR/.venv/bin/python}"
ROUTE="${ROUTE:-auto}"
ENGINE="${ENGINE:-local}"
LOG_FILE="$HOME/Library/Logs/doc-converter.log"
LOCK_DIR="${TMPDIR:-/tmp}/doc-converter-locks"

notify() {
  # Best-effort: a notification failure must never mark the conversion as failed.
  /usr/bin/osascript -e "display notification \"$1\" with title \"Doc Converter\"" >/dev/null 2>&1 || true
}

# One lock per selection: a second click on the same files while the first
# conversion is still running is ignored instead of producing duplicates.
mkdir -p "$LOCK_DIR"
LOCK="$LOCK_DIR/$(printf '%s\n' "$@" | /sbin/md5 -q)"
if ! mkdir "$LOCK" 2>/dev/null; then
  notify "Ya se está convirtiendo esa selección, espera a que termine"
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

cd "$PROJECT_DIR" || exit 1
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') route=$ROUTE engine=$ENGINE"
  printf '  %s\n' "$@"
} >> "$LOG_FILE"

if [ $# -eq 1 ]; then
  notify "Convirtiendo $(basename "$1")…"
else
  notify "Convirtiendo $# elementos…"
fi

START=$SECONDS
OUTPUT=$("$PYTHON_BIN" -m backend.cli --route "$ROUTE" --engine "$ENGINE" "$@" 2>&1)
STATUS=$?
printf '%s\n(%ss)\n' "$OUTPUT" "$((SECONDS - START))" >> "$LOG_FILE"

if [ $STATUS -ne 0 ]; then
  # Non-zero exit makes Automator show an alert with this stderr text.
  notify "Hubo errores. Detalle en ~/Library/Logs/doc-converter.log"
  printf '%s\n' "$OUTPUT" >&2
  exit 1
fi

COUNT=$(printf '%s\n' "$OUTPUT" | grep -c ' -> ')
notify "Listo: $COUNT archivo(s) convertido(s) en $((SECONDS - START))s"
exit 0
