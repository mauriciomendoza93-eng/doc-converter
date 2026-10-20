"""
llm_router.py — Cadena de modelos gratuitos con respaldo automático (solo CLI/Finder).

Una llamada recorre una lista ordenada de candidatos hasta que uno responde Y su
respuesta pasa la validación del llamador (p. ej. "no perdió texto"). Los
candidatos son:

- `omniroute:<modelo>`  OmniRoute local (http://127.0.0.1:20128/v1, compatible con
                        OpenAI): reparte entre las cuentas gratuitas conectadas
                        (Cloudflare, NVIDIA, Antigravity/agy, Mistral, Gemini...).
- `gemini:<modelo>`     API de Gemini con GEMINI_API_KEY, directo y sin OmniRoute.

Un candidato que falla queda en "cooldown" según la causa (credenciales vencidas
6 h, modelo inexistente 6 h, cuota 20 min, caída 3-5 min) y se guarda en disco,
así que los procesos worker de un lote no vuelven a probar lo que ya sabemos que
está caído. Un resultado que no pasa la validación NO penaliza al modelo: solo
se prueba el siguiente.

Configuración (env o .env del proyecto):
    OMNIROUTE_API_KEY      clave de cliente de OmniRoute (activa los candidatos omniroute)
    OMNIROUTE_BASE_URL     por defecto http://127.0.0.1:20128/v1
    GEMINI_API_KEY         activa los candidatos gemini
    DOC_CONVERTER_LLM_TEXT_CHAIN / DOC_CONVERTER_LLM_VISION_CHAIN
                           listas separadas por coma, p. ej.
                           "omniroute:agy/claude-sonnet-4-6,gemini:gemini-3-flash-preview"

Diagnóstico:  python -m backend.engines.llm_router status|probe
"""

import base64
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

# Orden pensado con medición (docs/llm-benchmark.md): primero lo rápido y gratuito
# que conserva el contenido; Gemini directo al final porque su cuota diaria es
# la que más se agota.
DEFAULT_TEXT_CHAIN = (
    'omniroute:agy/gemini-3.1-flash-lite',
    'omniroute:agy/claude-sonnet-4-6',
    'omniroute:nvidia/openai/gpt-oss-20b',
    'omniroute:cloudflare-ai/@cf/openai/gpt-oss-120b',
    'omniroute:cloudflare-ai/@cf/meta/llama-3.3-70b-instruct-fp8-fast',
    'omniroute:cloudflare-ai/@cf/qwen/qwen3-30b-a3b-fp8',
    'gemini:gemini-3-flash-preview',
    'gemini:gemini-3.6-flash',
)
DEFAULT_VISION_CHAIN = (
    'omniroute:agy/gemini-3.1-flash-lite',
    'omniroute:agy/claude-sonnet-4-6',
    'gemini:gemini-3-flash-preview',
    'gemini:gemini-3.6-flash',
)

DEFAULT_BASE_URL = 'http://127.0.0.1:20128/v1'
HEALTH_FILE = Path.home() / 'Library' / 'Caches' / 'doc-converter' / 'llm-health.json'
_COOLDOWN = {'auth': 6 * 3600, 'missing': 6 * 3600, 'quota': 20 * 60, 'down': 3 * 60, 'timeout': 5 * 60, 'quality': 30 * 60}
# Rechazos de validación seguidos tras los que un modelo pasa al final de la cola un rato.
_REJECTS_BEFORE_DEMOTION = 3
_DEFAULT_TIMEOUT = 100
_health_lock = threading.Lock()


class LLMUnavailable(RuntimeError):
    """Ningún candidato respondió con un resultado válido."""


@dataclass(frozen=True)
class Candidate:
    kind: str       # 'omniroute' | 'gemini'
    model: str

    @property
    def label(self) -> str:
        return f'{self.kind}:{self.model}'


@dataclass
class Reply:
    text: str
    model: str
    seconds: float
    attempts: list = field(default_factory=list)   # [(label, motivo)] de los que fallaron antes


def parse_chain(spec) -> list:
    out = []
    for item in (spec.split(',') if isinstance(spec, str) else spec):
        item = item.strip()
        if ':' in item:
            kind, model = item.split(':', 1)
            if kind in ('omniroute', 'gemini') and model:
                out.append(Candidate(kind, model))
    return out


