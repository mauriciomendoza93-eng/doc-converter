"""
document_converter.py — Motor de conversión genérica a Markdown (stateless).

Recibe bytes en memoria, retorna string Markdown generado.
Usa un proveedor de visión (Strategy/Factory) para PDFs e imágenes.
"""

from backend.utils.validators import validate_markdown
from backend.engines.vision_provider import get_vision_provider

_PROMPT = (
    'Eres un transcriptor experto de documentos legales y testimonios notariales. '
    'Transcribe este documento a formato Markdown preservando toda la jerarquía, '
    'sin omitir ni resumir absolutamente nada. Lee la letra pequeña. '
    'Ignora sellos que tapen el texto, pero extrae todo el texto legible. '
    'No agregues saludos ni explicaciones, devuelve SOLO el Markdown crudo.'
)


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
        return {'markdown': md, 'method': 'gemini-vision', 'success': True, 'validation': validation}
    except Exception as e:
        return {'markdown': '', 'method': 'gemini-vision', 'success': False, 'error': str(e)}