"""
jobs.py — Conversión de UN archivo para el CLI/Finder.

Se puede usar como biblioteca (`convert_file`) o como worker aislado:

    echo '{"path": "...", "engine": "local", "route": "auto", "scratch": "/tmp/x"}' | python -m backend.jobs

El worker escribe los resultados en `scratch` y responde con una línea JSON.
Correr cada archivo en su propio proceso aísla cuelgues y caídas de las
librerías nativas (MuPDF, Vision) y permite matar uno por tiempo límite sin
perder el resto del lote. Los imports pesados son diferidos: un .docx no paga
el costo de importar pandas ni el SDK de Gemini.
"""

import json
import os
import sys
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

ALREADY_TEXT_EXTS = {'.md', '.markdown', '.txt'}
FINANCE_EXTS = {'.xls', '.xlsx', '.xlsm', '.csv'}
DOCUMENT_EXTS = {'.pdf', '.png', '.jpg', '.jpeg'}
OFFICE_EXTS = {'.docx', '.pptx', '.html', '.htm', '.epub', '.doc', '.rtf', '.odt'}
IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.heic', '.webp', '.bmp', '.gif'}
SUPPORTED_EXTS = FINANCE_EXTS | DOCUMENT_EXTS | OFFICE_EXTS | IMAGE_EXTS


class Skip(Exception):
    """El archivo no necesita conversión (no es un error)."""


@dataclass
class Outcome:
    outputs: list = field(default_factory=list)   # [(nombre sugerido, bytes)]
    method: str = ''
    warnings: list = field(default_factory=list)
    pages: int = 0
    ocr_pages: int = 0
    structured_pages: int = 0


def _bootstrap():
    """Entorno del CLI: .env del proyecto, sin ruido de librerías."""
    warnings.filterwarnings('ignore', module='pydub')
    from dotenv import load_dotenv
    load_dotenv(PROJECT_DIR / '.env')
    import logging
    logging.getLogger('google_genai').setLevel(logging.ERROR)


def _md(name: str, markdown: str) -> list:
    return [(os.path.splitext(name)[0] + '.md', markdown.encode('utf-8'))]


def _convert_bytes(file_bytes: bytes, name: str, route: str, ai_categorize: bool = True) -> tuple:
    """Replica el enrutamiento de backend/main.py:/convert. Retorna (filename, bytes, warnings)."""
    from backend.engines.document_converter import convert_to_markdown, convert_document_smart
    from backend.engines.finance_converter import convert_to_finance_csv

    if name.lower().endswith(tuple(FINANCE_EXTS)):
        result = convert_to_finance_csv(file_bytes, name, ai_categorize)
    elif route == 'markdown':
        result = convert_to_markdown(file_bytes, name)
    else:
        result = convert_document_smart(file_bytes, name, force_finance=(route == 'finance'))

    if not result.get('success'):
        raise RuntimeError(result.get('error') or 'Error en conversión')
    warns = list(result.get('warnings') or [])
    if 'buffer' in result:
        return result['filename'], result['buffer'].getvalue(), warns
    # El nombre que propone Gemini sale del contenido; en el CLI el Markdown
    # conserva el nombre del original.
    return os.path.splitext(name)[0] + '.md', result['markdown'].encode('utf-8'), warns


def _convert_spreadsheet(path: Path, route: str, engine: str) -> Outcome:
    from backend.engines import local_converter
    kind = 'finance' if route == 'finance' else local_converter.spreadsheet_kind(str(path))
    if kind == 'finance':
        # El motor local no manda nada a Gemini: la categorización por IA es solo de ai/vision.
        name, data, warns = _convert_bytes(path.read_bytes(), path.name, 'finance', ai_categorize=(engine != 'local'))
        return Outcome([(name, data)], 'local', warns)
    if path.suffix.lower() == '.csv':
        raise Skip('CSV sin cabecera de movimientos (fecha + monto): ya es tabular, no hay nada que convertir')
    outputs = local_converter.convert_spreadsheet_generic(str(path))
    note = ['no parece un extracto bancario: se exportó cada hoja tal cual a CSV (usa --route finance para forzar el formato de movimientos)']
    return Outcome(outputs, 'local-hojas', note)


def _convert_pdf_local(path: Path, route: str) -> Outcome:
    from backend.engines import local_converter
    file_bytes = path.read_bytes()
    first_page = local_converter.pdf_first_page_text(file_bytes)  # valida cifrado/dañado/vacío
    warns = []
    # Los extractos bancarios necesitan un modelo que entienda las transacciones
    # para producir el CSV; el resto se resuelve íntegramente en la Mac.
    if route == 'auto' and local_converter.looks_like_bank_statement(first_page):
        try:
            name, data, w = _convert_bytes(file_bytes, path.name, route)
            return Outcome([(name, data)], 'gemini', w)
        except Exception as e:
            warns.append(f'extracto bancario: Gemini no disponible ({e}); se dejó Markdown local')
    r = local_converter.convert_pdf_ex(file_bytes)
    if not r.markdown.strip():
        raise RuntimeError('no se pudo extraer texto del PDF')
    method = 'local-ocr' if r.ocr_pages else 'local'
    return Outcome(_md(path.name, r.markdown), method, warns + r.warnings,
                   r.pages, r.ocr_pages, r.structured_pages)


