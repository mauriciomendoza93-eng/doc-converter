#!/bin/bash
# Compila docvision (Vision RecognizeDocumentsRequest, macOS 26+) a .build/docvision.
# Si el Mac no tiene macOS 26+ o swiftc, sale con 0 sin binario: el motor cae al OCR plano.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$DIR/.build/docvision"
MAJOR="$(sw_vers -productVersion | cut -d. -f1)"
if [ "$MAJOR" -lt 26 ] || ! command -v swiftc >/dev/null 2>&1; then
  echo "docvision: omitido (requiere macOS 26+ y swiftc); se usará el OCR plano." >&2
  rm -f "$OUT"; exit 0
fi
if [ -x "$OUT" ] && [ "$OUT" -nt "$DIR/docvision.swift" ]; then exit 0; fi
mkdir -p "$DIR/.build"
if swiftc -O -parse-as-library "$DIR/docvision.swift" -o "$OUT.tmp" 2>"$DIR/.build/build.log"; then
  mv "$OUT.tmp" "$OUT"; echo "docvision: compilado en $OUT"
else
  rm -f "$OUT.tmp" "$OUT"; echo "docvision: falló la compilación (ver $DIR/.build/build.log); se usará el OCR plano." >&2
fi
exit 0
