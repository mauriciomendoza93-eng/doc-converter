"""
document_converter.py — Motor de conversión genérica a Markdown (stateless).

Recibe bytes en memoria, retorna string Markdown generado.
Usa un proveedor de visión (Strategy/Factory) para PDFs e imágenes.
"""

import json
import re
import unicodedata

from backend.utils.validators import validate_markdown
from backend.engines.vision_provider import get_vision_provider

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
        provider = get_vision_provider("gemini")
        md = provider.process_document(file_bytes, mime_type, _PROMPT)
        validation = validate_markdown(md)
        filename = _generate_filename(md, original_name)
        return {'markdown': md, 'filename': filename, 'method': 'gemini-vision', 'success': True, 'validation': validation}
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
