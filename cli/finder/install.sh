#!/bin/bash
# Instala los Servicios de Finder (clic derecho) que ejecutan run-convert.sh:
#   "Convertir con Doc Converter"       → engine local (sin Gemini salvo extractos PDF)
#   "Convertir con Doc Converter (IA)"  → engine ai (reestructura con Gemini)
#
# Uso:  cli/finder/install.sh           instala, compila docvision y prueba de punta a punta
#       cli/finder/install.sh --check   solo diagnostica los servicios ya instalados
#
# Si mueves la carpeta del proyecto hay que volver a ejecutarlo: los servicios
# guardan la ruta absoluta (el script avisa con una notificación si se rompe).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
SERVICES=("Convertir con Doc Converter" "Convertir con Doc Converter (IA)")

check_services() {
  local bad=0 name wf cmd
  for name in "${SERVICES[@]}"; do
    wf="$HOME/Library/Services/$name.workflow/Contents/document.wflow"
    if [ ! -f "$wf" ]; then echo "✗ $name: no instalado"; bad=1; continue; fi
    cmd="$(/usr/bin/plutil -extract actions.0.action.ActionParameters.COMMAND_STRING raw -o - "$wf" 2>/dev/null || true)"
    if printf '%s' "$cmd" | grep -qF "$SCRIPT_DIR/run-convert.sh"; then
      echo "✓ $name → $SCRIPT_DIR/run-convert.sh"
    else
      echo "✗ $name: apunta a otra ruta (¿proyecto movido?). Ejecuta cli/finder/install.sh"; bad=1
    fi
  done
  [ -x "$PYTHON_BIN" ] && echo "✓ entorno Python: $PYTHON_BIN" || { echo "✗ falta $PYTHON_BIN"; bad=1; }
  if [ -x "$PROJECT_DIR/cli/docvision/.build/docvision" ]; then echo "✓ docvision (tablas/listas con Vision)"
  else echo "• docvision no compilado: se usará el OCR plano (requiere macOS 26+)"; fi
  return $bad
}

if [ "${1:-}" = "--check" ]; then check_services; exit $?; fi

if ! "$PYTHON_BIN" -c "import fitz, pandas, google.genai, dotenv, markitdown, pymupdf4llm, ocrmac, PIL" >/dev/null 2>&1; then
  echo "Error: el entorno $PROJECT_DIR/.venv no existe o le faltan dependencias." >&2
  echo "Créalo con: python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt && .venv/bin/python -m pip install -r requirements-local.txt" >&2
  exit 1
fi
if ! grep -q '^GEMINI_API_KEY=.' "$PROJECT_DIR/.env" 2>/dev/null; then
  echo "Aviso: falta GEMINI_API_KEY en $PROJECT_DIR/.env (solo necesaria para extractos PDF y el modo IA)." >&2
fi

chmod +x "$SCRIPT_DIR/run-convert.sh"

# OCR estructurado (tablas, listas): compilar y precalentar una sola vez.
"$PROJECT_DIR/cli/docvision/build.sh"
if [ -x "$PROJECT_DIR/cli/docvision/.build/docvision" ]; then
  echo "Precalentando Vision (la primera vez tarda ~30 s)…"
  (cd "$PROJECT_DIR" && "$PYTHON_BIN" -c "from backend.engines import structured_ocr as s; print('listo en %.0fs' % s.warm_up())") || echo "Aviso: no se pudo precalentar docvision." >&2
fi

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

# El comando no falla en silencio si la carpeta del proyecto ya no existe.
local CMD="RUN=\"$SCRIPT_DIR/run-convert.sh\"; if [ ! -x \"\$RUN\" ]; then /usr/bin/osascript -e 'display notification \"Doc Converter no está en $PROJECT_DIR. Ejecuta cli/finder/install.sh desde la nueva ubicación.\" with title \"Doc Converter\"'; echo \"No se encontró \$RUN. Ejecuta install.sh de nuevo.\" >&2; exit 1; fi; ENGINE=\"$ENGINE\" PYTHON_BIN=\"$PYTHON_BIN\" \"\$RUN\" \"\$@\""
local CMD_XML="${CMD//&/&amp;}"; CMD_XML="${CMD_XML//</&lt;}"; CMD_XML="${CMD_XML//>/&gt;}"

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
          <key>COMMAND_STRING</key><string>$CMD_XML</string>
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
/usr/bin/plutil -lint "$SERVICE_DIR/Info.plist" "$SERVICE_DIR/document.wflow" >/dev/null
echo "Instalado: Finder > clic derecho > Servicios (o Acciones rápidas) > $SERVICE_NAME"
}

install_service "${SERVICES[0]}" local
install_service "${SERVICES[1]}" ai

/System/Library/CoreServices/pbs -update >/dev/null 2>&1 || true

# Prueba de punta a punta con el mismo script que ejecuta el clic derecho.
TEST_DIR="$(mktemp -d)"
trap 'rm -rf "$TEST_DIR"' EXIT
printf '<h1>Prueba</h1><p>Doc Converter funciona.</p>' > "$TEST_DIR/prueba.html"
if DOC_CONVERTER_QUIET=1 ENGINE=local PYTHON_BIN="$PYTHON_BIN" "$SCRIPT_DIR/run-convert.sh" "$TEST_DIR/prueba.html" >/dev/null 2>&1 \
   && grep -q "Doc Converter funciona" "$TEST_DIR/prueba.md" 2>/dev/null; then
  echo "✓ Prueba de extremo a extremo correcta"
else
  echo "✗ La prueba de extremo a extremo falló; revisa ~/Library/Logs/doc-converter.log" >&2
  exit 1
fi
check_services
echo "(si no aparecen en el menú: killall Finder)"