def _debug(msg: str) -> None:
    if os.environ.get('DOC_CONVERTER_LLM_DEBUG'):
        print(f'[llm {time.strftime("%H:%M:%S")}] {msg}', file=sys.stderr, flush=True)


def _env(name: str, default: str = '') -> str:
    return os.environ.get(name, default).strip()


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent.parent / '.env')
    except ImportError:
        pass


# --- Salud persistente ---------------------------------------------------------

def _read_health() -> dict:
    try:
        return json.loads(HEALTH_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write_health(data: dict) -> None:
    try:
        HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=HEALTH_FILE.parent, prefix='.health-')
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f)
        os.replace(tmp, HEALTH_FILE)
    except OSError:
        pass  # la salud es una optimización: nunca debe romper una conversión


def cooldown_remaining(label: str) -> float:
    entry = _read_health().get(label)
    return max(0.0, entry['until'] - time.time()) if isinstance(entry, dict) and 'until' in entry else 0.0


def mark_failed(label: str, kind: str, detail: str = '') -> None:
    with _health_lock:
        data = _read_health()
        now = time.time()
        data = {k: v for k, v in data.items() if k == '_rejects' or v.get('until', 0) > now}
        data[label] = {'until': now + _COOLDOWN[kind], 'kind': kind, 'detail': detail[:160]}
        _write_health(data)


def mark_ok(label: str) -> None:
    with _health_lock:
        data = _read_health()
        if label in data or label in _rejects(data):
            data.pop(label, None)
            data.get('_rejects', {}).pop(label, None)
            _write_health(data)


def _rejects(data: dict) -> dict:
    return data.get('_rejects', {})


def mark_rejected(label: str) -> None:
    """La respuesta no pasó la validación (resumió, perdió o cambió datos). No es una
    falla del servicio, pero un modelo que lo hace seguido con este tipo de documento
    solo hace perder tiempo: tras varias seguidas baja al final de la cola."""
    with _health_lock:
        data = _read_health()
        counts = data.setdefault('_rejects', {})
        counts[label] = counts.get(label, 0) + 1
        if counts[label] >= _REJECTS_BEFORE_DEMOTION:
            counts.pop(label)
            data[label] = {'until': time.time() + _COOLDOWN['quality'], 'kind': 'quality', 'detail': 'rechazos seguidos de la validación'}
        _write_health(data)


def classify(exc: Exception) -> str:
    """Causa → tipo de cooldown."""
    if isinstance(exc, urllib.error.HTTPError):
        body = ''
        try:
            body = exc.read().decode(errors='replace').lower()
        except Exception:
            pass
        if exc.code in (401, 403):
            return 'auth'
        if exc.code in (404, 410) or (exc.code == 400 and any(k in body for k in ('not available', 'not found', 'unknown model', 'live catalog'))):
            return 'missing'
        if exc.code == 429 or 'quota' in body or 'rate limit' in body:
            return 'quota'
        if exc.code in (408, 504):
            return 'timeout'
        return 'down'
    text = str(exc).lower()
    if '429' in text or 'resource_exhausted' in text or 'quota' in text:
        return 'quota'
    if '401' in text or '403' in text or 'api key' in text or 'permission' in text:
        return 'auth'
    if '404' in text or 'not found' in text:
        return 'missing'
    if 'timed out' in text or 'timeout' in text or 'deadline' in text:
        return 'timeout'
    return 'down'


# --- Transportes -------------------------------------------------------------------

def _omniroute_up(base: str) -> bool:
    parsed = urlparse(base)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 80), timeout=1.5):
            return True
    except OSError:
        return False


