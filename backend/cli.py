#!/usr/bin/env python3
"""
cli.py — Conversión local de archivos (misma lógica que POST /convert).

Uso:
    python -m backend.cli <archivo|carpeta> [...] [--route auto|finance|markdown] [--engine local|ai]

--engine local (default): todo se convierte en la Mac (Office con markitdown,
PDF con capa de texto con pymupdf4llm, páginas escaneadas e imágenes con el OCR
nativo de Vision). Gemini solo interviene en extractos bancarios, que necesitan
un modelo para extraer las transacciones. --engine ai: todo PDF/imagen pasa por
Gemini (mismo comportamiento que la web).

El resultado (.csv o .md) se escribe junto al archivo original. Si ya existe
un archivo con ese nombre, se agrega un sufijo numérico en vez de sobrescribir.
Las carpetas se procesan sin recursión (solo archivos soportados del primer nivel)
y sus resultados van a una carpeta hermana `<carpeta>_convertida`.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from dotenv import load_dotenv

load_dotenv(PROJECT_DIR / '.env')
logging.getLogger('google_genai').setLevel(logging.ERROR)

from backend.engines import local_converter
from backend.engines.document_converter import convert_to_markdown, convert_document_smart
from backend.engines.finance_converter import convert_to_finance_csv

FINANCE_EXTS = {'.xls', '.xlsx', '.csv'}
DOCUMENT_EXTS = {'.pdf', '.png', '.jpg', '.jpeg'}
SUPPORTED_EXTS = FINANCE_EXTS | DOCUMENT_EXTS | local_converter.OFFICE_EXTS | local_converter.IMAGE_EXTS


def _local_markdown(name: str, md: str) -> tuple:
    return os.path.splitext(name)[0] + '.md', md.encode('utf-8')


def convert_path_local_first(path: Path, route: str) -> tuple:
    """Motor local: retorna (filename, bytes, método) o None si hace falta Gemini."""
    ext = path.suffix.lower()
    if ext in local_converter.OFFICE_EXTS:
        return (*_local_markdown(path.name, local_converter.convert_office(str(path))), 'local')
    if ext in local_converter.IMAGE_EXTS and route != 'finance':
        return (*_local_markdown(path.name, local_converter.convert_image(str(path))), 'local-ocr')
    if ext != '.pdf' or route == 'finance':
        return None

    file_bytes = path.read_bytes()
    # Los extractos bancarios necesitan un modelo que entienda las transacciones
    # para producir el CSV; el resto se resuelve íntegramente en la Mac.
    if route == 'auto' and local_converter.looks_like_bank_statement(local_converter.pdf_first_page_text(file_bytes)):
        try:
            return (*convert_bytes(file_bytes, path.name, route), 'gemini')
        except Exception:
            pass  # sin cuota / sin red: el Markdown local es mejor que nada
    markdown, ocr_pages = local_converter.convert_pdf(file_bytes)
    if not markdown.strip():
        return None
    return (*_local_markdown(path.name, markdown), 'local-ocr' if ocr_pages else 'local')


def convert_bytes(file_bytes: bytes, name: str, route: str) -> tuple:
    """Replica el enrutamiento de backend/main.py:/convert. Retorna (filename, bytes)."""
    if name.lower().endswith(tuple(FINANCE_EXTS)):
        result = convert_to_finance_csv(file_bytes, name)
    elif route == 'markdown':
        result = convert_to_markdown(file_bytes, name)
    else:
        result = convert_document_smart(file_bytes, name, force_finance=(route == 'finance'))

    if not result.get('success'):
        raise RuntimeError(result.get('error') or 'Error en conversión')

    if 'buffer' in result:
        return result['filename'], result['buffer'].getvalue()
    filename = result.get('filename') or (name.rsplit('.', 1)[0] + '.md')
    return filename, result['markdown'].encode('utf-8')


def unique_path(path: Path) -> Path:
    candidate, n = path, 1
    while candidate.exists():
        candidate = path.with_name(f'{path.stem}_{n}{path.suffix}')
        n += 1
    return candidate


def expand_inputs(paths: list) -> list:
    """Retorna pares (archivo, carpeta de salida)."""
    jobs = []
    for raw in paths:
        p = Path(raw).expanduser().resolve()
        if p.is_dir():
            out_dir = p.with_name(f'{p.name}_convertida')
            jobs.extend(
                (f, out_dir) for f in sorted(p.iterdir())
                if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS and not f.name.startswith('.')
            )
        else:
            jobs.append((p, p.parent))
    return jobs


def convert_path(path: Path, out_dir: Path, route: str, engine: str) -> tuple:
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTS:
        raise RuntimeError(f'tipo de archivo no soportado ({path.suffix or "sin extensión"})')
    result = None
    if engine == 'local' or ext in local_converter.OFFICE_EXTS or ext in local_converter.IMAGE_EXTS:
        result = convert_path_local_first(path, route)
    if result is None:
        method = 'local' if ext in FINANCE_EXTS else 'gemini'
        result = (*convert_bytes(path.read_bytes(), path.name, route), method)
    filename, data, method = result
    out_dir.mkdir(exist_ok=True)
    out_path = unique_path(out_dir / os.path.basename(filename))
    out_path.write_bytes(data)
    return out_path, method


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='doc-convert', description='Convierte documentos a CSV/Markdown.')
    parser.add_argument('paths', nargs='+', help='Archivos o carpetas a convertir')
    parser.add_argument('--route', choices=('auto', 'finance', 'markdown'), default='auto')
    parser.add_argument('--engine', choices=('local', 'ai'), default='local')
    args = parser.parse_args(argv)

    jobs = expand_inputs(args.paths)
    if not jobs:
        print('No se encontraron archivos soportados.', file=sys.stderr)
        return 1

    errors = 0
    for path, out_dir in jobs:
        try:
            out_path, method = convert_path(path, out_dir, args.route, args.engine)
            print(f'{path.name} -> {out_path.name} [{method}]')
        except Exception as e:
            errors += 1
            print(f'Error con {path.name}: {e}', file=sys.stderr)
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
