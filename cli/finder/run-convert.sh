#!/bin/bash
# Called by the Finder Quick Action with selected files/folders as "$@".
# Output files (.csv / .md) are written next to each original (folders: to the
# sibling "<folder>_convertida"). See backend/cli.py.
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
  [ -n "${DOC_CONVERTER_QUIET:-}" ] && return 0
  /usr/bin/osascript -e "display notification \"$1\" with title \"Doc Converter\"" >/dev/null 2>&1 || true
}

fail() {
  notify "$1"
  printf '%s\n' "$1" >&2
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >> "$LOG_FILE"
  exit 1
}

[ -x "$PYTHON_BIN" ] || fail "No se encuentra el entorno Python ($PYTHON_BIN). Ejecuta cli/finder/install.sh de nuevo."
[ -f "$PROJECT_DIR/backend/cli.py" ] || fail "Proyecto no encontrado en $PROJECT_DIR. Ejecuta cli/finder/install.sh de nuevo."

# One lock per selection: a second click on the same files while the first
# conversion is still running is ignored instead of producing duplicates.
# A lock whose process is gone (crash, logout) is stale and gets reclaimed.
mkdir -p "$LOCK_DIR"
LOCK="$LOCK_DIR/$(printf '%s\n' "$@" | /sbin/md5 -q)"
if ! mkdir "$LOCK" 2>/dev/null; then
  OLD_PID="$(cat "$LOCK/pid" 2>/dev/null || true)"
  if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    notify "Ya se está convirtiendo esa selección, espera a que termine"
    exit 0
  fi
  rm -rf "$LOCK"
  mkdir "$LOCK" 2>/dev/null || exit 0
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK" 2>/dev/null' EXIT

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
# Progress goes to the log as it happens (tail -f ~/Library/Logs/doc-converter.log);
# the full output is also kept in memory for the Automator error alert.
OUTPUT_FILE="$(mktemp "${TMPDIR:-/tmp}/doc-converter-out.XXXXXX")"
"$PYTHON_BIN" -m backend.cli --route "$ROUTE" --engine "$ENGINE" "$@" 2>&1 | tee -a "$LOG_FILE" > "$OUTPUT_FILE"
STATUS=${PIPESTATUS[0]}
OUTPUT="$(cat "$OUTPUT_FILE")"
rm -f "$OUTPUT_FILE"
printf '(%ss)\n' "$((SECONDS - START))" >> "$LOG_FILE"

COUNT=$(printf '%s\n' "$OUTPUT" | grep -c ' -> ')
ERRORS=$(printf '%s\n' "$OUTPUT" | grep -c '^Error')

if [ $STATUS -ne 0 ]; then
  # Non-zero exit makes Automator show an alert with this stderr text.
  notify "$COUNT convertido(s), $ERRORS con error. Detalle en ~/Library/Logs/doc-converter.log"
  printf '%s\n' "$OUTPUT" | grep -E '^(Error|Resumen|No se)' >&2
  exit 1
fi

notify "Listo: $COUNT archivo(s) convertido(s) en $((SECONDS - START))s"
exit 0
