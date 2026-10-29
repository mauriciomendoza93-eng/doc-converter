"""
vlmocr.py — OCR con un modelo de visión local (GLM-OCR vía MLX) en Apple Silicon.

Corre en su propio entorno (.venv-ocr, ver setup.sh) porque mlx-vlm exige versiones
de fastapi/transformers incompatibles con las de la web. El motor lo llama como
proceso aparte (backend/engines/vlm_ocr.py):

    echo '{"images": ["/tmp/a.png", ...]}' | .venv-ocr/bin/python cli/vlmocr/vlmocr.py

Por cada imagen escribe una línea JSON en stdout: {"i": 0, "text": "...", "seconds": 12.3,
"tokens": 1520, "truncated": false}. El modelo se carga una sola vez por llamada.
Un candado de archivo serializa el uso de la GPU entre los workers del lote: dos
modelos a la vez no van más rápido y duplican la memoria.

    .venv-ocr/bin/python cli/vlmocr/vlmocr.py --warm-up    descarga y prueba el modelo
"""

import fcntl
import json
import os
import sys
import time

MODEL = os.environ.get('DOC_CONVERTER_OCR_MODEL', 'mlx-community/GLM-OCR-8bit')
PROMPT = 'Text Recognition:'
# Una página densa ronda 1 500-2 500 tokens; más que esto es un bucle.
MAX_TOKENS = 5000
# A más resolución, más tokens de imagen y más lento, sin mejor lectura: ~150 ppp en A4/carta.
MAX_SIDE = 2000
LOCK_PATH = os.path.join(os.environ.get('TMPDIR', '/tmp'), 'doc-converter-vlmocr.lock')
os.environ.setdefault('HF_HUB_DISABLE_PROGRESS_BARS', '1')


def _prepare(path: str, tmp_dir: str, i: int) -> str:
    from PIL import Image
    img = Image.open(path).convert('RGB')
    if max(img.size) <= MAX_SIDE:
        return path
    img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    out = os.path.join(tmp_dir, f'vlm-{i:04d}.png')
    img.save(out)
    return out


def run(images: list) -> None:
    import tempfile
    from mlx_vlm import generate, load
    from mlx_vlm.prompt_utils import apply_chat_template

    with open(LOCK_PATH, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        model, processor = load(MODEL)
        prompt = apply_chat_template(processor, model.config, PROMPT, num_images=1)
        with tempfile.TemporaryDirectory() as tmp:
            for i, path in enumerate(images):
                started = time.monotonic()
                r = generate(model, processor, prompt, image=[_prepare(path, tmp, i)],
                             max_tokens=MAX_TOKENS, temperature=0.0, verbose=False)
                print(json.dumps({
                    'i': i, 'text': r.text, 'seconds': round(time.monotonic() - started, 1),
                    'tokens': r.generation_tokens, 'truncated': r.generation_tokens >= MAX_TOKENS,
                }, ensure_ascii=False), flush=True)


def warm_up() -> None:
    from PIL import Image, ImageDraw
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'warm.png')
        img = Image.new('RGB', (600, 160), 'white')
        ImageDraw.Draw(img).text((20, 60), 'Doc Converter OCR 2026', fill='black')
        img.save(path)
        run([path])


if __name__ == '__main__':
    if '--warm-up' in sys.argv:
        warm_up()
    else:
        # Convertir no toca la red: el modelo ya se descargó al instalar (setup.sh).
        os.environ.setdefault('HF_HUB_OFFLINE', '1')
        run(json.load(sys.stdin)['images'])
