# Acción Rápida de Finder

`install.sh` registra dos Servicios de clic derecho:

| Servicio | Motor | Cuándo usarlo |
|---|---|---|
| **Convertir con Doc Converter** | Local (`--engine local`) | Siempre. Segundos, sin cuota. |
| **Convertir con Doc Converter (IA)** | Local + Gemini (`--engine ai`) | Cuando el local no respetó bien la estructura: formularios densos, tablas complejas. |

## Motores disponibles (`--engine`)

| Motor | Qué hace | Medido con documentos reales |
|---|---|---|
| `local` (default) | OCR y extracción en la Mac, sin modelo | 6 archivos en 7 s. No estructura formularios |
| `ai` | Local + cadena de modelos gratuitos (OmniRoute → Gemini) reestructura el texto, validado | Formulario 2 págs 14 s · 10 págs con 360 importes 50 s; sin Gemini |
| `vision` | Manda las imágenes a Gemini (como la web) | El más lento y el que más cuota gasta; el único que corrige errores del OCR, porque mira el documento |

Si la IA devuelve menos texto del que había (señal de que resumió en vez de
reestructurar), se descarta su salida y se conserva el texto crudo. Perder
formato es aceptable; perder contenido no.

El modo IA **no manda el documento**: el OCR se hace igual en tu Mac y al modelo le llega
solo el texto extraído, en fragmentos de ~6 000 caracteres, para que reconstruya títulos y
tablas. Los fragmentos viajan por una **cadena de modelos gratuitos con respaldo**
(`backend/engines/llm_router.py`): OmniRoute (Antigravity/agy, NVIDIA, Cloudflare...) primero
y Gemini directo al final, porque su cuota diaria es la que más se agota.

Cada respuesta se **valida** contra el texto de entrada antes de aceptarla: no puede ser más
corta (resumen), perder texto (recall ≥ 92 %) ni perder, cambiar o inventar cifras. Si un modelo
falla la validación se prueba el siguiente; si todos fallan, ese fragmento queda sin
estructurar y el resultado lo declara (`! IA: fragmento 3 de 9 se dejó sin estructurar`).
Los modelos caídos (credenciales vencidas, sin cuota) o que fallan la validación seguido pasan
al final de la cola un rato (se recuerda entre archivos del lote). El método queda en el
informe: `local + ia (omniroute:agy/claude-sonnet-4-6)`. Sin ningún modelo disponible se
conserva el texto local y se declara: `IA no disponible, se conservó el texto local`.

Configuración en `.env` (ver `.env.example`): `OMNIROUTE_API_KEY` (+ `OMNIROUTE_BASE_URL`) y/o
`GEMINI_API_KEY`; `DOC_CONVERTER_LLM_TEXT_CHAIN` reemplaza el orden. Diagnóstico:

    .venv/bin/python -m backend.engines.llm_router status   # estado y pausas de cada modelo
    .venv/bin/python -m backend.engines.llm_router probe    # llama a cada uno de verdad
    DOC_CONVERTER_LLM_DEBUG=1 .venv/bin/python -m backend.cli --engine ai doc.pdf   # traza

Mediciones y elección de modelos: `docs/llm-benchmark.md`.

**Extractos bancarios (PDF o imagen)**: ya no se le pide a un modelo que *lea píxeles*. El
texto lo extrae la Mac, el modelo lo convierte en movimientos JSON y cada importe devuelto
tiene que existir como cifra en el documento; los que no, se descartan y se informan (si son
más del 20 % se descarta la extracción y se usa la visión de Gemini como respaldo). El banco
se detecta por palabras clave, no por lo que diga el modelo. Los signos ingreso/egreso los
infiere el modelo: el informe pide revisar totales.

Por qué el local solo no alcanza en algunos documentos: el OCR lee el texto y
dónde está, pero no interpreta el diseño. En un formulario a varias columnas
entrega los fragmentos y el orden se arma con reglas de geometría, que fallan
cuando el diseño es denso. El modelo sí reconstruye esa estructura.

## Qué hace cada formato (motor local)

| Entrada | Herramienta | Salida |
|---|---|---|
| `.docx`, `.pptx`, `.html` | markitdown | Markdown |
| `.doc`, `.rtf`, `.odt` | textutil (macOS) → markitdown | Markdown |
| `.pdf` con capa de texto | pymupdf4llm | Markdown |
| `.pdf` escaneado o mixto | pymupdf4llm + OCR de Vision (macOS) por página | Markdown |
| `.png`, `.jpg`, `.tiff`, `.heic`, `.webp`, `.bmp`, `.gif` | OCR de Vision | Markdown |
| `.xls`, `.xlsx`, `.csv` con movimientos (fecha + monto) | pandas | CSV financiero |
| `.xls`, `.xlsx` que no son un extracto | pandas | un CSV fiel por hoja, sin interpretar columnas |
| PDF/imagen que parece extracto bancario | texto local + modelo, cifras verificadas (respaldo: Gemini visión) | CSV financiero |