def _chat_omniroute(model: str, system, user: str, images: list, max_tokens: int, json_mode: bool, timeout: int) -> str:
    base = _env('OMNIROUTE_BASE_URL', DEFAULT_BASE_URL).rstrip('/')
    if images:
        content = [{'type': 'text', 'text': user}] + [
            {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(im).decode()}}
            for im in images
        ]
    else:
        content = user
    messages = ([{'role': 'system', 'content': system}] if system else []) + [{'role': 'user', 'content': content}]
    body = {'model': model, 'messages': messages, 'max_tokens': max_tokens, 'temperature': 0, 'stream': True}
    if json_mode:
        body['response_format'] = {'type': 'json_object'}
    req = urllib.request.Request(
        base + '/chat/completions', data=json.dumps(body).encode(),
        headers={'Authorization': 'Bearer ' + _env('OMNIROUTE_API_KEY'), 'Content-Type': 'application/json'},
    )
    # Con stream el gateway de OmniRoute recibe bytes de continuo y no corta a los ~33 s
    # como hace con una respuesta larga de un modelo lento.
    parts, deadline = [], time.monotonic() + timeout
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            if time.monotonic() > deadline:
                raise TimeoutError(f'timed out tras {timeout}s')
            line = raw.decode(errors='replace').strip()
            if not line.startswith('data:') or line.endswith('[DONE]'):
                continue
            try:
                delta = json.loads(line[5:])['choices'][0].get('delta', {})
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
            if delta.get('content'):
                parts.append(delta['content'])
    return ''.join(parts)


def _chat_gemini(model: str, system, user: str, images: list, document, max_tokens: int, json_mode: bool, timeout: int) -> str:
    from google import genai
    from google.genai import types
    client = genai.Client(
        api_key=_env('GEMINI_API_KEY') or None,
        http_options=types.HttpOptions(timeout=timeout * 1000, retry_options=types.HttpRetryOptions(attempts=1)),
    )
    parts = []
    if document:
        parts.append(types.Part.from_bytes(data=document[0], mime_type=document[1]))
    else:
        parts += [types.Part.from_bytes(data=im, mime_type='image/jpeg') for im in images]
    parts.append(user)
    config = types.GenerateContentConfig(
        system_instruction=system or None, max_output_tokens=max_tokens, temperature=0,
        response_mime_type='application/json' if json_mode else None,
    )
    return client.models.generate_content(model=model, contents=parts, config=config).text or ''


def _as_jpeg(data: bytes) -> bytes:
    if data[:2] == b'\xff\xd8':
        return data
    from PIL import Image
    buf = io.BytesIO()
    Image.open(io.BytesIO(data)).convert('RGB').save(buf, format='JPEG', quality=88)
    return buf.getvalue()


def render_pdf_pages(data: bytes, dpi: int = 130) -> list:
    """Páginas de un PDF como JPEG, para los modelos que solo reciben imágenes."""
    import fitz
    out = []
    with fitz.open(stream=data, filetype='pdf') as doc:
        for page in doc:
            out.append(page.get_pixmap(dpi=dpi).tobytes('jpeg', jpg_quality=82))
    return out


# --- Enrutador -------------------------------------------------------------------------

