# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Proyecto

`doc-conventer` — Conversor de documentos financieros y legales a CSV/Markdown desplegado en Vercel (FastAPI + HTML vanilla).

- **Frontend**: `frontend/index.html` — HTML único con CSS/JS vanilla, estilo Hyer Aviation
- **Backend**: `backend/main.py` — FastAPI serverless con Mangum
- **Endpoint**: `POST /convert` (multipart/form-data)

## Comandos

```bash
# Desarrollo local (Python)
cd backend && pip install -r requirements.txt
python main.py          # Levanta en http://localhost:3000

# Desarrollo local (con Vercel CLI)
vercel dev

# Deploy a producción
vercel --prod
```

## Uso local (Finder / CLI)

`backend/cli.py` convierte archivos del disco y `cli/finder/install.sh` instala
el Servicio de Finder **Convertir con Doc Converter** (motor local; el antiguo **(IA)**
se retiró en 2026-10 y el installer lo quita). Detalle en `cli/finder/README.md`. Usa
`.venv/` + `.env` del proyecto, y `.venv-ocr/` para el OCR con modelo.

```bash
.venv/bin/python -m backend.cli <archivo|carpeta> [--engine local|ai] [--route auto|finance|markdown]
cli/finder/install.sh   # registra el clic derecho > Servicios
```

`backend/cli.py` planifica (`backend/batch.py`: carpetas recursivas, nombres sin
colisiones, `_informe.json`) y ejecuta cada archivo en un proceso worker aislado con
tiempo límite (`backend/jobs.py`). `cli/docvision/` es un helper Swift (Vision
`RecognizeDocumentsRequest`, macOS 26+) que reconoce tablas/listas; `install.sh` lo
compila y precalienta (el arranque en frío son ~30 s por binario nuevo).
`backend/engines/local_converter.py` es el motor local (markitdown, textutil,
pymupdf4llm y OCR por página: `read_images` prueba la nube (cadena de `vlm_ocr.CLOUD_CHAIN`:
Haiku 5.5 con `ANTHROPIC_API_KEY`, Claude Sonnet 4.6 vía OmniRoute, Gemini), luego GLM-OCR local
vía MLX (helper `cli/vlmocr/` en su propio `.venv-ocr`, porque mlx-vlm choca con las versiones
de fastapi de la web) y Vision al final; el OCR plano de Vision valida cada lectura.
`DOC_CONVERTER_OCR=auto|local|vision` (CLI `--ocr`) decide si la imagen sale de la Mac;
second-brain fuerza `local`. Mediciones en `docs/ocr-benchmark.md`) y `backend/engines/text_structurer.py`
reestructura ese texto con Gemini en modo texto (`--engine ai`). Las fotos se recortan
y enderezan al documento con Vision antes del OCR (`crop_to_document`); si la
detección es dudosa, solo lo sugiere en la salida. Solo los usa el
CLI: sus dependencias están en `requirements-local.txt` y **no** se despliegan en
Vercel.

`backend/engines/llm_router.py` reparte las llamadas de IA del CLI entre OmniRoute y Gemini
con respaldo, validación de contenido y memoria de modelos caídos (`docs/llm-benchmark.md`);
la web de Vercel sigue con Gemini directo (sin `OMNIROUTE_API_KEY`).

Si mueves el proyecto, los servicios de Finder se rompen (ruta absoluta): vuelve a
ejecutar `cli/finder/install.sh` (`--check` diagnostica).

Se evaluó y descartó un modelo local (Ollama) para estructurar: en un documento
de 21 páginas recortó más de la mitad del contenido y tardó 4 min 21 s frente a
57 s de Gemini. También se descartó docling: su OCR lee peor que Vision. Y MinerU 4 (2026-09):
estructura mejor tablas escaneadas, pero 40 s/página, ~2 GB, servidor propio,
nube china por defecto y aún con errores de dígitos; para escaneos puntuales
basta `--engine vision` (Gemini). GLM-OCR / PaddleOCR-VL vía MLX son candidatos
no evaluados aquí si `docvision` no alcanza en algún documento. **Actualización 2026-10**:
se evaluaron (`docs/ocr-benchmark.md`): Claude Sonnet 4.6 vía OmniRoute lee mejor que todo
lo local; GLM-OCR 8 bits es el respaldo sin red; PaddleOCR-VL-1.6 4 bits leyó peor que Vision
y LightOnOCR-3 entró en bucle. Mistral OCR no hace falta (Claude gratis lee mejor; Haiku 5.5
cuesta menos).

## Servidor propio (HP)

