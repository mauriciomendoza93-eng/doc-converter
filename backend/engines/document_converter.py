"""
document_converter.py — Motor de conversión genérica a Markdown (stateless).

Recibe bytes en memoria, retorna string Markdown generado.
Usa un proveedor de visión (Strategy/Factory) para PDFs e imágenes.
"""

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
