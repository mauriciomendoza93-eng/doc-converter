"""
text_structurer.py — Reestructura texto ya extraído (OCR local o capa de texto)
a Markdown limpio usando Gemini en modo texto. Solo lo usa el CLI.

Es el reemplazo del modo IA basado en visión: en vez de mandar las imágenes del
PDF (payload de megabytes, troceado en varias solicitudes y minutos de espera),
manda el texto que ya extrajo la Mac. Un documento entra normalmente en UNA
solicitud sin importar cuántas páginas tenga.
"""

from concurrent.futures import ThreadPoolExecutor

from backend.engines.vision_provider import get_vision_provider

# Tamaño de lote en caracteres. Un documento típico entra completo en uno solo;
# el corte se hace en un salto de página/párrafo para no partir una frase.
_CHUNK_CHARS = 40_000
_MAX_WORKERS = 4
# Un modelo puede "resumir" en vez de reestructurar y perder contenido. Si la
# salida encoge por debajo de esta proporción de la entrada, se descarta y se
# conserva el texto crudo: peor formato es preferible a perder datos.
_MIN_OUTPUT_RATIO = 0.6

_PROMPT = (
    'Recibes el texto crudo extraído por OCR de un documento (legal, académico o '
    'administrativo). Las columnas, tablas y títulos perdieron su formato original.\n\n'
    'Devuelve ese mismo contenido como Markdown bien estructurado:\n'
    '- Reconstruye títulos y jerarquía con encabezados.\n'
    '- Reconstruye tablas y formularios como tablas Markdown cuando el contenido lo sea '
    '(por ejemplo pares etiqueta/valor de un formulario).\n'
    '- Corrige la separación de líneas y el orden de lectura cuando el OCR mezcló columnas.\n\n'
    'Reglas estrictas:\n'
    '- No resumas, no omitas ni inventes nada: todo dato del texto debe aparecer.\n'
    '- No corrijas nombres, cifras ni fechas, cópialos tal cual.\n'
    '- No agregues comentarios ni explicaciones: responde SOLO el Markdown.'
)


def _split_text(text: str) -> list:
    if len(text) <= _CHUNK_CHARS:
        return [text]
    chunks, rest = [], text
    while len(rest) > _CHUNK_CHARS:
        cut = rest.rfind('\n\n', 0, _CHUNK_CHARS)
        if cut <= 0:
            cut = rest.rfind('\n', 0, _CHUNK_CHARS)
        if cut <= 0:
            cut = _CHUNK_CHARS
        chunks.append(rest[:cut])
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks


def _with_gemini(text: str) -> str:
    provider = get_vision_provider('gemini')
    chunks = _split_text(text)
    if len(chunks) == 1:
        return provider.process_text(chunks[0], _PROMPT)
    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(chunks))) as pool:
        parts = list(pool.map(lambda c: provider.process_text(c, _PROMPT), chunks))
    return '\n\n'.join(parts)


def structure_markdown(text: str) -> tuple:
    """Reestructura el texto con Gemini y retorna (markdown, método).

    Si el resultado encoge demasiado respecto de la entrada, se descarta y se
    conserva el texto sin estructurar. Propaga la excepción del proveedor
    (p. ej. cuota agotada) para que el llamador decida el respaldo.
    """
    if not text.strip():
        return text, 'sin texto'

    markdown = _with_gemini(text)
    if len(markdown) < _MIN_OUTPUT_RATIO * len(text):
        return text, 'sin estructurar (la IA devolvió menos texto del que había)'
    return markdown, 'ia'
