"""
vlm_ocr.py — OCR de escaneos y fotos con modelos de visión: en la nube (cadena gratuita de
llm_router) y en la Mac (GLM-OCR vía MLX, cli/vlmocr).

El OCR de Vision lee letra por letra: en escaneos de fax, fotos con sombra o márgenes
cortados entrega "edame", "arciles,jen", "iforniación" y basura de sellos. Un modelo
de visión lee la página entera con contexto de idioma, en orden de lectura y sin ruido.
Medido con un testimonio escaneado (docs/ocr-benchmark.md), de mejor a peor: Claude Sonnet
4.6 (vía OmniRoute), Gemini, GLM-OCR local, Vision. GLM-OCR corre sin red ni cuota (~1,1 GB)
pero tarda 15-90 s por página y comete errores de contenido ("recibita" por "ochenta y
cuatro"); por eso es el respaldo de la nube, no al revés.

Modo (DOC_CONVERTER_OCR): 'auto' (por defecto) nube → GLM-OCR → Vision; 'local' GLM-OCR →
Vision, sin que la imagen salga de la Mac; 'vision' solo Vision.

Un modelo puede fallar distinto que un OCR clásico: entrar en bucle, saltarse un bloque
o completar texto que no está. Por eso cada página se contrasta con el OCR de Vision de
la misma imagen (que es rápido y no inventa) y se descarta si:
  - llegó al tope de tokens o repite el mismo fragmento (bucle);
  - cubre menos del MIN_AGREEMENT del texto de Vision (se saltó contenido);
  - es mucho más larga que Vision (inventó o duplicó).
Las cifras de Vision que el modelo no reproduce se informan para revisarlas a mano.

Sin Apple Silicon, sin .venv-ocr o con DOC_CONVERTER_OCR=vision, available() es False;
sin claves o con modo 'local'/'vision', cloud_available() es False.
"""

import json
import os
import platform
import re
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from backend.engines import structured_ocr

PROJECT_DIR = Path(__file__).resolve().parents[2]
VENV_PYTHON = PROJECT_DIR / '.venv-ocr' / 'bin' / 'python'
HELPER = PROJECT_DIR / 'cli' / 'vlmocr' / 'vlmocr.py'

# Medido con un testimonio notarial escaneado: el modelo bueno coincide ~0,87 con Vision
# (lo que falta son los errores de Vision); un bucle o una página recortada, 0,34-0,47.
MIN_AGREEMENT = 0.75
MAX_LENGTH_RATIO = 1.6
# Sin texto de Vision no hay con qué contrastar: solo se acepta una lectura sustancial.
MIN_WORDS_UNVERIFIED = 20
# Comentarios del modelo sobre la imagen en vez de su texto ("The text is too blurry to be
# accurately transcribed."), visto en una página casi en blanco de un testimonio.
_META = re.compile(r'^\W*(the (text|image|document|page)|this (image|page|document)|i (cannot|can\'t|am unable)'
                   r'|unable to|no (legible |readable )?text)\b.*(?:\n|$)', re.I | re.M)
# Un modelo frío tarda en cargar; una página densa con batería y bajo consumo, ~90 s.
_TIMEOUT_PER_PAGE = 300
_TIMEOUT_LOAD = 180

# Nube: orden por calidad medida, no por velocidad (Claude ~50 s/página, Gemini ~25 s).
# Gemini 3.6 Flash leyó mejor que 3 Flash, pero devolvió una página vacía: va al final.
# Claude Haiku 5.5 (API directa, ~US$ 1 por 1 000 páginas) solo entra con ANTHROPIC_API_KEY;
# no se pudo medir sin clave, pero cada página igual se valida y, si falla, sigue la cadena.
CLOUD_CHAIN = (
    'anthropic:claude-haiku-5-5',
    'omniroute:agy/claude-sonnet-4-6',
    'gemini:gemini-3-flash-preview',
    'omniroute:agy/gemini-3.1-flash-lite',
    'gemini:gemini-3.6-flash',
)
CLOUD_PROMPT = ('Transcribe este documento escaneado a Markdown, completo y fiel: todo el texto en orden '
                'de lectura, sin resumir, sin corregir el contenido ni agregar comentarios. Usa títulos y '
                'tablas Markdown cuando el documento los tenga. Responde solo con la transcripción.')
# Con 3 páginas a la vez agy (OmniRoute) empezó a responder 503 en un escaneo de 15 páginas.
_CLOUD_WORKERS = 2
_CLOUD_TIMEOUT = 180


def _cloud_failure(detail: str) -> str:
    """Resumen corto de por qué ningún modelo de la nube sirvió para una página."""
    d = detail.lower()
    if 'rechazada' in d:
        return 'sus lecturas no pasaron la validación'
    if 'quota' in d or '429' in d:
        return 'sin cuota o no disponible'
    return 'no disponible'


def mode() -> str:
    m = os.environ.get('DOC_CONVERTER_OCR', '').lower()
    return m if m in ('local', 'vision') else 'auto'


def available() -> bool:
    """GLM-OCR local instalado y permitido."""
    if mode() == 'vision':
        return False
    return (sys.platform == 'darwin' and platform.machine() == 'arm64'
            and VENV_PYTHON.is_file() and HELPER.is_file())


def _cloud_router():
    from backend.engines.llm_router import LLMRouter
    return LLMRouter(vision_chain=os.environ.get('DOC_CONVERTER_LLM_OCR_CHAIN') or CLOUD_CHAIN)


def cloud_available() -> bool:
    return mode() == 'auto' and _cloud_router().available()


