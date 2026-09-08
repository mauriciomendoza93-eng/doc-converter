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
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from mangum import Mangum

# Asegurar que el paquete 'backend' sea importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.engines.document_converter import convert_to_markdown
from backend.engines.finance_converter import convert_to_finance_csv


app = FastAPI(title='doc-conventer', version='2.0.0')

# Vercel requiere handler Mangum
handler = Mangum(app)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, 'frontend')


@app.get('/', response_class=HTMLResponse)
async def index():
    """Sirve el frontend HTML."""
    index_path = os.path.join(FRONTEND_DIR, 'index.html')
    if not os.path.exists(index_path):
        return HTMLResponse('<h1>Frontend no encontrado</h1>', status_code=404)
    with open(index_path, 'r', encoding='utf-8') as f:
        return HTMLResponse(f.read())


@app.post('/convert')
async def convert(file: UploadFile = File(...), route: str = Form('auto')):
    """
    Convierte archivo subido en memoria.
    Retorna StreamingResponse para forzar descarga en navegador.
    """
    try:
        file_bytes = await file.read()
        original_name = file.filename

        # Enrutamiento estricto: extensiones financieras → SIEMPRE CSV de finanzas,
        # sin importar el modo que indique el frontend. Nunca van a document_converter.
        if original_name.lower().endswith(('.xls', '.xlsx', '.csv')):
            result = convert_to_finance_csv(file_bytes, original_name)
            if not result.get('success'):
                raise HTTPException(status_code=500, detail=result.get('error', 'Error en conversión'))
            buffer = result['buffer']
            filename = result['filename']
            media_type = 'text/csv'
            return StreamingResponse(
                buffer,
                media_type=media_type,
                headers={'Content-Disposition': f'attachment; filename="{filename}"'},
            )

        if route == 'finance':
            result = convert_to_finance_csv(file_bytes, original_name)
            if not result.get('success'):
                raise HTTPException(status_code=500, detail=result.get('error', 'Error en conversión'))
            buffer = result['buffer']
            filename = result['filename']
            media_type = 'text/csv'
        elif route == 'markdown':
            result = convert_to_markdown(file_bytes, original_name)
            if not result.get('success'):
                raise HTTPException(status_code=500, detail=result.get('error', 'Error en conversión'))
            md = result['markdown']
            filename = original_name.rsplit('.', 1)[0] + '.md'
            media_type = 'text/markdown'
            buffer = io.BytesIO(md.encode('utf-8'))
            buffer.seek(0)
        else:  # auto
            name = original_name.lower()
            bank_kw = ['extracto', 'movimientos', 'cuenta', 'banco', 'transferencia']
            if any(kw in name for kw in bank_kw):
                result = convert_to_finance_csv(file_bytes, original_name)
                if not result.get('success'):
                    result = convert_to_markdown(file_bytes, original_name)
                    if not result.get('success'):
                        raise HTTPException(status_code=500, detail=result.get('error', 'Error en conversión'))
                    md = result['markdown']
                    filename = original_name.rsplit('.', 1)[0] + '.md'
                    media_type = 'text/markdown'
                    buffer = io.BytesIO(md.encode('utf-8'))
                    buffer.seek(0)
                else:
                    buffer = result['buffer']
                    filename = result['filename']
                    media_type = 'text/csv'
            else:
                result = convert_to_markdown(file_bytes, original_name)
                if not result.get('success'):
                    raise HTTPException(status_code=500, detail=result.get('error', 'Error en conversión'))
                md = result['markdown']
                filename = original_name.rsplit('.', 1)[0] + '.md'
                media_type = 'text/markdown'
                buffer = io.BytesIO(md.encode('utf-8'))
                buffer.seek(0)

        return StreamingResponse(
            buffer,
            media_type=media_type,
            headers={'Content-Disposition': f'attachment; filename="{filename}"'},
        )
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == '__main__':
    import uvicorn
    port = int(os.getenv('PORT', '3000'))
    uvicorn.run('backend.main:app', host='0.0.0.0', port=port, reload=False)