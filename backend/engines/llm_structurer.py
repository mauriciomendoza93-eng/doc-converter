"""
llm_structurer.py — Modelo de lenguaje LOCAL (Ollama) para reestructurar texto
ya extraído por OCR. Solo lo usa el CLI.

Existe para no depender de la cuota de Gemini y para que los documentos
personales o de empresa no salgan de la Mac. Es más lento que Gemini, así que
se usa como respaldo (o como preferencia explícita vía DOC_CONVERTER_LLM=local).

Configuración por variables de entorno:
    OLLAMA_HOST          (default http://127.0.0.1:11434)
    OLLAMA_MODEL         (default qwen2.5:7b-instruct)
    DOC_CONVERTER_LLM    gemini | local | auto (default auto: Gemini y si falla, local)
"""

import json
import os
import re
import urllib.error
import urllib.request

_DEFAULT_HOST = 'http://127.0.0.1:11434'
_DEFAULT_MODEL = 'qwen2.5:7b-instruct'
_TIMEOUT = 900
# Los modelos con "razonamiento" devuelven su monólogo interno; se descarta.
_THINK_BLOCK = re.compile(r'<think>.*?</think>\s*', re.DOTALL | re.IGNORECASE)


def host() -> str:
    return os.getenv('OLLAMA_HOST', _DEFAULT_HOST).rstrip('/')


def model() -> str:
    return os.getenv('OLLAMA_MODEL', _DEFAULT_MODEL)


def is_available() -> bool:
    try:
        with urllib.request.urlopen(f'{host()}/api/tags', timeout=2) as r:
            tags = json.loads(r.read())
    except Exception:
        return False
    return any(m.get('name') == model() for m in tags.get('models', []))


def structure(text: str, prompt: str) -> str:
    payload = json.dumps({
        'model': model(),
        'prompt': f'{prompt}\n\n---\n\n{text}',
        'stream': False,
        'think': False,
        'options': {
            'temperature': 0,
            'num_ctx': 16384,
            # Tope de generación: la salida no debe exceder mucho a la entrada.
            # Sin él, un modelo que se descarrila genera miles de tokens.
            'num_predict': min(8192, max(1024, len(text) // 2)),
        },
    }).encode()
    req = urllib.request.Request(
        f'{host()}/api/generate', data=payload, headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        response = json.loads(r.read()).get('response', '')
    return _THINK_BLOCK.sub('', response).strip()