def read_cloud(image_paths: list, plains: list) -> list:
    """Lee cada imagen con la cadena de la nube, en paralelo. Cada respuesta se valida con
    judge() contra el texto de Vision de esa página; si no pasa, prueba el siguiente modelo.
    Retorna por imagen (texto o None, modelo, motivo si ninguno sirvió)."""
    from backend.engines.llm_router import LLMUnavailable, _as_jpeg
    router = _cloud_router()

    def one(i):
        def validate(text):
            out, why = judge({'text': text}, plains[i])
            return True if out is not None else why
        with open(image_paths[i], 'rb') as f:
            jpeg = _as_jpeg(f.read())
        try:
            reply = router.complete(CLOUD_PROMPT, images=[jpeg], vision=True, validate=validate,
                                    max_tokens=8000, timeout=_CLOUD_TIMEOUT)
        except LLMUnavailable as e:
            return None, '', 'nube: ' + _cloud_failure(str(e))
        return clean(reply.text), reply.model, ''

    if not image_paths:
        return []
    with ThreadPoolExecutor(max_workers=min(_CLOUD_WORKERS, len(image_paths))) as pool:
        return list(pool.map(one, range(len(image_paths))))


def recognize(image_paths: list) -> list:
    """Corre el modelo sobre las imágenes, en una sola carga. Retorna por imagen
    {'text', 'seconds', 'truncated'} o None si esa imagen no llegó a procesarse.
    Lanza RuntimeError si el helper no arranca."""
    if not image_paths:
        return []
    try:
        proc = subprocess.run(
            [str(VENV_PYTHON), str(HELPER)],
            input=json.dumps({'images': [str(p) for p in image_paths]}),
            capture_output=True, text=True,
            timeout=_TIMEOUT_LOAD + _TIMEOUT_PER_PAGE * len(image_paths),
        )
    except (subprocess.SubprocessError, OSError) as e:
        raise RuntimeError(f'OCR con modelo falló: {e}') from e
    results = [None] * len(image_paths)
    for line in proc.stdout.splitlines():
        try:
            item = json.loads(line)
            results[item['i']] = item
        except (ValueError, KeyError, IndexError, TypeError):
            continue
    if proc.returncode != 0 and not any(results):
        raise RuntimeError(f'OCR con modelo falló: {proc.stderr.strip()[-300:]}')
    return results


def _grams(text: str, n: int = 4) -> Counter:
    text = re.sub(r'\s+', ' ', text.lower())
    return Counter(text[i:i + n] for i in range(len(text) - n + 1))


def agreement(reference: str, candidate: str) -> float:
    """Fracción de los 4-gramas de caracteres de `reference` presentes en `candidate`.
    Tolera los errores de una letra del OCR clásico mejor que comparar palabras."""
    ref, cand = _grams(reference), _grams(candidate)
    total = sum(ref.values())
    if not total:
        return 1.0
    return sum(min(n, cand[g]) for g, n in ref.items()) / total


def looks_looped(text: str) -> bool:
    """Un mismo fragmento de 8 palabras repetido 4+ veces: el modelo entró en bucle."""
    words = text.split()
    if len(words) < 40:
        return False
    counts = Counter(tuple(words[i:i + 8]) for i in range(len(words) - 7))
    return counts.most_common(1)[0][1] >= 4


def judge(result, plain: str) -> tuple:
    """Decide si la lectura del modelo reemplaza al OCR de Vision de la misma página.
    Retorna (texto o None, motivo del descarte)."""
    if not result or not result.get('text', '').strip():
        return None, 'sin resultado'
    text = clean(result['text'])
    if result.get('truncated') or looks_looped(text):
        return None, 'repetía texto en bucle'
    if not plain.strip():
        if len(text.split()) < MIN_WORDS_UNVERIFIED:
            return None, 'Vision no vio texto y la lectura del modelo no se puede verificar'
        return text, ''
    score = agreement(plain, text)
    if score < MIN_AGREEMENT:
        return None, f'omitía texto (coincidencia {score:.0%})'
    ratio = len(structured_ocr.tokens(text)) / max(1, len(structured_ocr.tokens(plain)))
    if ratio > MAX_LENGTH_RATIO:
        return None, f'agregaba texto ({ratio:.1f}× el de Vision)'
    return text, ''


def clean(text: str) -> str:
    """Quita los envoltorios ```markdown y los comentarios sobre la imagen que a veces
    agrega el modelo, y convierte sus tablas HTML a Markdown."""
    text = re.sub(r'^\s*```(?:markdown|md|html)?\s*\n|\n?```\s*$', '', text.strip())
    text = re.sub(r'\n{3,}', '\n\n', _META.sub('', text)).strip()
    return re.sub(r'<table\b.*?</table>', lambda m: _html_table_md(m.group(0)), text, flags=re.S | re.I)


def _html_table_md(html: str) -> str:
    rows = []
    for tr in re.findall(r'<tr\b.*?</tr>', html, flags=re.S | re.I):
        cells = re.findall(r'<t[hd]\b[^>]*>(.*?)</t[hd]>', tr, flags=re.S | re.I)
        rows.append([re.sub(r'<[^>]+>', ' ', c) for c in cells])
    if not rows:
        return html
    return structured_ocr._table_md(rows)


def missing_numbers(plain: str, text: str, limit: int = 8) -> list:
    """Cifras de 3+ dígitos que Vision leyó y el modelo no reproduce."""
    missing, _extra = structured_ocr.number_check(plain, text)
    out = []
    for t in missing:
        if sum(c.isdigit() for c in t) >= 3 and t not in out:
            out.append(t)
    return out[:limit]
