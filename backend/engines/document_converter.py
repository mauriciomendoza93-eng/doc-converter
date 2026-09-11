"""
document_converter.py — Motor de conversión genérica a Markdown (stateless).

Recibe bytes en memoria, retorna string Markdown generado.
Usa un proveedor de visión (Strategy/Factory) para PDFs e imágenes.
"""

import io
import json
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import fitz  # PyMuPDF
from pypdf import PdfReader, PdfWriter

from backend.utils.validators import validate_markdown
from backend.engines.vision_provider import get_vision_provider

# Dos límites del stack 100% gratuito compiten entre sí y hay que balancearlos:
# 1. Vercel Hobby: la función serverless tiene maxDuration=60s. Un PDF
#    escaneado a alta resolución puede tardar 2-4 min en transcribirse en una
#    sola llamada — no tanto por cantidad de páginas sino por el peso de las
#    imágenes incrustadas (un testimonio notarial de 10 páginas puede pesar 5MB+).
# 2. Gemini free tier: cuota de solo 20 requests/día para el modelo. Dividir
#    un documento en lotes (chunking) consume una solicitud POR LOTE, así que
#    hacerlo agresivamente agota la cuota diaria mucho más rápido.
#
# Mitigación, en orden de prioridad (la que no cuesta cuota primero):
# 1. Recomprimir el PDF (re-renderizar cada página a menor DPI/calidad JPEG)
#    antes de enviarlo — reduce el payload ~2-3x sin perder legibilidad OCR,
#    sin gastar ninguna solicitud extra.
# 2. Solo si el documento sigue siendo muy extenso (muchas páginas) después de
#    comprimir, dividirlo en lotes grandes (pocos lotes) y transcribirlos en
#    paralelo. El umbral es alto a propósito para que la gran mayoría de
#    documentos (como testimonios notariales de pocas páginas) se procesen en
#    UNA sola llamada.
_LARGE_DOC_PAGE_THRESHOLD = 20
_PAGE_CHUNK_SIZE = 10
_DOWNSAMPLE_DPI = 120
_DOWNSAMPLE_JPEG_QUALITY = 70
_DOWNSAMPLE_MIN_BYTES = 1_500_000  # no vale la pena recomprimir PDFs ya livianos


def _downsample_pdf(file_bytes: bytes) -> bytes:
    """Re-renderiza cada página como imagen JPEG comprimida para reducir el
    peso del PDF antes de enviarlo a Gemini (documentos escaneados a alta
    resolución son el caso típico que agota el tiempo de la función serverless).

    Best-effort: si algo falla (PDF con estructura inusual, etc.) devuelve los
    bytes originales sin modificar — nunca debe romper la conversión.
    """
    try:
        src = fitz.open(stream=file_bytes, filetype='pdf')
        out = fitz.open()
        for page in src:
            pix = page.get_pixmap(dpi=_DOWNSAMPLE_DPI)
            jpeg_bytes = pix.tobytes('jpeg', jpg_quality=_DOWNSAMPLE_JPEG_QUALITY)
            new_page = out.new_page(width=page.rect.width, height=page.rect.height)
            new_page.insert_image(page.rect, stream=jpeg_bytes)
        result = out.tobytes(garbage=4, deflate=True)
        return result if len(result) < len(file_bytes) else file_bytes
    except Exception:
        return file_bytes

_PROMPT = (
    'Eres un transcriptor experto de documentos legales y testimonios notariales. '
    'Transcribe este documento a formato Markdown preservando toda la jerarquía, '
    'sin omitir ni resumir absolutamente nada. Lee la letra pequeña. '
    'Ignora sellos que tapen el texto, pero extrae todo el texto legible. '
    'No agregues saludos ni explicaciones, devuelve SOLO el Markdown crudo.'
)

