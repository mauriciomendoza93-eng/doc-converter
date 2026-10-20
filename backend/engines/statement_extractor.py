"""
statement_extractor.py — Movimientos de un extracto bancario a partir de su TEXTO.

En vez de pedirle a un modelo que lea píxeles (y se equivoque de dígito), el texto
lo extrae el motor local (capa de texto u OCR de Vision) y el modelo solo lo
estructura como JSON. Después se verifica cada movimiento contra ese texto: el
importe devuelto tiene que existir como cifra en el documento; los que no, se
descartan y se informan. Solo lo usa el CLI.
"""

import json
import re

from backend.engines import llm_router, structured_ocr
from backend.engines.finance_converter import _detect_banco, _to_number

_CHUNK_CHARS = 9_000
_MAX_DROP_RATIO = 0.2   # si se descarta más de esto, la extracción no es confiable

_PROMPT = (
    'Recibes el texto de un extracto bancario (puede venir de OCR, con tablas en Markdown o '
    'columnas separadas). Extrae TODOS los movimientos.\n\n'
    'Responde SOLO un objeto JSON: {"banco": "<nombre del banco o vacío>", "transacciones": '
    '[{"fecha": "YYYY-MM-DD", "descripcion": "<texto del movimiento>", "monto": <número>}]}\n\n'
    'Reglas estrictas:\n'
    '- "monto" es un número JSON con punto decimal, sin separadores de miles. Negativo para '
    'egresos/débitos/cargos/retiros y positivo para ingresos/créditos/abonos/depósitos.\n'
    '- Copia los importes exactamente como aparecen: no calcules, no redondees, no corrijas.\n'
    '- No incluyas saldos, totales, subtotales ni filas de cabecera o pie.\n'
    '- Un movimiento por fila, en el orden del documento. Si no hay movimientos: {"banco": "", "transacciones": []}.'
)


def _amounts_in(text: str) -> set:
    """Valores absolutos (2 decimales) de todas las cifras que aparecen en el texto."""
    values = set()
    for token in re.findall(r'\d[\d.,]*\d|\d', text):
        n = _to_number(token)
        if n == n:  # no NaN
            values.add(round(abs(n), 2))
    return values


def _parse(raw: str):
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get('transacciones'), list):
        return None
    return data


def _valid_json(raw: str):
    return True if _parse(raw) else 'no es el JSON esperado'


def _chunks(text: str) -> list:
    if len(text) <= _CHUNK_CHARS:
        return [text]
    out, rest = [], text
    while len(rest) > _CHUNK_CHARS:
        cut = max(rest.rfind('\n', 0, _CHUNK_CHARS), 1)
        out.append(rest[:cut])
        rest = rest[cut:]
    out.append(rest)
    return out


def extract(text: str) -> dict:
    """Retorna {'banco', 'transacciones', 'warnings', 'model'}; lanza LLMUnavailable
    o ValueError si la extracción no es confiable (el llamador usa otro camino)."""
    router = llm_router.get_router()
    if not router.available():
        raise llm_router.LLMUnavailable('no hay claves configuradas')
    known = _amounts_in(text)
    banco, kept, dropped, models = '', [], [], set()
    for i, chunk in enumerate(_chunks(text)):
        reply = router.complete(chunk, system=_PROMPT, json_mode=True, validate=_valid_json, start=i, max_tokens=8000)
        models.add(reply.model)
        data = _parse(reply.text)
        banco = banco or str(data.get('banco') or '').strip()
        for t in data['transacciones']:
            try:
                monto = float(t['monto'])
                fecha, desc = str(t['fecha']).strip(), str(t.get('descripcion', '')).strip()
            except (KeyError, TypeError, ValueError):
                dropped.append(str(t)[:60])
                continue
            if round(abs(monto), 2) in known and re.fullmatch(r'\d{4}-\d{2}-\d{2}', fecha):
                kept.append({'fecha': fecha, 'descripcion': desc, 'monto': monto})
            else:
                dropped.append(f'{fecha} {desc[:30]} {monto}')
    # El banco: la detección por palabras clave manda; el nombre del modelo solo vale si
    # realmente aparece en el documento (evita nombres inventados o corruptos).
    detected = _detect_banco(text[:3000], '')
    if detected != 'Efectivo':
        banco = detected
    elif not banco or banco.lower() not in text.lower():
        banco = ''
    total = len(kept) + len(dropped)
    if not kept:
        raise ValueError('el modelo no devolvió movimientos verificables')
    if len(dropped) > _MAX_DROP_RATIO * total:
        raise ValueError(f'{len(dropped)} de {total} movimientos no se pudieron verificar contra el texto')
    warnings = []
    if dropped:
        warnings.append(f'{len(dropped)} movimiento(s) descartado(s) por no coincidir con ninguna cifra del documento: ' + '; '.join(dropped[:3]))
    warnings.append('extracto procesado desde el texto del documento; los signos (ingreso/egreso) los infirió el modelo: revisa los totales')
    return {'banco': banco, 'transacciones': kept, 'warnings': warnings, 'model': ', '.join(sorted(models))}
