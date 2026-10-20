"""
text_structurer.py — Reestructura texto ya extraído (OCR local o capa de texto)
a Markdown limpio con un modelo gratuito. Solo lo usa el CLI.

El texto se parte en fragmentos pequeños y cada uno viaja por la cadena de modelos
de llm_router (OmniRoute + Gemini). Un fragmento solo se acepta si el modelo no
resumió, no perdió texto y no cambió, perdió ni inventó cifras; si un modelo falla
esa validación se prueba el siguiente, y si todos fallan ese fragmento se conserva
sin estructurar (peor formato es preferible a perder datos). Los fragmentos corren
en paralelo repartidos entre los primeros modelos de la cadena.
"""

from concurrent.futures import ThreadPoolExecutor

from backend.engines import llm_router, structured_ocr

# ~1.5-2k tokens de salida por fragmento: cabe en el límite de salida de los modelos
# gratuitos y un fallo solo repite una porción pequeña del documento.
_CHUNK_CHARS = 6_000
_MAX_WORKERS = 4
# Un modelo puede "resumir" en vez de reestructurar y perder contenido.
_MIN_OUTPUT_RATIO = 0.6
_MIN_RECALL = 0.92

_PROMPT = (
    'Recibes texto extraído de un documento (legal, académico, administrativo o '
    'financiero) por OCR o por su capa de texto. Las columnas, tablas y títulos pueden '
    'haber perdido su formato, o venir ya como Markdown parcial.\n\n'
    'Devuelve ese mismo contenido como Markdown bien estructurado:\n'
    '- Reconstruye títulos y jerarquía con encabezados.\n'
    '- Reconstruye tablas y formularios como tablas Markdown cuando el contenido lo sea '
    '(por ejemplo pares etiqueta/valor de un formulario).\n'
    '- Corrige la separación de líneas y el orden de lectura cuando el OCR mezcló columnas.\n\n'
    'Reglas estrictas:\n'
    '- No resumas, no omitas ni inventes nada: todo dato del texto debe aparecer.\n'
    '- No corrijas nombres, cifras, fechas ni códigos: cópialos tal cual, incluso si parecen '
    'erróneos o contienen caracteres extraños (p. ej. "10234560ęŚ").\n'
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


def validator(source: str):
    """Función de validación para llm_router: True si `salida` conserva `source`."""
    ref_numbers = len(structured_ocr.numeric_tokens(source))

    def check(output: str):
        if len(output) < _MIN_OUTPUT_RATIO * len(source):
            return 'la salida es mucho más corta que el texto (resumió)'
        recall = structured_ocr.recall(source, output)
        if recall < _MIN_RECALL:
            return f'perdió texto (conserva {recall:.0%})'
        missing, extra = structured_ocr.number_check(source, output)
        # Una cifra cambiada aparece como "falta una + sobra otra": cero tolerancia
        # (1 % en documentos con cientos de cifras). Solo se perdonan enteros sueltos
        # nuevos, que son la numeración de listas que el modelo puede agregar.
        allowed = int(0.01 * ref_numbers)
        invented = [t for t in extra if not (t.isdigit() and len(t) <= 3)]
        if len(missing) > allowed or len(invented) > allowed:
            return f'alteró cifras (faltan {missing[:3]}, nuevas {invented[:3]})'
        return True

    return check


def _structure_chunk(index: int, chunk: str):
    router = llm_router.get_router()
    try:
        reply = router.complete(chunk, system=_PROMPT, validate=validator(chunk), start=index)
        return reply.text, reply.model, None
    except llm_router.LLMUnavailable as e:
        return chunk, None, str(e)


def structure_markdown(text: str) -> tuple:
    """Reestructura el texto y retorna (markdown, método, advertencias).

    Lanza LLMUnavailable si ningún fragmento pudo procesarse (sin claves, sin red,
    todo caído): el llamador decide el respaldo."""
    if not text.strip():
        return text, 'sin texto', []
    router = llm_router.get_router()
    if not router.available():
        raise llm_router.LLMUnavailable('no hay claves configuradas (OMNIROUTE_API_KEY / GEMINI_API_KEY)')

    chunks = _split_text(text)
    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(chunks))) as pool:
        results = list(pool.map(lambda a: _structure_chunk(*a), enumerate(chunks)))

    ok = [r for r in results if r[1]]
    if not ok:
        raise llm_router.LLMUnavailable(results[0][2] or 'sin respuesta')

    warnings = []
    for i, (_, model, error) in enumerate(results, 1):
        if not model:
            where = f'fragmento {i} de {len(chunks)}' if len(chunks) > 1 else 'el documento'
            warnings.append(f'IA: {where} se dejó sin estructurar ({error[:160]})')
    models = sorted({r[1] for r in ok})
    return '\n\n'.join(r[0] for r in results), 'ia (' + ', '.join(models) + ')', warnings