_SMART_PROMPT = (
    'Eres un transcriptor experto de documentos. Analiza este documento y responde '
    'ÚNICAMENTE con un objeto JSON (sin markdown, sin explicaciones) con esta forma exacta:\n'
    '{\n'
    '  "es_financiero": boolean,\n'
    '  "banco": string o null (nombre del banco si el documento lo menciona, ej. "BCP", "BNB", "Banco Unión"),\n'
    '  "markdown": string,\n'
    '  "transacciones": [{"fecha": "YYYY-MM-DD", "descripcion": string, "monto": number}] o null\n'
    '}\n\n'
    'Marca "es_financiero": true SOLO si el documento es un extracto de cuenta, estado de '
    'movimientos, o listado de transacciones bancarias.\n\n'
    'Si es_financiero es true:\n'
    '- "transacciones" debe incluir TODAS las transacciones del documento, sin omitir ninguna.\n'
    '- "monto" lleva signo: negativo para egresos/cargos/débitos, positivo para ingresos/abonos/créditos.\n'
    '- "markdown" puede ser un resumen breve (no hace falta transcribir la tabla completa ahí).\n\n'
    'Si es_financiero es false:\n'
    '- "transacciones" y "banco" van null.\n'
    '- "markdown" debe ser la transcripción COMPLETA del documento preservando toda la '
    'jerarquía, sin omitir ni resumir nada. Lee la letra pequeña. Ignora sellos que tapen '
    'el texto, pero extrae todo el texto legible.'
)


def _strip_json_fences(text: str) -> str:
    return re.sub(r'^```(json)?|```$', '', text.strip(), flags=re.MULTILINE).strip()


def _slugify(text: str, max_len: int = 60) -> str:
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
    text = re.sub(r'[^a-zA-Z0-9\s-]', '', text).strip().lower()
    text = re.sub(r'[\s_-]+', '-', text)
    return text[:max_len].strip('-')


def _generate_filename(markdown: str, original_name: str) -> str:
    """
    Genera un nombre identificativo a partir del contenido transcrito: usa el
    primer heading (título/tipo de documento) y, si aparece, una fecha en
    formato DD/MM/YYYY (u otro separador) encontrada en el propio texto.
    Si no se puede identificar nada útil, conserva el nombre original.
    """
    heading_match = re.search(r'^#{1,3}\s+(.+)$', markdown, re.MULTILINE)
    if not heading_match:
        return original_name.rsplit('.', 1)[0] + '.md'

    slug = _slugify(heading_match.group(1))
    if not slug:
        return original_name.rsplit('.', 1)[0] + '.md'

    fecha_match = re.search(r'\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\b', markdown)
    if fecha_match:
        dia, mes, anio = fecha_match.groups()
        slug = f"{slug}-{anio}-{mes.zfill(2)}-{dia.zfill(2)}"

    return f"{slug}.md"


def _count_pdf_pages(file_bytes: bytes) -> int:
    return len(PdfReader(io.BytesIO(file_bytes)).pages)


def _split_pdf_into_chunks(file_bytes: bytes, chunk_size: int) -> list:
    reader = PdfReader(io.BytesIO(file_bytes))
    chunks = []
    for start in range(0, len(reader.pages), chunk_size):
        writer = PdfWriter()
        for page in reader.pages[start:start + chunk_size]:
            writer.add_page(page)
        buf = io.BytesIO()
        writer.write(buf)
        chunks.append(buf.getvalue())
    return chunks


def _prepare_pdf(file_bytes: bytes) -> bytes:
    """Recomprime el PDF si supera el umbral de peso (ver _downsample_pdf)."""
    if len(file_bytes) > _DOWNSAMPLE_MIN_BYTES:
        return _downsample_pdf(file_bytes)
    return file_bytes


def _transcribe_chunks(chunks: list, prompt: str, response_mime_type: str = None) -> list:
    """Transcribe cada lote de páginas en paralelo (hilos, I/O-bound: llamadas HTTP a Gemini).
    Preserva el orden de las páginas (los resultados respetan el orden de envío)."""
    provider = get_vision_provider("gemini")
    with ThreadPoolExecutor(max_workers=len(chunks)) as executor:
        futures = [
            executor.submit(provider.process_document, chunk, 'application/pdf', prompt, response_mime_type)
            for chunk in chunks
        ]
        return [f.result() for f in futures]


def convert_to_markdown(file_bytes: bytes, original_name: str) -> dict:
    """
    Stateless: recibe bytes y nombre, retorna dict con Markdown y metadata.

    Envía el archivo crudo al proveedor de visión (Gemini 2.5 Flash),
    que acepta PDFs e imágenes nativamente.
    """
    ext = '.' + original_name.rsplit('.', 1)[-1].lower() if '.' in original_name else ''

    if ext == '.pdf':
        mime_type = 'application/pdf'
    else:
        mime_type = 'image/jpeg'

    try:
        if ext == '.pdf':
            file_bytes = _prepare_pdf(file_bytes)

        if ext == '.pdf' and _count_pdf_pages(file_bytes) > _LARGE_DOC_PAGE_THRESHOLD:
            chunks = _split_pdf_into_chunks(file_bytes, _PAGE_CHUNK_SIZE)
            md_parts = _transcribe_chunks(chunks, _PROMPT)
            md = '\n\n'.join(md_parts)
            method = 'gemini-vision-chunked'
        else:
            provider = get_vision_provider("gemini")
            md = provider.process_document(file_bytes, mime_type, _PROMPT)
            method = 'gemini-vision'
        validation = validate_markdown(md)
        filename = _generate_filename(md, original_name)
        return {'markdown': md, 'filename': filename, 'method': method, 'success': True, 'validation': validation}
    except Exception as e:
        return {'markdown': '', 'filename': '', 'method': 'gemini-vision', 'success': False, 'error': str(e)}