El OCR de Vision es nativo de macOS: ~0,2 s por página, en paralelo y sin cuota.
En macOS 26+ se usa `RecognizeDocumentsRequest` (helper `cli/docvision`), que además
reconoce **tablas con sus celdas, listas y títulos** y los entrega como Markdown;
cada página se contrasta contra el OCR plano y, si la versión estructurada pierde
texto (recall < 90 %), se conserva el plano. Sin macOS 26 o sin `swiftc`, todo
funciona igual con el OCR plano.
Gemini solo se usa para extractos bancarios en PDF, porque extraer transacciones
necesita un modelo; si falla (sin cuota o sin red), cae a Markdown local. El motor
local **no** manda conceptos de planillas a Gemini para categorizarlos.

Cifras de un extracto: acepta `1,000.00`, `1.000,00`, `(250.50)`, `250.50-`; con
columnas separadas de débito y crédito calcula el signo; una fila con fecha pero
sin monto legible se informa como advertencia en vez de perderse en silencio.

El resultado se guarda junto al archivo original y nunca sobrescribe: si el
nombre existe, agrega `_1`, `_2`. Si dos archivos comparten nombre (`memo.docx` y
`memo.rtf`), cada resultado conserva su extensión (`memo.docx.md`, `memo.rtf.md`).

## Carpetas y lotes

Si seleccionas una carpeta se convierten **todas sus subcarpetas** (usa
`--no-recursive` para solo el primer nivel) y los resultados van a la carpeta
hermana `<carpeta>_convertida` con la misma estructura. Se ignoran archivos ocultos,
bloqueos de Office (`~$…`), `.DS_Store`, paquetes (`.app`, `.photoslibrary`…), las
carpetas `*_convertida` y los `.md/.txt`; el resumen dice cuántos y de qué tipo.

- **Paralelo y aislado**: hasta 3 archivos a la vez, cada uno en su propio proceso
  con tiempo límite (`--timeout`, 900 s). Un PDF corrupto, cifrado o colgado se
  informa con un mensaje claro y el resto sigue.
- **`_informe.json`** en la carpeta de salida: por archivo, método, páginas, páginas
  con OCR, advertencias (p. ej. "2 página(s) sin texto reconocible: 7, 12"), errores
  y huella SHA-256.
- **Idempotente**: volver a ejecutar sobre la misma carpeta solo reconvierte lo que
  cambió (`--force` para todo) y reemplaza sus propias salidas en vez de numerarlas.
- `--dry-run` lista qué se convertiría sin tocar nada.

## Instalación

    python3 -m venv .venv
    .venv/bin/python -m pip install -r requirements.txt
    .venv/bin/python -m pip install -r requirements-local.txt
    cli/finder/install.sh

`install.sh` compila y precalienta `docvision` (la primera vez Vision tarda ~30 s en
preparar el modelo; después, ~0,2 s por página), registra los servicios y hace una
prueba de extremo a extremo con el mismo script del clic derecho.

Los servicios guardan la ruta absoluta del proyecto: **si mueves la carpeta, vuelve a
ejecutar `install.sh`** (si no, el clic derecho avisa con una notificación en vez de
fallar en silencio). Para diagnosticar sin reinstalar:

    cli/finder/install.sh --check

`GEMINI_API_KEY` en `.env` solo hace falta para extractos bancarios y el modo IA.

## Uso

Finder > selecciona archivo(s)/carpeta > clic derecho > Servicios (o Acciones
rápidas) > **Convertir con Doc Converter**.

## Cómo saber si está trabajando

- Al empezar aparece una notificación ("Convirtiendo …") y al terminar otra con
  el total y los segundos que tardó.
- Mientras corre, macOS muestra un engranaje girando en la barra de menús.
- Un segundo clic sobre la misma selección mientras aún corre se ignora, para no
  duplicar el trabajo.
- Si algo falla, Automator muestra una alerta con el detalle.
- El registro completo, con tiempos, está en `~/Library/Logs/doc-converter.log`:

      tail -f ~/Library/Logs/doc-converter.log

Desde la terminal:

    .venv/bin/python -m backend.cli archivo.pdf|carpeta [--engine local|ai|vision] [--route auto|finance|markdown]
                                    [--jobs N] [--timeout S] [--no-recursive] [--force] [--dry-run]

Cada línea del resultado indica el motor usado: `[local]`, `[local-ocr]` o `[gemini]`.

Si los servicios no aparecen de inmediato, ejecuta `killall Finder`.

## Desinstalar

    rm -rf ~/Library/Services/"Convertir con Doc Converter.workflow"
    rm -rf ~/Library/Services/"Convertir con Doc Converter (IA).workflow"
