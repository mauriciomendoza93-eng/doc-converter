#!/bin/bash
# Installs the Finder right-click Services that run run-convert.sh on the
# selected files/folders:
#   "Convertir con Doc Converter"       → engine local (Gemini solo si hace falta OCR)
#   "Convertir con Doc Converter (IA)"  → engine ai (todo PDF/imagen pasa por Gemini)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"

if ! "$PYTHON_BIN" -c "import fitz, pandas, google.genai, dotenv, markitdown, pymupdf4llm, ocrmac" >/dev/null 2>&1; then
  echo "Error: el entorno $PROJECT_DIR/.venv no existe o le faltan dependencias." >&2
  echo "Créalo con: python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt && .venv/bin/python -m pip install -r requirements-local.txt" >&2
  exit 1
fi
if ! grep -q '^GEMINI_API_KEY=.' "$PROJECT_DIR/.env" 2>/dev/null; then
  echo "Aviso: falta GEMINI_API_KEY en $PROJECT_DIR/.env (necesaria para PDF/imágenes)." >&2
fi

chmod +x "$SCRIPT_DIR/run-convert.sh"

install_service() {
local SERVICE_NAME="$1" ENGINE="$2"
local SERVICE_DIR="$HOME/Library/Services/$SERVICE_NAME.workflow/Contents"
mkdir -p "$SERVICE_DIR"

cat > "$SERVICE_DIR/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>NSServices</key>
  <array>
    <dict>
      <key>NSMenuItem</key>
      <dict><key>default</key><string>$SERVICE_NAME</string></dict>
      <key>NSMessage</key><string>runWorkflowAsService</string>
      <key>NSSendFileTypes</key>
      <array><string>public.item</string></array>
    </dict>
  </array>
</dict>
</plist>
PLIST

cat > "$SERVICE_DIR/document.wflow" <<WFLOW
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>AMApplicationBuild</key><string>528</string>
  <key>actions</key>
  <array>
    <dict>
      <key>action</key>
      <dict>
        <key>ActionBundlePath</key><string>/System/Library/Automator/Run Shell Script.action</string>
        <key>ActionParameters</key>
        <dict>
          <key>COMMAND_STRING</key><string>ENGINE="$ENGINE" PYTHON_BIN="$PYTHON_BIN" "$SCRIPT_DIR/run-convert.sh" "\$@"</string>
          <key>inputMethod</key><integer>1</integer>
          <key>shell</key><string>/bin/bash</string>
        </dict>
      </dict>
    </dict>
  </array>
  <key>workflowMetaData</key>
  <dict>
    <key>serviceInputTypeIdentifier</key><string>com.apple.Automator.fileSystemObject</string>
    <key>workflowTypeIdentifier</key><string>com.apple.Automator.servicesMenu</string>
  </dict>
</dict>
</plist>
WFLOW
echo "Instalado: Finder > clic derecho > Servicios (o Acciones rápidas) > $SERVICE_NAME"
}

install_service "Convertir con Doc Converter" local
install_service "Convertir con Doc Converter (IA)" ai

/System/Library/CoreServices/pbs -update >/dev/null 2>&1 || true
echo "(si no aparecen: killall Finder)"
