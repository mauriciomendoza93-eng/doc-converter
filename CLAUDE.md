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
| `ANTHROPIC_API_KEY` | (Opcional) Fallback Anthropic Vision |

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