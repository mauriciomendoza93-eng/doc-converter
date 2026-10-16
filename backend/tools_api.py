"""
tools_api.py — Herramientas PDF con almacén local (solo servidor propio, no Vercel).

Todo resultado queda en DATA_DIR con su original inmutable y un evento auditable.
"""

import io
import json
import os
import zipfile

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from backend import store
from backend.engines import pdf_tools
from backend.utils.http import content_disposition, safe_filename, validate_upload

router = APIRouter(prefix='/api')

PDF = {'.pdf'}
OFFICE = {'.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.odt', '.ods', '.odp', '.rtf'}
OPS = ('merge', 'split', 'reorder', 'rotate', 'compress', 'watermark', 'redact', 'ocr', 'office')


def _require_store():
    if not store.enabled():
        raise HTTPException(status_code=503, detail='Herramientas PDF solo disponibles en el servidor local (DATA_DIR sin configurar).')


async def _read(files: list[UploadFile], allowed: set[str]) -> list[tuple[str, bytes]]:
    out = []
    for f in files:
        name = safe_filename(f.filename)
        data = await f.read()
        validate_upload(data, name, allowed)
        out.append((name, data))
    return out


@router.get('/status')
def status():
    return {'storage': store.enabled(), 'expiry_days': store.EXPIRY_DAYS,
            'database': bool(store.DATABASE_URL),
            'office': pdf_tools._soffice() is not None,
            'ocr': pdf_tools.shutil.which('ocrmypdf') is not None}


@router.post('/inspect')
async def inspect(file: UploadFile = File(...)):
    [(_, data)] = await _read([file], PDF)
    try:
        return pdf_tools.inspect(data)
    except pdf_tools.ToolInputError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post('/preview')
async def preview(file: UploadFile = File(...), page: int = Form(1)):
    [(_, data)] = await _read([file], PDF)
    try:
        return Response(pdf_tools.preview(data, page), media_type='image/png')
    except pdf_tools.ToolInputError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post('/pdf/{op}')
async def run(op: str, files: list[UploadFile] = File(...), pages: str = Form(''),
              degrees: int = Form(90), text: str = Form(''), lang: str = Form('spa+eng')):
    _require_store()
    if op not in OPS:
        raise HTTPException(status_code=404, detail=f'Operación desconocida. Usa: {", ".join(OPS)}')
    if op != 'merge' and len(files) != 1:
        raise HTTPException(status_code=400, detail='Esta operación recibe un solo archivo.')
    items = await _read(files, OFFICE if op == 'office' else PDF)
    hashes = [store.save_original(data, name) for name, data in items]
    name, data = items[0]
    warnings = [] if op == 'office' else pdf_tools.inspect(data)['warnings']
    extra = {}
    try:
        if op == 'merge':
            results = [pdf_tools.merge([d for _, d in items])]
        elif op == 'split':
            results = pdf_tools.split(data, pages)
        elif op == 'reorder':
            results = [pdf_tools.reorder(data, pages)]
        elif op == 'rotate':
            results = [pdf_tools.rotate(data, degrees, pages)]
        elif op == 'compress':
            results = [pdf_tools.compress(data)]
            extra = {'before': len(data), 'after': len(results[0])}
        elif op == 'watermark':
            results = [pdf_tools.watermark(data, text)]
        elif op == 'redact':
            pdf, hits = pdf_tools.redact(data, text.split('\n'))
            results, extra = [pdf], {'hits': hits}
        elif op == 'ocr':
            results = [pdf_tools.ocr(data, lang)]
        else:
            results = [pdf_tools.office_to_pdf(data, name)]
    except pdf_tools.ToolUnavailable as e:
        store.log_event(op, 'unavailable', hashes, error=str(e))
        raise HTTPException(status_code=503, detail=str(e))
    except pdf_tools.ToolInputError as e:
        store.log_event(op, 'rejected', hashes, error=str(e))
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        store.log_event(op, 'error', hashes, error=str(e)[:300])
        raise HTTPException(status_code=500, detail='Error interno en la operación.')

    outputs = [store.save_output(r, op if len(results) == 1 else f'{op}{i + 1}', name, '.pdf',
                                 hashes, warnings=warnings, **extra)
               for i, r in enumerate(results)]
    return {'outputs': outputs, 'warnings': warnings, **extra}


@router.get('/files')
def files():
    _require_store()
    store.purge_expired()
    return store.list_outputs()


@router.get('/files/{out_id}')
def download(out_id: str):
    _require_store()
    found = store.output_path(out_id)
    if not found:
        raise HTTPException(status_code=404, detail='El archivo no existe o ya caducó.')
    meta, path = found
    store.log_event('download', 'delivered', meta['inputs'], meta['sha256'], out_id)
    return FileResponse(path, media_type='application/pdf',
                        headers={'Content-Disposition': content_disposition(meta['filename'])})


@router.delete('/files/{out_id}')
def delete(out_id: str):
    _require_store()
    if not store.delete_output(out_id):
        raise HTTPException(status_code=404, detail='El archivo no existe.')
    return {'deleted': out_id}


@router.delete('/originals/{digest}')
def delete_original(digest: str):
    _require_store()
    if not store.delete_original(digest):
        raise HTTPException(status_code=404, detail='El original no existe.')
    return {'deleted': digest}


@router.get('/events')
def events(limit: int = 200):
    _require_store()
    return store.read_events(min(limit, 2000))


@router.get('/export')
def export():
    """ZIP con resultados vigentes, sus metadatos y el registro de eventos."""
    _require_store()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for meta in store.list_outputs():
            _, path = store.output_path(meta['id'])
            z.write(path, f"outputs/{meta['id']}/{meta['filename']}")
            z.writestr(f"outputs/{meta['id']}/meta.json", json.dumps(meta, ensure_ascii=False, indent=2))
        z.writestr('events.json', json.dumps(store.read_events(100_000), ensure_ascii=False, indent=2, default=str))
    buf.seek(0)
    store.log_event('export', 'delivered')
    return StreamingResponse(buf, media_type='application/zip',
                             headers={'Content-Disposition': content_disposition('doc-converter-export.zip')})
