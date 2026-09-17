"""
local_converter.py — Conversión 100% local (sin Gemini) para el CLI / Finder.

No se usa en Vercel: depende de requirements-local.txt (markitdown, pymupdf4llm,
ocrmac) y de `textutil` (macOS). Los imports pesados son diferidos.

- Office/HTML (.docx, .pptx, .html)  → markitdown
- .doc, .rtf, .odt                    → textutil (a HTML) → markitdown
- PDF                                 → híbrido por página: capa de texto con
                                        pymupdf4llm y OCR de Vision (framework
                                        nativo de macOS) solo donde hace falta
- Imágenes (.png, .jpg)               → OCR de Vision

El OCR de Vision corre en la Mac, es del orden de 0.2 s por página y no consume
cuota de ninguna API.
"""

import io
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor

import fitz  # PyMuPDF

MARKITDOWN_EXTS = {'.docx', '.pptx', '.html', '.htm'}
TEXTUTIL_EXTS = {'.doc', '.rtf', '.odt'}
OFFICE_EXTS = MARKITDOWN_EXTS | TEXTUTIL_EXTS
IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.tiff', '.heic'}

# Una página se manda a OCR si su capa de texto no llega a estos caracteres.
_MIN_CHARS_PER_PAGE = 120
_OCR_DPI = 150
_OCR_LANGUAGES = ['es-ES', 'en-US']
_MAX_OCR_WORKERS = 8
# Dos fragmentos de OCR se unen en una línea si sus centros verticales
# (coordenadas normalizadas 0-1) difieren menos que _LINE_TOLERANCE Y además
# están pegados horizontalmente. Sin la segunda condición, en formularios y
# diapositivas a dos columnas se mezcla el texto de columnas distintas.
_LINE_TOLERANCE = 0.012
_COLUMN_GAP = 0.04

_BANK_KEYWORDS = (
    'extracto bancario', 'extracto de cuenta', 'estado de cuenta',
    'saldo anterior', 'saldo inicial', 'saldo final', 'número de cuenta', 'nro. de cuenta',
)


# --- Office -----------------------------------------------------------------

def _markitdown(path: str) -> str:
    from markitdown import MarkItDown
    return MarkItDown().convert(path).text_content


def convert_office(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in MARKITDOWN_EXTS:
        return _markitdown(path)
    with tempfile.TemporaryDirectory() as tmp:
        html_path = os.path.join(tmp, 'doc.html')
        subprocess.run(
            ['/usr/bin/textutil', '-convert', 'html', '-output', html_path, path],
            check=True, capture_output=True, timeout=120,
        )
        return _markitdown(html_path)


# --- OCR local (Vision) -----------------------------------------------------

def _ocr_image(image) -> str:
    """OCR de una imagen PIL con Vision. Reconstruye líneas a partir de las
    cajas que devuelve Vision (origen abajo-izquierda, coordenadas 0-1)."""
    from ocrmac import ocrmac
    fragments = ocrmac.OCR(
        image, language_preference=_OCR_LANGUAGES, recognition_level='accurate',
    ).recognize()

    # Se respeta el orden de lectura que ya resuelve Vision: reordenar por
    # geometría empeora los formularios y las diapositivas a varias columnas.
    lines, current, current_y, current_right = [], [], None, None
    for text, _conf, (x, y, w, h) in fragments:
        center_y = y + h / 2
        same_line = (
            current_y is not None
            and abs(center_y - current_y) <= _LINE_TOLERANCE
            and x - current_right <= _COLUMN_GAP
        )
        if same_line:
            current.append(text)
        else:
            if current:
                lines.append(' '.join(current))
            current, current_y = [text], center_y
        current_right = x + w
    if current:
        lines.append(' '.join(current))
    return '\n'.join(lines)


def _page_image(page):
    from PIL import Image
    return Image.open(io.BytesIO(page.get_pixmap(dpi=_OCR_DPI).tobytes('png')))


def convert_image(path: str) -> str:
    from PIL import Image
    with Image.open(path) as img:
        return _ocr_image(img.convert('RGB'))


# --- PDF --------------------------------------------------------------------

def pdf_first_page_text(file_bytes: bytes) -> str:
    with fitz.open(stream=file_bytes, filetype='pdf') as doc:
        return doc[0].get_text().lower() if len(doc) else ''


def looks_like_bank_statement(first_page_text: str) -> bool:
    return any(k in first_page_text for k in _BANK_KEYWORDS)


def convert_pdf(file_bytes: bytes) -> tuple:
    """Convierte un PDF a Markdown página por página: usa la capa de texto
    donde existe y OCR de Vision donde no. Retorna (markdown, páginas_ocr)."""
    import pymupdf4llm

    with fitz.open(stream=file_bytes, filetype='pdf') as doc:
        total = len(doc)
        if total == 0:
            return '', 0
        needs_ocr = [i for i in range(total) if len(doc[i].get_text().strip()) < _MIN_CHARS_PER_PAGE]
        text_pages = [i for i in range(total) if i not in set(needs_ocr)]

        pages_md = {}
        if text_pages:
            for chunk in pymupdf4llm.to_markdown(
                doc, pages=text_pages, page_chunks=True, show_progress=False,
            ):
                pages_md[chunk['metadata']['page'] - 1] = chunk['text'].strip()

        if needs_ocr:
            images = [_page_image(doc[i]) for i in needs_ocr]
            with ThreadPoolExecutor(max_workers=min(_MAX_OCR_WORKERS, len(images))) as pool:
                for i, text in zip(needs_ocr, pool.map(_ocr_image, images)):
                    pages_md[i] = text.strip()

    markdown = '\n\n'.join(pages_md[i] for i in range(total) if pages_md.get(i))
    return markdown, len(needs_ocr)