class LLMRouter:
    def __init__(self, text_chain=None, vision_chain=None):
        _load_dotenv()
        self.text_chain = parse_chain(text_chain or _env('DOC_CONVERTER_LLM_TEXT_CHAIN') or DEFAULT_TEXT_CHAIN)
        self.vision_chain = parse_chain(vision_chain or _env('DOC_CONVERTER_LLM_VISION_CHAIN') or DEFAULT_VISION_CHAIN)

    def configured(self, c: Candidate) -> bool:
        if c.kind == 'omniroute':
            return bool(_env('OMNIROUTE_API_KEY'))
        return bool(_env('GEMINI_API_KEY'))

    def available(self) -> bool:
        return any(self.configured(c) for c in self.text_chain)

    def complete(self, user: str, *, system=None, images=None, document=None, vision=False,
                 max_tokens: int = 8000, json_mode: bool = False, validate=None, start: int = 0,
                 timeout: int = _DEFAULT_TIMEOUT) -> Reply:
        """Prueba los candidatos en orden (rotado desde `start` entre los primeros
        sanos, para repartir chunks en paralelo) y retorna la primera respuesta válida.
        `validate(texto) -> bool | str`: False o un str (motivo) rechazan la respuesta."""
        chain = [c for c in (self.vision_chain if vision else self.text_chain) if self.configured(c)]
        if not chain:
            raise LLMUnavailable('no hay claves configuradas (OMNIROUTE_API_KEY / GEMINI_API_KEY)')
        healthy = [c for c in chain if cooldown_remaining(c.label) == 0]
        # Los que fallaron hace poco se saltan; si no queda ninguno sano se reintentan
        # todos (puede que ya se hayan recuperado).
        pool = healthy or chain
        if healthy and start:
            k = start % min(len(healthy), 3)
            pool = healthy[k:] + healthy[:k]
        attempts = [(c.label, 'en pausa por fallo reciente') for c in chain if c not in pool]
        pages, omni_up = None, None
        t0 = time.monotonic()
        for cand in pool:
            label = cand.label
            try:
                if cand.kind == 'omniroute':
                    if omni_up is None:
                        omni_up = _omniroute_up(_env('OMNIROUTE_BASE_URL', DEFAULT_BASE_URL))
                    if not omni_up:
                        attempts.append((label, 'OmniRoute no responde'))
                        continue
                    imgs = images
                    if vision and not imgs and document:
                        if document[1] == 'application/pdf':
                            pages = pages or render_pdf_pages(document[0])
                            imgs = pages
                        else:
                            imgs = [_as_jpeg(document[0])]
                    text = _chat_omniroute(cand.model, system, user, imgs or [], max_tokens, json_mode, timeout)
                else:
                    text = _chat_gemini(cand.model, system, user, images or [], document, max_tokens, json_mode, timeout)
            except Exception as exc:
                kind = classify(exc)
                mark_failed(label, kind, str(exc))
                attempts.append((label, f'{kind}: {str(exc)[:80]}'))
                _debug(f'{label} falló ({kind}) a los {time.monotonic() - t0:.0f}s: {str(exc)[:80]}')
                continue
            text = _strip_fences(text)
            verdict = validate(text) if validate else True
            if verdict is True or verdict is None:
                mark_ok(label)
                _last['model'] = label
                _debug(f'{label} OK en {time.monotonic() - t0:.0f}s ({len(text)} caracteres)')
                return Reply(text, label, round(time.monotonic() - t0, 1), attempts)
            mark_rejected(label)
            attempts.append((label, f'respuesta rechazada: {verdict if isinstance(verdict, str) else "no pasó la validación"}'))
            _debug(f'{label} RECHAZADO a los {time.monotonic() - t0:.0f}s: {attempts[-1][1]}')
        raise LLMUnavailable('; '.join(f'{a}: {b}' for a, b in attempts) or 'sin respuesta')


def _strip_fences(text: str) -> str:
    t = (text or '').strip()
    if t.startswith('```'):
        t = t.split('\n', 1)[1] if '\n' in t else ''
        if t.rstrip().endswith('```'):
            t = t.rstrip()[:-3]
    return t.strip()


_router = None
_last = {'model': ''}


def last_model() -> str:
    """Último candidato que respondió (para declararlo en el método del informe)."""
    return _last['model']


def get_router() -> LLMRouter:
    global _router
    if _router is None:
        _router = LLMRouter()
    return _router


# --- CLI de diagnóstico -----------------------------------------------------------------

def _status(probe: bool) -> int:
    r = get_router()
    base = _env('OMNIROUTE_BASE_URL', DEFAULT_BASE_URL)
    print(f'OmniRoute: {"activo" if _omniroute_up(base) else "NO responde"} en {base} '
          f'(clave {"configurada" if _env("OMNIROUTE_API_KEY") else "FALTA OMNIROUTE_API_KEY"})')
    print(f'Gemini directo: clave {"configurada" if _env("GEMINI_API_KEY") else "FALTA GEMINI_API_KEY"}')
    for title, chain, vision in (('Texto', r.text_chain, False), ('Visión', r.vision_chain, True)):
        print(f'\n{title}:')
        for c in chain:
            if not r.configured(c):
                print(f'  - {c.label}: sin clave')
                continue
            rest = cooldown_remaining(c.label)
            state = f'en pausa {rest / 60:.0f} min ({_read_health().get(c.label, {}).get("kind")})' if rest else 'ok'
            if probe:
                try:
                    t = time.monotonic()
                    one = LLMRouter(text_chain=[c.label], vision_chain=[c.label])
                    one.complete(f'Responde solo: ok ({time.time():.0f})', max_tokens=20, timeout=40)
                    state = f'responde en {time.monotonic() - t:.1f}s'
                except LLMUnavailable as e:
                    state = f'FALLA: {str(e)[:90]}'
            print(f'  - {c.label}: {state}')
    return 0


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if cmd in ('status', 'probe'):
        sys.exit(_status(probe=(cmd == 'probe')))
    print('uso: python -m backend.engines.llm_router status|probe')
    sys.exit(2)
