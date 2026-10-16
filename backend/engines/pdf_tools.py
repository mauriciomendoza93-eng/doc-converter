"""
pdf_tools.py — Operaciones sobre PDF en memoria (pikepdf + PyMuPDF) y Office→PDF.

Cada función recibe bytes y devuelve bytes nuevos: nunca toca el original.
"""

import io
import os
import shutil
import subprocess
import tempfile

import fitz  # PyMuPDF
import pikepdf


class ToolUnavailable(RuntimeError):
    """Dependencia externa (LibreOffice, ocrmypdf) no instalada."""


class ToolInputError(ValueError):
    """Parámetros inválidos para la operación."""


def _open(data: bytes) -> pikepdf.Pdf:
    try:
        return pikepdf.open(io.BytesIO(data))
    except pikepdf.PasswordError:
        raise ToolInputError('El PDF está cifrado con contraseña; desbloquéalo antes de editarlo.')
    except pikepdf.PdfError as e:
        raise ToolInputError(f'PDF dañado o ilegible: {e}')


def _save(pdf: pikepdf.Pdf, **kw) -> bytes:
    out = io.BytesIO()
    pdf.save(out, **kw)
    return out.getvalue()


def parse_pages(spec: str, total: int) -> list[int]:
    """'1-3,5' → [0,1,2,4] (índices base 0). Valida rango."""
    pages: list[int] = []
    for part in (spec or '').replace(' ', '').split(','):
        if not part:
            continue
        try:
            if '-' in part:
                a, b = part.split('-', 1)
                a, b = int(a), int(b or total)
                if a > b:
                    raise ToolInputError(f'Rango invertido: {part}')
                pages.extend(range(a - 1, b))
            else:
                pages.append(int(part) - 1)
        except ValueError:
            raise ToolInputError(f'Rango de páginas inválido: {part}')
    if not pages:
        raise ToolInputError('Indica al menos una página.')
    bad = [p + 1 for p in pages if p < 0 or p >= total]
    if bad:
        raise ToolInputError(f'Páginas fuera de rango (el PDF tiene {total}): {bad}')
    return pages


def inspect(data: bytes) -> dict:
    """Detecta lo que una edición puede alterar: cifrado, formularios, firmas, fuentes."""
    info = {'pages': 0, 'encrypted': False, 'forms': False, 'signatures': False,
            'unembedded_fonts': [], 'warnings': []}
    try:
        pdf = pikepdf.open(io.BytesIO(data))
    except pikepdf.PasswordError:
        info['encrypted'] = True
        info['warnings'].append('PDF cifrado: hay que quitar la contraseña antes de editar.')
        return info
    except pikepdf.PdfError as e:
        raise ToolInputError(f'PDF dañado o ilegible: {e}')
    with pdf:
        info['pages'] = len(pdf.pages)
        info['encrypted'] = pdf.is_encrypted
        acro = pdf.Root.get('/AcroForm')
        fields = acro.get('/Fields', []) if acro is not None else []
        info['forms'] = len(fields) > 0
        info['signatures'] = any(f.get('/FT') == '/Sig' for f in fields)
        fonts = set()
        for page in pdf.pages:
            res = page.obj.get('/Resources') or {}
            for _, font in (res.get('/Font') or {}).items():
                desc = font.get('/FontDescriptor')
                embedded = desc is not None and any(k in desc for k in ('/FontFile', '/FontFile2', '/FontFile3'))
                if not embedded and font.get('/Subtype') != '/Type3':
                    fonts.add(str(font.get('/BaseFont', '?')).lstrip('/'))
        info['unembedded_fonts'] = sorted(fonts)
    if info['encrypted']:
        info['warnings'].append('PDF cifrado: el resultado se guardará sin cifrar.')
    if info['signatures']:
        info['warnings'].append('Tiene firmas digitales: cualquier operación las invalida.')
    if info['forms']:
        info['warnings'].append('Tiene campos de formulario: unir, dividir o comprimir puede perderlos.')
    if info['unembedded_fonts']:
        info['warnings'].append('Fuentes no incrustadas: el texto puede verse distinto en otro equipo.')
    return info


def preview(data: bytes, page: int = 1, width: int = 600) -> bytes:
    """PNG de una página (base 1)."""
    with fitz.open(stream=data, filetype='pdf') as doc:
        if not 1 <= page <= doc.page_count:
            raise ToolInputError(f'Página {page} fuera de rango (1-{doc.page_count}).')
        p = doc[page - 1]
        zoom = width / p.rect.width
        return p.get_pixmap(matrix=fitz.Matrix(zoom, zoom)).tobytes('png')


def merge(files: list[bytes]) -> bytes:
    if len(files) < 2:
        raise ToolInputError('Sube al menos dos PDF para unir.')
    out = pikepdf.new()
    for data in files:
        with _open(data) as src:
            out.pages.extend(src.pages)
    return _save(out)


def split(data: bytes, ranges: str) -> list[bytes]:
    """'1-2;3-5' → un PDF por grupo separado por ';'."""
    groups = [g for g in (ranges or '').split(';') if g.strip()]
    if not groups:
        raise ToolInputError('Indica los grupos, por ejemplo: 1-2;3-5')
    with _open(data) as src:
        parts = []
        for g in groups:
            out = pikepdf.new()
            for i in parse_pages(g, len(src.pages)):
                out.pages.append(src.pages[i])
            parts.append(_save(out))
    return parts


