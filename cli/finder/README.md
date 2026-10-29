# Acción Rápida de Finder

`install.sh` registra **un** Servicio de clic derecho: **Convertir con Doc Converter**
(`--engine local`). Los documentos digitales se convierten en la Mac en segundos; los escaneos
y fotos los lee el mejor modelo de visión disponible (ver abajo).

Hasta 2026-10 había un segundo servicio, **(IA)**, que mandaba el texto del OCR a modelos en
la nube para reordenarlo. Ya no hace falta: ahora el modelo lee la imagen de la página entera
en orden y sin la basura que lo motivaba. `install.sh` lo quita; el motor sigue disponible
por terminal (`--engine ai`).

## OCR de escaneos y fotos: nube → modelo local → Vision

El OCR de Vision lee letra por letra. En escaneos de fax, fotos con sombra o márgenes
cortados entregaba "edame", "arciles,jen", "iforniación" y restos de sellos ("LUNCA DEL RO").
Ahora cada página escaneada o foto se lee con modelos de visión, por página y en este orden
(medido en `docs/ocr-benchmark.md`):

1. **Nube**, gratis con tus claves actuales: Claude Sonnet 4.6 (OmniRoute) → Gemini 3 Flash →
   Gemini 3.1 Flash-Lite → Gemini 3.6 Flash. Con `ANTHROPIC_API_KEY` en `.env` va primero
   **Claude Haiku 5.5** (de pago, ~US$ 1 por 1 000 páginas). Tres páginas a la vez.
2. **GLM-OCR en la Mac** (0,9 B, 8 bits, MLX en `.venv-ocr`): sin red ni cuota, 15-90 s por
   página y con más errores de contenido que la nube.
3. **Vision**, si ningún modelo pasó la validación.

Vision corre siempre (0,2 s por página) y su OCR plano es la **referencia**: una lectura se
descarta y se prueba el siguiente lector si entró en bucle, cubre menos del 75 % del texto de
Vision (se saltó algo), tiene 1,6× más texto (inventó) o comenta la imagen en vez de
transcribirla. Las cifras que Vision leyó y el modelo no reproduce se avisan para revisarlas.
El método del informe nombra los lectores usados: `ocr claude-sonnet-4-6 + glm-ocr`.

Dónde va la imagen (`--ocr` o `DOC_CONVERTER_OCR`):

| Modo | Lectores | La imagen sale de la Mac |
|---|---|---|
| `auto` (default, Finder) | nube → GLM-OCR → Vision | Sí, a OmniRoute/Google (y Anthropic con clave) |
| `local` (second-brain) | GLM-OCR → Vision | No |
| `vision` | Vision | No |

- **Lo que no corrige**: letras que no están en la imagen (margen cortado por el escáner) se
  completan por contexto y a veces mal. Revisa siempre cifras, nombres y fechas.
- GLM-OCR se descarga una vez al instalar (~1,1 GB en `~/.cache/huggingface`); leer con él no
  usa la red. Otro modelo: `DOC_CONVERTER_OCR_MODEL=mlx-community/GLM-OCR-4bit` (30 % más
  rápido, algo más de errores). Otra cadena de nube: `DOC_CONVERTER_LLM_OCR_CHAIN`.
- Estado de cada lector: `.venv/bin/python -m backend.engines.llm_router status`.

## Motores disponibles (`--engine`)

| Motor | Qué hace | Medido con documentos reales |
|---|---|---|
| `local` (default) | Extracción en la Mac; escaneos y fotos según `--ocr` (nube → GLM-OCR → Vision) | Digitales: 6 archivos en 7 s |
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
| `.pdf` escaneado o mixto | pymupdf4llm + OCR por página (nube → GLM-OCR → Vision) | Markdown |
| `.png`, `.jpg`, `.tiff`, `.heic`, `.webp`, `.bmp`, `.gif` | recorte del documento + OCR (nube → GLM-OCR → Vision) | Markdown |
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
preparar el modelo; después, ~0,2 s por página), crea `.venv-ocr` y descarga GLM-OCR
(`cli/vlmocr/setup.sh`, solo Apple Silicon), registra el servicio y hace una prueba de
extremo a extremo con el mismo script del clic derecho.

Los servicios guardan la ruta absoluta del proyecto: **si mueves la carpeta, vuelve a
ejecutar `install.sh`** (si no, el clic derecho avisa con una notificación en vez de
fallar en silencio). Para diagnosticar sin reinstalar:

    cli/finder/install.sh --check

`GEMINI_API_KEY` en `.env` solo hace falta para extractos bancarios y el modo IA.

## Uso

Finder > selecciona archivo(s)/carpeta > clic derecho > Servicios (o Acciones
rápidas) > **Convertir con Doc Converter**.

Un lote con escaneos tarda más: el tiempo límite por archivo crece con sus páginas
(240 s por página y por worker, sobre los 900 s de base) y los workers se turnan la GPU.

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
                                    [--ocr auto|local|vision] [--jobs N] [--timeout S] [--no-recursive] [--force] [--dry-run]

Cada línea del resultado indica el motor usado: `[local]`, `[local-ocr]` (solo Vision), `[ocr claude-sonnet-4-6 + glm-ocr]` (modelos que leyeron páginas) o `[gemini]`.

Si los servicios no aparecen de inmediato, ejecuta `killall Finder`.

## Desinstalar

    rm -rf ~/Library/Services/"Convertir con Doc Converter.workflow"
    rm -rf .venv-ocr   # y el modelo: ~/.cache/huggingface/hub/models--mlx-community--GLM-OCR-8bit
