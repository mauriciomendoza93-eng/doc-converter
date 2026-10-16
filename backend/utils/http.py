"""
http.py — Validación de subidas y cabeceras de descarga seguras.
"""

import os
import re
import unicodedata
from urllib.parse import quote

from fastapi import HTTPException

MAX_UPLOAD_MB = float(os.getenv('MAX_UPLOAD_MB', '25'))

# Firma (magic bytes) esperada por extensión. None = texto, sin firma fija.
SIGNATURES = {
    '.pdf': (b'%PDF-',),
    '.png': (b'\x89PNG\r\n\x1a\n',),
    '.jpg': (b'\xff\xd8\xff',),
    '.jpeg': (b'\xff\xd8\xff',),
    '.xlsx': (b'PK\x03\x04',),
    '.docx': (b'PK\x03\x04',),
    '.pptx': (b'PK\x03\x04',),
    '.odt': (b'PK\x03\x04',),
    '.ods': (b'PK\x03\x04',),
    '.odp': (b'PK\x03\x04',),
    '.xls': (b'\xd0\xcf\x11\xe0',),
    '.doc': (b'\xd0\xcf\x11\xe0',),
    '.ppt': (b'\xd0\xcf\x11\xe0',),
    '.csv': None,
    '.rtf': (b'{\\rtf',),
}


def safe_filename(name: str | None, default: str = 'archivo') -> str:
    """Quita rutas, caracteres de control y separadores; conserva tildes."""
    name = os.path.basename((name or '').replace('\\', '/'))
    name = unicodedata.normalize('NFC', name)
    name = re.sub(r'[\x00-\x1f\x7f"<>:|?*/]+', '', name).strip(' .')
    return name[:150] or default


def content_disposition(filename: str) -> str:
    """Cabecera attachment con fallback ASCII y nombre UTF-8 (RFC 6266/5987)."""
    filename = safe_filename(filename)
    ascii_name = unicodedata.normalize('NFKD', filename).encode('ascii', 'ignore').decode() or 'descarga'
    ascii_name = ascii_name.replace('\\', '').replace('"', '')
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename)}'


def validate_upload(data: bytes, filename: str, allowed: set[str]) -> str:
    """Valida tamaño, extensión y firma real. Devuelve la extensión en minúsculas."""
    if not data:
        raise HTTPException(status_code=400, detail='El archivo está vacío.')
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f'El archivo supera el límite de {MAX_UPLOAD_MB:g} MB.')
    ext = os.path.splitext(filename.lower())[1]
    if ext not in allowed:
        raise HTTPException(
            status_code=415,
            detail=f'Formato no soportado ({ext or "sin extensión"}). Usa: {", ".join(sorted(allowed))}.',
        )
    sigs = SIGNATURES.get(ext)
    if sigs and not any(data.startswith(s) for s in sigs):
        raise HTTPException(status_code=415, detail=f'El contenido no corresponde a un archivo {ext}.')
    return ext
