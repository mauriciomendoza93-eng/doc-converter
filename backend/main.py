#!/usr/bin/env python3
"""
main.py — FastAPI stateless + Vercel (Mangum).

Endpoints:
- GET /         → Sirve index.html
- POST /convert → Recibe UploadFile, procesa en memoria, StreamingResponse (descarga)
"""

import os
from dotenv import load_dotenv

load_dotenv()

import io
import sys
import traceback
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from mangum import Mangum

# Asegurar que el paquete 'backend' sea importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.engines.document_converter import convert_to_markdown, convert_document_smart
from backend.engines.finance_converter import convert_to_finance_csv
from backend.utils.http import content_disposition, safe_filename, validate_upload


app = FastAPI(title='doc-conventer', version='2.0.0')

# Vercel requiere handler Mangum
handler = Mangum(app)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, 'frontend')

# Herramientas PDF + almacén local: solo donde están pikepdf/psycopg (servidor HP).
# En Vercel el import falla y la app sigue sirviendo /convert.
try:
    from backend.tools_api import router as tools_router
    app.include_router(tools_router)
except ImportError:
    tools_router = None


@app.get('/tools', response_class=HTMLResponse)
async def tools_page():
    with open(os.path.join(FRONTEND_DIR, 'tools.html'), 'r', encoding='utf-8') as f:
        return HTMLResponse(f.read())


@app.get('/{icon_name:str}')
async def serve_static_icon(icon_name: str):
    if icon_name in {'apple-touch-icon.png', 'favicon.ico', 'favicon.svg', 'favicon-32x32.png', 'icon-192.png', 'icon-512.png', 'manifest.json'}:
        file_path = os.path.join(FRONTEND_DIR, icon_name)
        if os.path.isfile(file_path):
            media_type = 'application/octet-stream'
            if icon_name.endswith('.png'):
                media_type = 'image/png'
            elif icon_name.endswith('.svg'):
                media_type = 'image/svg+xml'
            elif icon_name.endswith('.ico'):
                media_type = 'image/x-icon'
            elif icon_name.endswith('.json'):
                media_type = 'application/manifest+json'
            with open(file_path, 'rb') as f:
                return Response(content=f.read(), media_type=media_type)
    raise HTTPException(status_code=404, detail='Not found')


@app.get('/', response_class=HTMLResponse)
async def index():
    """Sirve el frontend HTML."""
    index_path = os.path.join(FRONTEND_DIR, 'index.html')
    if not os.path.exists(index_path):
        return HTMLResponse('<h1>Frontend no encontrado</h1>', status_code=404)
    with open(index_path, 'r', encoding='utf-8') as f:
        return HTMLResponse(f.read())


CONVERT_EXTS = {'.xls', '.xlsx', '.csv', '.pdf', '.png', '.jpg', '.jpeg'}
UNAVAILABLE_HINTS = ('GEMINI_API_KEY', 'cuota', 'quota', '429', '503', 'UNAVAILABLE', 'timeout')


def _engine_error(result: dict) -> HTTPException:
    """Error de conversión recuperable: 503 si el proveedor no está disponible, 422 si no."""
    msg = result.get('error') or 'No se pudo convertir el archivo.'
    if any(h.lower() in msg.lower() for h in UNAVAILABLE_HINTS):
        return HTTPException(status_code=503, detail=f'Servicio de IA no disponible: {msg}')
    return HTTPException(status_code=422, detail=msg)


def _download(buffer, filename: str, media_type: str) -> StreamingResponse:
    return StreamingResponse(
        buffer, media_type=media_type,
        headers={'Content-Disposition': content_disposition(filename)},
    )


def _markdown_buffer(result: dict, original_name: str):
    filename = result.get('filename') or (original_name.rsplit('.', 1)[0] + '.md')
    return io.BytesIO(result['markdown'].encode('utf-8')), filename


@app.post('/convert')
async def convert(file: UploadFile = File(...), route: str = Form('auto')):
    """
    Convierte archivo subido en memoria.
    Retorna StreamingResponse para forzar descarga en navegador.
    """
    if route not in ('auto', 'finance', 'markdown'):
        raise HTTPException(status_code=400, detail='route debe ser auto, finance o markdown.')
    original_name = safe_filename(file.filename)
    file_bytes = await file.read()
    ext = validate_upload(file_bytes, original_name, CONVERT_EXTS)
    try:
        # Enrutamiento estricto: extensiones financieras → SIEMPRE CSV de finanzas,
        # sin importar el modo que indique el frontend. Nunca van a document_converter.
        if ext in ('.xls', '.xlsx', '.csv'):
            result = convert_to_finance_csv(file_bytes, original_name)
            if not result.get('success'):
                raise _engine_error(result)
            return _download(result['buffer'], result['filename'], 'text/csv')

        # PDF/imagen: un único llamado a Gemini clasifica (¿es un extracto
        # bancario?) y extrae en el mismo paso. Si es financiero, se categoriza
        # con el mismo motor que Excel/CSV; si no, se transcribe a Markdown.
        if route == 'markdown':
            result = convert_to_markdown(file_bytes, original_name)
        else:
            result = convert_document_smart(file_bytes, original_name, force_finance=(route == 'finance'))
        if not result.get('success'):
            raise _engine_error(result)
        if route == 'finance' or result.get('type') == 'finance':
            return _download(result['buffer'], result['filename'], 'text/csv')
        buffer, filename = _markdown_buffer(result, original_name)
        return _download(buffer, filename, 'text/markdown')
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        if any(h.lower() in str(e).lower() for h in UNAVAILABLE_HINTS):
            raise HTTPException(status_code=503, detail='Servicio de IA no disponible. Intenta más tarde.')
        raise HTTPException(status_code=500, detail='Error interno al convertir. Revisa los logs del servidor.')


if __name__ == '__main__':
    import uvicorn
    port = int(os.getenv('PORT', '3000'))
    uvicorn.run('backend.main:app', host='0.0.0.0', port=port, reload=False)