"""
structured_ocr.py — OCR con estructura (tablas, listas, títulos) 100% local.

Envuelve el binario `docvision` (cli/docvision/docvision.swift), que usa
RecognizeDocumentsRequest de Vision (macOS 26+): reconoce tablas con sus celdas,
listas y párrafos en el dispositivo, sin modelo externo ni cuota. Solo lo usa el
CLI/Finder.

Garantía de contenido: el Markdown estructurado se compara contra el OCR plano
de la misma página. Si el estructurado pierde texto (recall bajo), se descarta
esa página y se conserva el plano: peor formato es preferible a perder datos.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
DOCVISION_BIN = PROJECT_DIR / 'cli' / 'docvision' / '.build' / 'docvision'

# Fracción mínima de los tokens del OCR plano que debe reaparecer en el
# estructurado. Dos pasadas de OCR no coinciden al 100 % (puntuación, guiones),
# por eso no se exige 1.0; por debajo de esto sí falta contenido.
MIN_RECALL = 0.9
_TOKEN = re.compile(r'[0-9A-Za-zÀ-ÿ]+(?:[.,][0-9]+)*')
_TIMEOUT_PER_IMAGE = 60


def available() -> bool:
    return sys.platform == 'darwin' and DOCVISION_BIN.is_file() and DOCVISION_BIN.stat().st_mode & 0o111 != 0


def analyze(image_paths: list) -> list:
    """Corre docvision sobre las imágenes y retorna una lista de bloques por
    imagen (o None si esa imagen falló). Lanza RuntimeError si el binario falla."""
    if not image_paths:
        return []
    try:
        proc = subprocess.run(
            [str(DOCVISION_BIN), *map(str, image_paths)],
            capture_output=True, timeout=_TIMEOUT_PER_IMAGE * len(image_paths) + 30,
        )
        payload = json.loads(proc.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as e:
        raise RuntimeError(f'docvision falló: {e}') from e
    pages = payload.get('pages', [])
    if len(pages) != len(image_paths):
        raise RuntimeError('docvision devolvió un número inesperado de páginas')
    return [None if 'error' in p else p.get('blocks', []) for p in pages]


def _cell(text: str) -> str:
    return text.replace('|', '\\|').replace('\n', ' ').strip()


def _table_md(rows: list) -> str:
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return ''
    width = max(len(r) for r in rows)
    rows = [r + [''] * (width - len(r)) for r in rows]
    lines = ['| ' + ' | '.join(_cell(c) for c in rows[0]) + ' |',
             '|' + '---|' * width]
    lines += ['| ' + ' | '.join(_cell(c) for c in r) + ' |' for r in rows[1:]]
    return '\n'.join(lines)


def _list_md(items: list) -> str:
    out = []
    for it in items:
        marker = (it.get('marker') or '').strip()
        marker = marker if re.fullmatch(r'\d+[.)]|[a-zA-Z][.)]', marker) else '-'
        out.append(f'{marker} {it.get("text", "").strip()}')
    return '\n'.join(out)


def blocks_to_markdown(blocks: list, first_page: bool = True) -> str:
    """Bloques de docvision (ya en orden de lectura) → Markdown."""
    parts, title_used = [], not first_page
    for b in blocks:
        kind = b.get('type')
        if kind == 'title':
            text = b['text'].strip()
            parts.append(text if title_used else f'# {text}')
            title_used = True
        elif kind == 'paragraph':
            parts.append(b['text'].strip())
        elif kind == 'table':
            parts.append(_table_md(b.get('rows', [])))
        elif kind == 'list':
            parts.append(_list_md(b.get('items', [])))
    return '\n\n'.join(p for p in parts if p)


def tokens(text: str) -> list:
    return [t.lower() for t in _TOKEN.findall(text) if len(t) > 1 or t.isdigit()]


def recall(reference: str, candidate: str) -> float:
    """Fracción de los tokens de `reference` presentes en `candidate` (multiconjunto)."""
    ref = tokens(reference)
    if not ref:
        return 1.0
    pool = {}
    for t in tokens(candidate):
        pool[t] = pool.get(t, 0) + 1
    hit = 0
    for t in ref:
        if pool.get(t, 0) > 0:
            pool[t] -= 1
            hit += 1
    return hit / len(ref)


def choose(structured: str, plain: str) -> tuple:
    """Elige entre el Markdown estructurado y el texto plano de una página.
    Retorna (texto, estructurado_usado)."""
    if structured.strip() and recall(plain, structured) >= MIN_RECALL:
        return structured, True
    return plain, False


def warm_up() -> float:
    """Primera ejecución del binario tras compilarlo: Vision tarda ~30 s en preparar
    el modelo (después, ~0.2 s por página). Se hace una vez al instalar, no en el
    primer documento del usuario. Retorna los segundos que tardó."""
    import tempfile
    import time
    from PIL import Image, ImageDraw
    start = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        img = Image.new('RGB', (900, 400), 'white')
        ImageDraw.Draw(img).text((60, 60), 'Doc Converter 2026 - 1,234.50', fill='black')
        path = f'{tmp}/warm.png'
        img.save(path)
        analyze([path])
    return time.monotonic() - start
