#!/bin/bash
# Crea .venv-ocr con mlx-vlm y descarga/prueba GLM-OCR (~1,1 GB, una sola vez).
# Sin Apple Silicon sale con 0 sin entorno: el motor sigue con el OCR de Vision.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$DIR/../.." && pwd)"
VENV="$PROJECT_DIR/.venv-ocr"
if [ "$(uname -m)" != "arm64" ]; then
  echo "vlmocr: omitido (requiere Apple Silicon); se usará el OCR de Vision." >&2; exit 0
fi
if ! "$VENV/bin/python" -c "import mlx_vlm" 2>/dev/null; then
  echo "vlmocr: creando $VENV…"
  python3 -m venv "$VENV" && PIP_DISABLE_PIP_VERSION_CHECK=1 "$VENV/bin/python" -m pip install -q -r "$DIR/requirements.txt" \
    || { echo "vlmocr: no se pudo instalar mlx-vlm; se usará el OCR de Vision." >&2; rm -rf "$VENV"; exit 0; }
fi
echo "vlmocr: descargando y probando el modelo (la primera vez tarda unos minutos)…"
if "$VENV/bin/python" "$DIR/vlmocr.py" --warm-up >/dev/null 2>"$DIR/.warm-up.log"; then
  echo "vlmocr: listo"
else
  echo "vlmocr: falló la prueba del modelo (ver $DIR/.warm-up.log); se usará el OCR de Vision." >&2
fi
exit 0