def reorder(data: bytes, order: str) -> bytes:
    """'3,1,2' → nuevo orden; las páginas omitidas se descartan."""
    with _open(data) as src:
        out = pikepdf.new()
        for i in parse_pages(order, len(src.pages)):
            out.pages.append(src.pages[i])
        return _save(out)


def rotate(data: bytes, degrees: int, pages: str = '') -> bytes:
    if degrees % 90:
        raise ToolInputError('La rotación debe ser múltiplo de 90.')
    with _open(data) as pdf:
        targets = parse_pages(pages, len(pdf.pages)) if pages else range(len(pdf.pages))
        for i in targets:
            pdf.pages[i].rotate(degrees, relative=True)
        return _save(pdf)


def compress(data: bytes) -> bytes:
    """Recomprime flujos e imágenes; devuelve el original si no mejora."""
    with fitz.open(stream=data, filetype='pdf') as doc:
        if doc.needs_pass:
            raise ToolInputError('El PDF está cifrado con contraseña.')
        for page in doc:
            for img in page.get_images(full=True):
                try:
                    pix = fitz.Pixmap(doc, img[0])
                    if pix.width * pix.height < 200_000 or pix.alpha:
                        continue
                    if pix.n > 3:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    page.replace_image(img[0], stream=pix.tobytes('jpeg', jpg_quality=70))
                except Exception:
                    continue  # imagen con formato raro: se deja tal cual
        out = doc.tobytes(garbage=4, deflate=True, deflate_images=True, clean=True)
    with _open(out) as pdf:
        out = _save(pdf, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)
    return out if len(out) < len(data) else data


def watermark(data: bytes, text: str, opacity: float = 0.15) -> bytes:
    text = (text or '').strip()
    if not text:
        raise ToolInputError('Escribe el texto de la marca de agua.')
    with fitz.open(stream=data, filetype='pdf') as doc:
        for page in doc:
            r = page.rect
            size = min(72.0, r.width * 0.8 / max(fitz.get_text_length(text, fontsize=1), 1))
            x = (r.width - fitz.get_text_length(text, fontsize=size)) / 2
            page.insert_text((x, r.height / 2 + size / 3), text, fontsize=size,
                             color=(0.5, 0.5, 0.5), fill_opacity=opacity, overlay=True)
        return doc.tobytes(garbage=3, deflate=True)


def redact(data: bytes, terms: list[str]) -> tuple[bytes, int]:
    """Elimina de verdad (no solo tapa) cada aparición de los términos. Devuelve (pdf, hits)."""
    terms = [t.strip() for t in terms if t.strip()]
    if not terms:
        raise ToolInputError('Indica al menos un texto a tachar.')
    hits = 0
    with fitz.open(stream=data, filetype='pdf') as doc:
        for page in doc:
            for t in terms:
                for rect in page.search_for(t):
                    page.add_redact_annot(rect, fill=(0, 0, 0))
                    hits += 1
            page.apply_redactions()
        return doc.tobytes(garbage=4, deflate=True), hits


def ocr(data: bytes, lang: str = 'spa+eng') -> bytes:
    """Capa de texto buscable con ocrmypdf (Tesseract)."""
    if not shutil.which('ocrmypdf'):
        raise ToolUnavailable('OCR no disponible: falta ocrmypdf/Tesseract en este servidor.')
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = os.path.join(tmp, 'in.pdf'), os.path.join(tmp, 'out.pdf')
        with open(src, 'wb') as f:
            f.write(data)
        r = subprocess.run(['ocrmypdf', '--skip-text', '-l', lang, '--output-type', 'pdf', src, dst],
                           capture_output=True, text=True, timeout=600)
        if r.returncode not in (0, 6):  # 6 = ya tenía texto
            raise ToolInputError(f'OCR falló: {r.stderr.strip()[-300:]}')
        with open(dst if os.path.exists(dst) else src, 'rb') as f:
            return f.read()


def _soffice() -> str | None:
    return shutil.which('soffice') or shutil.which('libreoffice') or next(
        (p for p in ('/Applications/LibreOffice.app/Contents/MacOS/soffice',) if os.path.exists(p)), None)


def office_to_pdf(data: bytes, filename: str) -> bytes:
    """Convierte .docx/.xlsx/.pptx/.odt… a PDF con LibreOffice headless."""
    exe = _soffice()
    if not exe:
        raise ToolUnavailable('Office→PDF no disponible: falta LibreOffice en este servidor.')
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, 'entrada' + os.path.splitext(filename)[1].lower())
        with open(src, 'wb') as f:
            f.write(data)
        # Perfil aislado: evita bloqueos si hay otra instancia de LibreOffice abierta.
        cmd = [exe, f'-env:UserInstallation=file://{tmp}/profile', '--headless',
               '--convert-to', 'pdf', '--outdir', tmp, src]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            raise ToolInputError('LibreOffice tardó demasiado en convertir el archivo.')
        out = os.path.join(tmp, 'entrada.pdf')
        if r.returncode != 0 or not os.path.exists(out):
            raise ToolInputError(f'LibreOffice no pudo convertir el archivo: {r.stderr.strip()[-300:]}')
        with open(out, 'rb') as f:
            return f.read()
