"""
local_converter.py — Conversión 100% local (sin Gemini) para el CLI / Finder.

No se usa en Vercel: depende de requirements-local.txt (markitdown, pymupdf4llm,
ocrmac) y de `textutil` (macOS). Los imports pesados son diferidos.

- Office/HTML (.docx, .pptx, .html)  → markitdown
- .doc, .rtf, .odt                    → textutil (a HTML) → markitdown
- PDF                                 → híbrido por página: capa de texto con
                                        pymupdf4llm y OCR de Vision (framework
                                        nativo de macOS) solo donde hace falta
- Imágenes (.png, .jpg, .heic ...)    → OCR de Vision
- Planillas genéricas (.xlsx, .xls)   → un CSV fiel por hoja (los extractos
                                        bancarios siguen el pipeline financiero)

El OCR usa primero docvision (tablas, listas y títulos reconocidos por Vision en
macOS 26+, ver structured_ocr.py) y cae al OCR plano de ocrmac si no está
disponible o si pierde texto respecto del plano.

El OCR de Vision corre en la Mac, es del orden de 0.2 s por página y no consume
cuota de ninguna API.
"""

import io
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import fitz  # PyMuPDF

from backend.engines import structured_ocr

MARKITDOWN_EXTS = {'.docx', '.pptx', '.html', '.htm', '.epub'}
TEXTUTIL_EXTS = {'.doc', '.rtf', '.odt'}
OFFICE_EXTS = MARKITDOWN_EXTS | TEXTUTIL_EXTS
IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.heic', '.webp', '.bmp', '.gif'}
SPREADSHEET_EXTS = {'.xls', '.xlsx', '.xlsm', '.csv'}

# Una página se manda a OCR si su capa de texto no llega a estos caracteres.
_MIN_CHARS_PER_PAGE = 120
_OCR_DPI = 200
# Páginas renderizadas a la vez: acota la memoria en escaneos de cientos de páginas.
_OCR_CHUNK_PAGES = 8
_OCR_LANGUAGES = ['es-ES', 'en-US']
_MAX_OCR_WORKERS = 8
# Dos fragmentos de OCR se unen en una línea si sus centros verticales
# (coordenadas normalizadas 0-1) difieren menos que _LINE_TOLERANCE Y además
# están pegados horizontalmente. Sin la segunda condición, en formularios y
# diapositivas a dos columnas se mezcla el texto de columnas distintas.
_LINE_TOLERANCE = 0.012
_COLUMN_GAP = 0.04
# Recorte de fotos: si el documento ocupa más de esta fracción no se toca;
# por debajo de esta confianza solo se sugiere el recorte.
_DOC_FULL_FRAME = 0.9
_DOC_MIN_CONFIDENCE = 0.8

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
    img = Image.open(io.BytesIO(page.get_pixmap(dpi=_OCR_DPI).tobytes('png')))
    img.load()  # carga perezosa: sin esto, dos hilos la decodifican a la vez y PIL falla
    return img


def ocr_images(images: list) -> list:
    """OCR de varias imágenes PIL. Retorna [(texto, estructurado: bool)].

    Intenta docvision (tablas/listas/títulos) y lo contrasta contra el OCR plano
    de la misma imagen; si pierde contenido o no está disponible, usa el plano."""
    if not images:
        return []
    with ThreadPoolExecutor(max_workers=min(_MAX_OCR_WORKERS, len(images))) as pool:
        plain_jobs = [pool.submit(_ocr_image, im) for im in images]
        blocks = [None] * len(images)
        if structured_ocr.available():
            with tempfile.TemporaryDirectory() as tmp:
                paths = []
                for i, im in enumerate(images):
                    path = os.path.join(tmp, f'{i:04d}.png')
                    im.save(path, format='PNG')
                    paths.append(path)
                try:
                    blocks = structured_ocr.analyze(paths)
                except RuntimeError:
                    pass  # sin estructura: queda el OCR plano
        plains = [j.result() for j in plain_jobs]

    out = []
    for plain_text, page_blocks in zip(plains, blocks):
        if page_blocks is None:
            out.append((plain_text, False))
        else:
            out.append(structured_ocr.choose(structured_ocr.blocks_to_markdown(page_blocks), plain_text))
    return out


