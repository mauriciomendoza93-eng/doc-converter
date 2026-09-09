# Arquitectura de doc-conventer

## Visión general

`doc-conventer` es un conversor de documentos **serverless** desplegado en Vercel.

```
Cliente (browser)
        │
        │  POST /convert (multipart/form-data)
        ▼
FastAPI + Mangum (backend/main.py)
        │
        ├── .xls/.xlsx/.csv → finance_converter.py (pandas → CSV)
        └── .pdf/.png/.jpg  → document_converter.py → vision_provider.py (Gemini → Markdown)
        │
        ▼
StreamingResponse (descarga directa en browser)
```

## Endpoints

| Método | Ruta      | Descripción |
|--------|-----------|-------------|
| GET    | `/`       | Sirve `frontend/index.html` |
| POST   | `/convert`| Convierte archivo subido → CSV o Markdown |

## Lógica de enrutamiento

1. **Extensiones financieras** (`.xls`, `.xlsx`, `.csv`) → SIEMPRE `finance_converter.py` → CSV
2. **Parámetro `route`**:
   - `finance` → `finance_converter.py` → CSV
   - `markdown` → `document_converter.py` → Markdown
   - `auto` (default) → detecta por keywords de banco en filename → CSV, si no → Markdown

## Backend engines

| Archivo | Función |
|---------|---------|
| `backend/engines/finance_converter.py` | Limpia y convierte extractos bancarios a CSV estructurado |
| `backend/engines/document_converter.py` | Orquesta OCR de documentos (PDF/imágenes → Markdown) |
| `backend/engines/vision_provider.py` | Abstracción OCR: Gemini 1.5 Flash vía google-genai SDK |

## Frontend

`frontend/index.html` — HTML autónomo, sin build, sin Node.js.
- CSS vanilla + vanilla JS
- Estilo Hyer Aviation (Deep Ink, Cool Ash, Clay Ember)
- Estados: IDLE → PROCESSING → SUCCESS / ERROR

## Decisiones técnicas

- **Serverless**: Vercel Python build (`@vercel/python`) + Mangum
- **Stateless**: todo en memoria, sin disco ni estado en servidor
- **OCR**: Gemini 1.5 Flash como principal (google-genai SDK)
- **Sin dependencias frontend**: un solo archivo HTML, carga instantánea
- **Path original**: la rama `src/` (parser SEMILLA CLI) existe pero no es el foco actual

## Pendiente

- Configurar `GEMINI_API_KEY` en Vercel para habilitar OCR completo
- Integración real de Canvas (stub definido, API pendiente)