`deploy/hp/deploy.sh` despliega la misma app en Docker en el HP
(https://hp-server.taila36576.ts.net:8300, solo tailnet) con `DATA_DIR`, lo que activa
`/tools` y `/api/*` (`backend/tools_api.py`, `backend/store.py`,
`backend/engines/pdf_tools.py`: pikepdf, PyMuPDF, LibreOffice, ocrmypdf). Originales
inmutables, salidas versionadas, eventos de solo-anexado en Postgres (`doc_converter`),
caducidad y respaldo restic diario. En Vercel el import de pikepdf falla y solo queda `/convert`.
Tests: `.venv/bin/python -m pytest tests/`. La web (Vercel y HP) convierte un archivo por
subida; el lote por carpetas (primer nivel) es solo del CLI/Finder.

## Arquitectura (big picture)

```
Archivo subido (multipart) → FastAPI /convert → Procesamiento en memoria
    ├── .xls/.xlsx/.csv → Finance CSV (pandas) → StreamingResponse CSV (blob)
    └── .pdf/.png/.jpg  → OCR con Gemini Vision → StreamingResponse Markdown
```

## Endpoints

| Método | Ruta      | Descripción |
|--------|-----------|-------------|
| GET    | `/`       | Sirve `frontend/index.html` |
| POST   | `/convert`| Convierte archivo, retorna descarga forzada |

### `POST /convert`

**Entrada:** `multipart/form-data`
- `file` (UploadFile): `.xls`, `.xlsx`, `.csv`, `.pdf`, `.png`, `.jpg`
- `route` (string, opcional): `auto` | `finance` | `markdown` (default: `auto`)

**Salida:** `StreamingResponse` con `Content-Disposition: attachment`
- CSV binario para financieros (`text/csv`)
- Markdown texto para legales (`text/markdown`)

## Lógica de enrutamiento (backend/main.py:49-127)

```python
if filename.endswith(('.xls', '.xlsx', '.csv')):
    # SIEMPRE finance CSV, sin importar route
    convert_to_finance_csv()
elif route == 'finance':
    convert_to_finance_csv()
elif route == 'markdown':
    convert_to_markdown()
else:  # auto
    if 'banco' keywords in filename:
        convert_to_finance_csv()
    else:
        convert_to_markdown()
```

## Backend engines

| Archivo | Función |
|---------|---------|
| `backend/engines/finance_converter.py` | `convert_to_finance_csv(bytes, name) → {success, buffer, filename, error}` |
| `backend/engines/document_converter.py` | `convert_to_markdown(bytes, name) → {success, markdown, error}` |
| `backend/engines/vision_provider.py` | OCR con Gemini (google-genai SDK, `types.Part` para PDF bytes) |

## Frontend (`frontend/index.html`)

- **Autónomo**: sin build, sin Node, sin React, sin Tailwind CDN
- **Estilo**: Hyer Aviation — Deep Ink `#000d10`, Cool Ash `#8e8e95`, Clay Ember `#bc7155`, Pebble `#d5d3d4`
- **Botones**: píldora (`border-radius: 1000px`)
- **Tarjetas**: bordes rectos `4px`, sin sombras
- **Estados**: IDLE → PROCESSING (spinner + progress bar) → SUCCESS (descarga/visor MD + copy) / ERROR

## Configuración Vercel

```json
{
  "builds": [{ "src": "backend/main.py", "use": "@vercel/python" }],
  "routes": [{ "src": "/(.*)", "dest": "backend/main.py" }]
}
```

## Variables de entorno requeridas

| Variable | Descripción |
|----------|-------------|
| `GEMINI_API_KEY` | Clave Google Gemini para OCR Vision (requerida para PDF/imágenes) |
| `ANTHROPIC_API_KEY` | (Opcional, solo CLI/Finder) Claude Haiku 5.5 primero en la cadena de OCR |

## Estado actual

✅ **Producción**: https://doc-conventer.vercel.app
- Frontend HTML vanilla (estilo Hyer Aviation) servido desde FastAPI `/`
- Backend FastAPI serverless con enrutamiento estricto por extensión
- CSV financiero: pandas + detección dinámica de headers + limpieza numérica
- OCR legal: Gemini 1.5 Flash via google-genai SDK (`types.Part` para PDF bytes)
- Deploy automático en push a `main`

## Estructura del repo

```
doc-conventer/
├── backend/
│   ├── main.py                      # FastAPI + Mangum handler
│   └── engines/
│       ├── finance_converter.py     # CSV financiero
│       ├── document_converter.py    # Markdown + OCR
│       └── vision_provider.py       # Gemini Vision SDK
├── frontend/
│   └── index.html                   # HTML autónomo (CSS + JS inline)
├── vercel.json                      # Config Vercel (Python build)
├── requirements.txt                 # fastapi, mangum, pandas, google-genai, etc.
└── CLAUDE.md                        # Este archivo
```