def convert_document_smart(file_bytes: bytes, original_name: str, force_finance: bool = False) -> dict:
    """
    Clasifica y extrae en una sola llamada a Gemini: si el documento es un
    extracto bancario, delega al motor financiero para categorizar y producir
    un CSV (mismo pipeline que Excel); si no, retorna Markdown como siempre.

    `force_finance=True` (route='finance' explícito): si el documento no trae
    transacciones extraíbles, retorna error explícito en vez de degradar a
    Markdown silenciosamente.

    Ante cualquier fallo de clasificación/parseo (JSON inválido, error de red,
    etc.) cae a `convert_to_markdown()` como red de seguridad — nunca rompe lo
    que ya funcionaba.

    Retorna: { 'success': bool, 'type': 'finance'|'markdown', ...campos de
    convert_to_finance_csv_from_transactions o de convert_to_markdown, 'error'?: str }
    """
    ext = '.' + original_name.rsplit('.', 1)[-1].lower() if '.' in original_name else ''
    mime_type = 'application/pdf' if ext == '.pdf' else 'image/jpeg'

    try:
        if ext == '.pdf':
            file_bytes = _prepare_pdf(file_bytes)

        if ext == '.pdf' and _count_pdf_pages(file_bytes) > _LARGE_DOC_PAGE_THRESHOLD:
            # Documento grande: clasificar y extraer por lotes de páginas en
            # paralelo (mismo motivo que en convert_to_markdown — respetar el
            # maxDuration de la función serverless). Cada lote se clasifica de
            # forma independiente y los resultados se combinan.
            chunks = _split_pdf_into_chunks(file_bytes, _PAGE_CHUNK_SIZE)
            raw_parts = _transcribe_chunks(chunks, _SMART_PROMPT, response_mime_type='application/json')
            parsed_parts = [json.loads(_strip_json_fences(r)) for r in raw_parts]

            es_financiero = any(bool(p.get('es_financiero')) for p in parsed_parts)
            transacciones = [t for p in parsed_parts for t in (p.get('transacciones') or [])]
            banco = next((p.get('banco') for p in parsed_parts if p.get('banco')), None)
            data = {
                'es_financiero': es_financiero,
                'banco': banco,
                'transacciones': transacciones,
                'markdown': '\n\n'.join(p.get('markdown') or '' for p in parsed_parts),
            }
        else:
            provider = get_vision_provider("gemini")
            raw = provider.process_document(file_bytes, mime_type, _SMART_PROMPT, response_mime_type='application/json')
            data = json.loads(_strip_json_fences(raw))

        es_financiero = bool(data.get('es_financiero'))
        transacciones = data.get('transacciones') or []

        if es_financiero or (force_finance and transacciones):
            if not transacciones:
                if force_finance:
                    return {'success': False, 'type': 'finance', 'error': 'No se detectaron transacciones financieras en el documento'}
                # es_financiero=true pero sin transacciones extraídas: cae a markdown normal.
                return convert_to_markdown(file_bytes, original_name)

            from backend.engines.finance_converter import convert_to_finance_csv_from_transactions
            result = convert_to_finance_csv_from_transactions(transacciones, data.get('banco'), original_name)
            result['type'] = 'finance'
            return result

        if force_finance:
            return {'success': False, 'type': 'finance', 'error': 'No se detectaron transacciones financieras en el documento'}

        md = data.get('markdown') or ''
        validation = validate_markdown(md)
        filename = _generate_filename(md, original_name)
        return {
            'markdown': md, 'filename': filename, 'method': 'gemini-vision-smart',
            'success': True, 'validation': validation, 'type': 'markdown',
        }
    except Exception:
        if force_finance:
            return {'success': False, 'type': 'finance', 'error': 'No se pudo clasificar/extraer el documento como financiero'}
        result = convert_to_markdown(file_bytes, original_name)
        result['type'] = 'markdown'
        return result