def _extract_text(path: Path) -> tuple:
    """Texto crudo extraído en la Mac. Retorna (texto, Outcome parcial)."""
    from backend.engines import local_converter
    ext = path.suffix.lower()
    if ext in local_converter.OFFICE_EXTS:
        return local_converter.convert_office(str(path)), Outcome()
    if ext in local_converter.IMAGE_EXTS:
        text, note = local_converter.convert_image(str(path))
        return text, Outcome(warnings=[note] if note else [])
    r = local_converter.convert_pdf_ex(path.read_bytes())
    return r.markdown, Outcome(warnings=r.warnings, pages=r.pages, ocr_pages=r.ocr_pages,
                               structured_pages=r.structured_pages)


def _convert_ai(path: Path) -> Outcome:
    """Modo IA: texto extraído localmente → Gemini lo reestructura. Si Gemini no
    está disponible conserva el texto local y lo declara."""
    from backend.engines import text_structurer
    text, partial = _extract_text(path)
    if not text.strip():
        raise RuntimeError('sin texto extraíble')
    try:
        markdown, metodo = text_structurer.structure_markdown(text)
        partial.method = f'local + {metodo}'
    except Exception as e:
        markdown = text
        partial.method = 'local'
        partial.warnings.append(f'IA no disponible, se conservó el texto local: {e}')
    partial.outputs = _md(path.name, markdown)
    return partial


def _convert_local_first(path: Path, route: str) -> Outcome:
    from backend.engines import local_converter
    ext = path.suffix.lower()
    if ext in local_converter.OFFICE_EXTS:
        return Outcome(_md(path.name, local_converter.convert_office(str(path))), 'local')
    if ext in local_converter.IMAGE_EXTS and route != 'finance':
        text, note = local_converter.convert_image(str(path))
        return Outcome(_md(path.name, text), 'local-ocr', [note] if note else [], pages=1, ocr_pages=1)
    if ext == '.pdf':
        return _convert_pdf_local(path, route)
    raise RuntimeError(f'ruta {route!r} no aplica a {ext}')


def convert_file(path: Path, route: str = 'auto', engine: str = 'local') -> Outcome:
    _bootstrap()
    ext = path.suffix.lower()
    if not path.is_file():
        raise RuntimeError('el archivo no existe o no es un archivo regular')
    if path.stat().st_size == 0:
        raise RuntimeError('el archivo está vacío (0 bytes)')
    if ext in ALREADY_TEXT_EXTS:
        raise Skip(f'ya es texto ({ext})')
    if ext not in SUPPORTED_EXTS:
        raise RuntimeError(f'tipo de archivo no soportado ({path.suffix or "sin extensión"})')

    if ext in FINANCE_EXTS:
        return _convert_spreadsheet(path, route, engine)
    if engine == 'ai' and route != 'finance':
        return _convert_ai(path)
    # Office e imágenes siempre se resuelven en la Mac; PDFs, solo con el motor local.
    if (engine == 'local' or ext in OFFICE_EXTS or ext in IMAGE_EXTS) and not (route == 'finance' and ext in DOCUMENT_EXTS):
        return _convert_local_first(path, route)
    # engine 'vision', o PDF/imagen con --route finance: Gemini mira el documento.
    name, data, warns = _convert_bytes(path.read_bytes(), path.name, route)
    return Outcome([(name, data)], 'gemini', warns)


# --- Worker ------------------------------------------------------------------

def _worker(job: dict) -> dict:
    started = time.monotonic()
    result = {'ok': False}
    try:
        outcome = convert_file(Path(job['path']), job.get('route', 'auto'), job.get('engine', 'local'))
        scratch = Path(job['scratch'])
        scratch.mkdir(parents=True, exist_ok=True)
        files = []
        for i, (name, data) in enumerate(outcome.outputs):
            tmp = scratch / f'{i}.out'
            tmp.write_bytes(data)
            files.append({'name': name, 'tmp': str(tmp), 'bytes': len(data)})
        result.update(ok=True, files=files, method=outcome.method, warnings=outcome.warnings,
                      pages=outcome.pages, ocr_pages=outcome.ocr_pages,
                      structured_pages=outcome.structured_pages)
    except Skip as s:
        result.update(ok=True, skipped=str(s), files=[])
    except Exception as e:
        result['error'] = str(e) or e.__class__.__name__
    result['seconds'] = round(time.monotonic() - started, 2)
    return result


if __name__ == '__main__':
    # Las librerías pueden imprimir en stdout; el protocolo usa solo la última línea.
    real_stdout, sys.stdout = sys.stdout, sys.stderr
    payload = _worker(json.loads(sys.stdin.read()))
    real_stdout.write(json.dumps(payload, ensure_ascii=False) + '\n')
    real_stdout.flush()