def detect_document(image):
    """Bordes del documento dentro de una foto, con la segmentación de
    documentos de Vision. Retorna (esquinas en píxeles [sup-izq, inf-izq,
    inf-der, sup-der], confianza, fracción del área) o None si no ve uno."""
    import Vision
    from Foundation import NSData
    buf = io.BytesIO()
    image.save(buf, format='PNG')
    data = buf.getvalue()
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(
        NSData.dataWithBytes_length_(data, len(data)), None)
    request = Vision.VNDetectDocumentSegmentationRequest.alloc().init()
    handler.performRequests_error_([request], None)
    results = request.results() or []
    if not results:
        return None
    obs = results[0]
    w, h = image.size
    # Vision: coordenadas 0-1 con origen abajo-izquierda.
    to_px = lambda p: (p.x * w, (1 - p.y) * h)
    quad = [to_px(obs.topLeft()), to_px(obs.bottomLeft()), to_px(obs.bottomRight()), to_px(obs.topRight())]
    xs, ys = [p[0] for p in quad], [p[1] for p in quad]
    area = (max(xs) - min(xs)) * (max(ys) - min(ys)) / (w * h)
    return quad, float(obs.confidence()), area


def crop_to_document(image) -> tuple:
    """Recorta y endereza el documento si la foto abarca más que él.
    Retorna (imagen, nota); la nota es '' si no hubo nada que decir."""
    from PIL import Image
    found = detect_document(image)
    if not found:
        return image, ''
    quad, confidence, area = found
    if area > _DOC_FULL_FRAME:
        return image, ''  # el documento ya ocupa la foto
    if confidence < _DOC_MIN_CONFIDENCE:
        return image, (f'posible documento en el {area:.0%} de la foto (confianza {confidence:.0%}); '
                       'no se recortó, conviene recortarla a mano')
    (tl, bl, br, tr) = quad
    dist = lambda a, b: ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
    size = (int(max(dist(tl, tr), dist(bl, br))), int(max(dist(tl, bl), dist(tr, br))))
    flat = image.transform(size, Image.QUAD, data=[c for p in quad for c in p], resample=Image.BICUBIC)
    return flat, f'documento recortado al {area:.0%} de la foto y enderezado'


