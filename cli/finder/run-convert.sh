#!/bin/bash
# Called by the Finder Quick Action with selected files/folders as "$@".
# Output files (.csv / .md) are written next to each original.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$PROJECT_DIR/.venv/bin/python}"
ROUTE="${ROUTE:-auto}"
LOG_FILE="$HOME/Library/Logs/doc-converter.log"

cd "$PROJECT_DIR" || exit 1
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') route=$ROUTE"
  printf '  %s\n' "$@"
} >> "$LOG_FILE"

OUTPUT=$("$PYTHON_BIN" -m backend.cli --route "$ROUTE" "$@" 2>&1)
STATUS=$?
printf '%s\n' "$OUTPUT" >> "$LOG_FILE"

notify() {
  # Best-effort: a notification failure must never mark the conversion as failed.
  /usr/bin/osascript -e "display notification \"$1\" with title \"Doc Converter\"" >/dev/null 2>&1 || true
}

if [ $STATUS -ne 0 ]; then
  # Non-zero exit makes Automator show an alert with this stderr text.
  notify "Hubo errores. Detalle en ~/Library/Logs/doc-converter.log"
  printf '%s\n' "$OUTPUT" >&2
  exit 1
fi

COUNT=$(printf '%s\n' "$OUTPUT" | grep -c ' -> ')
notify "Listo: $COUNT archivo(s) convertido(s)"
exit 0