def _open_image(path: str):
    """Abre una imagen con PIL; los formatos que PIL no lee (HEIC) pasan por sips."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    try:
        img = Image.open(path)
        img.load()
    except (UnidentifiedImageError, OSError):
        with tempfile.TemporaryDirectory() as tmp:
            converted = os.path.join(tmp, 'img.png')
            proc = subprocess.run(['/usr/bin/sips', '-s', 'format', 'png', path, '--out', converted],
                                  capture_output=True, timeout=120)
            if proc.returncode != 0 or not os.path.exists(converted):
                raise ConversionError('no se pudo leer la imagen (archivo dañado o formato no soportado)')
            img = Image.open(converted)
            img.load()
    return ImageOps.exif_transpose(img).convert('RGB')


def convert_image(path: str) -> tuple:
    """Retorna (texto, nota de recorte)."""
    img = _open_image(path)
    img, note = crop_to_document(img)
    text, structured = ocr_images([img])[0]
    return text, note


# --- PDF --------------------------------------------------------------------

class ConversionError(RuntimeError):
    """Error esperable de un archivo (cifrado, dañado, vacío) con mensaje para el usuario."""


@dataclass
class PdfResult:
    markdown: str
    pages: int = 0
    ocr_pages: int = 0
    structured_pages: int = 0
    warnings: list = field(default_factory=list)


def open_pdf(file_bytes: bytes):
    """Abre un PDF con errores claros en vez de los de MuPDF."""
    if not file_bytes:
        raise ConversionError('el archivo está vacío (0 bytes)')
    try:
        doc = fitz.open(stream=file_bytes, filetype='pdf')
    except Exception as e:
        raise ConversionError(f'PDF dañado o ilegible ({e})') from e
    if doc.needs_pass or doc.is_encrypted:
        doc.close()
        raise ConversionError('PDF protegido con contraseña; quítale la protección y vuelve a intentar')
    return doc


def pdf_first_page_text(file_bytes: bytes) -> str:
    with open_pdf(file_bytes) as doc:
        return doc[0].get_text().lower() if len(doc) else ''


def looks_like_bank_statement(first_page_text: str) -> bool:
    return any(k in first_page_text for k in _BANK_KEYWORDS)


def convert_pdf_ex(file_bytes: bytes) -> PdfResult:
    """Convierte un PDF a Markdown página por página: usa la capa de texto
    donde existe y OCR de Vision donde no (en lotes, para acotar la memoria)."""
    import pymupdf4llm

    with open_pdf(file_bytes) as doc:
        total = len(doc)
        if total == 0:
            raise ConversionError('el PDF no tiene páginas')
        needs_ocr = [i for i in range(total) if len(doc[i].get_text().strip()) < _MIN_CHARS_PER_PAGE]
        ocr_set = set(needs_ocr)
        text_pages = [i for i in range(total) if i not in ocr_set]
        result = PdfResult('', pages=total, ocr_pages=len(needs_ocr))

        pages_md = {}
        if text_pages:
            for chunk in pymupdf4llm.to_markdown(
                doc, pages=text_pages, page_chunks=True, show_progress=False,
            ):
                pages_md[chunk['metadata']['page'] - 1] = chunk['text'].strip()

        for start in range(0, len(needs_ocr), _OCR_CHUNK_PAGES):
            batch = needs_ocr[start:start + _OCR_CHUNK_PAGES]
            for i, (text, structured) in zip(batch, ocr_images([_page_image(doc[i]) for i in batch])):
                pages_md[i] = text.strip()
                result.structured_pages += structured

    blank = [i + 1 for i in range(total) if not pages_md.get(i)]
    if blank:
        shown = ', '.join(map(str, blank[:10])) + ('…' if len(blank) > 10 else '')
        result.warnings.append(f'{len(blank)} página(s) sin texto reconocible: {shown}')
    result.markdown = '\n\n'.join(pages_md[i] for i in range(total) if pages_md.get(i))
    return result


def convert_pdf(file_bytes: bytes) -> tuple:
    """Compatibilidad: retorna (markdown, páginas_ocr)."""
    r = convert_pdf_ex(file_bytes)
    return r.markdown, r.ocr_pages


# --- Planillas --------------------------------------------------------------

_FINANCE_HEADER_WORDS = ('monto', 'cargo', 'abono', 'importe', 'retiro', 'debito', 'débito', 'credito', 'crédito')


def _read_sheets(path: str) -> dict:
    """{nombre de hoja: DataFrame sin cabecera}. Un CSV es una sola hoja."""
    import pandas as pd
    ext = os.path.splitext(path)[1].lower()
    if ext == '.csv':
        for enc in ('utf-8-sig', 'latin-1'):
            try:
                return {'': pd.read_csv(path, header=None, dtype=str, encoding=enc, sep=None, engine='python')}
            except UnicodeDecodeError:
                continue
        raise ConversionError('no se pudo leer el CSV (codificación desconocida)')
    engine = 'xlrd' if ext == '.xls' else 'openpyxl'
    return pd.read_excel(path, sheet_name=None, header=None, dtype=object, engine=engine)


def looks_like_transactions(df) -> bool:
    """¿Alguna de las primeras 30 filas es una cabecera de movimientos (fecha + monto)?"""
    for i in range(min(30, len(df))):
        row = ' '.join(str(v).lower() for v in df.iloc[i].tolist())
        if 'fecha' in row and any(w in row for w in _FINANCE_HEADER_WORDS):
            return True
    return False


def spreadsheet_kind(path: str) -> str:
    """'finance' si la primera hoja con datos parece un extracto de movimientos."""
    try:
        sheets = _read_sheets(path)
    except Exception as e:
        raise ConversionError(f'no se pudo leer la planilla ({e})') from e
    return 'finance' if any(looks_like_transactions(df) for df in sheets.values()) else 'generic'


def convert_spreadsheet_generic(path: str) -> list:
    """Planilla que no es un extracto: un CSV fiel por hoja, sin interpretar columnas.
    Retorna [(nombre_sugerido, bytes)]."""
    try:
        sheets = _read_sheets(path)
    except Exception as e:
        raise ConversionError(f'no se pudo leer la planilla ({e})') from e
    stem = os.path.splitext(os.path.basename(path))[0]
    outputs = []
    for name, df in sheets.items():
        df = df.dropna(how='all').dropna(axis=1, how='all')
        if df.empty:
            continue
        label = f'{stem}.csv' if len(sheets) == 1 and not name else f'{stem} - {re.sub(r"[\\/:*?]", "_", str(name))}.csv'
        outputs.append((label, df.to_csv(index=False, header=False).encode('utf-8')))
    if not outputs:
        raise ConversionError('la planilla no tiene datos')
    return outputs
