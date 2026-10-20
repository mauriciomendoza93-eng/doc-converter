"""
batch.py — Lotes de conversión para el CLI/Finder.

- plan():  expande archivos y carpetas (recursivo por defecto) en una lista de
           trabajos, con carpeta de salida y nombres sin colisiones.
- run():   ejecuta los trabajos en procesos worker (backend.jobs) en paralelo,
           con tiempo límite por archivo; un archivo que se cuelga o cae no
           afecta al resto.
- Para carpetas escribe `_informe.json` en la carpeta de salida: sirve de
  auditoría (qué se convirtió, con qué método, advertencias) y de manifiesto
  para no reconvertir lo que no cambió.
"""

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from backend.jobs import ALREADY_TEXT_EXTS, SUPPORTED_EXTS

PROJECT_DIR = Path(__file__).resolve().parent.parent
REPORT_NAME = '_informe.json'
REPORT_VERSION = 1
DEFAULT_TIMEOUT = 900
_SKIP_DIRS = {'node_modules', '__pycache__'}
_SKIP_DIR_SUFFIXES = ('.app', '.photoslibrary', '.bundle', '.framework', '.xcodeproj', '.xcworkspace', '.rtfd', '.pages', '.numbers', '.key')
_SKIP_FILES = {'.DS_Store', 'Thumbs.db', 'desktop.ini', REPORT_NAME}


@dataclass
class Item:
    src: Path
    out_dir: Path
    rel: str                       # ruta relativa al origen (para el informe)
    from_folder: bool
    collide: bool = False          # otro archivo del mismo directorio comparte nombre base
    sha256: str = ''
    result: dict = field(default_factory=dict)


@dataclass
class Plan:
    items: list
    ignored: Counter               # razón/extensión → cantidad
    out_roots: list                # carpetas de salida de las carpetas seleccionadas
    missing: list                  # rutas que no existen


def _walk_folder(root: Path, recursive: bool):
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            d for d in dirnames
            if not d.startswith('.') and d not in _SKIP_DIRS and not d.endswith('_convertida')
            and not d.lower().endswith(_SKIP_DIR_SUFFIXES) and not os.path.islink(os.path.join(dirpath, d))
        )
        if not recursive and Path(dirpath) != root:
            dirnames[:] = []
            continue
        for name in sorted(filenames):
            yield Path(dirpath) / name


def plan(paths: list, recursive: bool = True) -> Plan:
    items, ignored, out_roots, missing = [], Counter(), [], []
    seen = set()
    for raw in paths:
        p = Path(raw).expanduser()
        if not p.exists():
            missing.append(str(raw))
            continue
        p = p.resolve()
        if p.is_dir():
            out_root = p.with_name(f'{p.name}_convertida')
            out_roots.append(out_root)
            for f in _walk_folder(p, recursive):
                name = f.name
                if name.startswith('.') or name.startswith('~$') or name in _SKIP_FILES or not f.is_file():
                    continue
                ext = f.suffix.lower()
                if ext in ALREADY_TEXT_EXTS:
                    ignored['ya es texto'] += 1
                elif ext not in SUPPORTED_EXTS:
                    ignored[ext or 'sin extensión'] += 1
                elif f not in seen:
                    seen.add(f)
                    rel = f.relative_to(p)
                    items.append(Item(f, out_root / rel.parent, str(rel), True))
        elif p not in seen:
            seen.add(p)
            items.append(Item(p, p.parent, p.name, False))

    groups = Counter((it.out_dir, it.src.stem.lower()) for it in items)
    for it in items:
        it.collide = groups[(it.out_dir, it.src.stem.lower())] > 1
    return Plan(items, ignored, out_roots, missing)


def unique_path(path: Path) -> Path:
    candidate, n = path, 1
    while candidate.exists():
        candidate = path.with_name(f'{path.stem}_{n}{path.suffix}')
        n += 1
    return candidate


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


# --- Ejecución ---------------------------------------------------------------

def _run_worker(item: Item, engine: str, route: str, timeout: int) -> dict:
    scratch = tempfile.mkdtemp(prefix='doc-converter-')
    job = json.dumps({'path': str(item.src), 'engine': engine, 'route': route, 'scratch': scratch})
    proc = subprocess.Popen(
        [sys.executable, '-m', 'backend.jobs'],
        cwd=PROJECT_DIR, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True, text=True,
    )
    try:
        out, err = proc.communicate(job, timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        shutil.rmtree(scratch, ignore_errors=True)
        return {'ok': False, 'error': f'tiempo límite de {timeout}s superado; se canceló este archivo', 'seconds': timeout}
    lines = [ln for ln in out.splitlines() if ln.strip()]
    try:
        result = json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError):
        tail = (err or '').strip().splitlines()[-1:] or ['sin detalle']
        shutil.rmtree(scratch, ignore_errors=True)
        return {'ok': False, 'error': f'el proceso de conversión terminó de forma inesperada (código {proc.returncode}): {tail[0][:200]}', 'seconds': 0}
    result['scratch'] = scratch
    return result


def _final_name(item: Item, name: str) -> str:
    """Si dos archivos comparten nombre base (memo.docx / memo.rtf), el Markdown
    conserva la extensión original (memo.docx.md) en vez de pisarse o numerarse."""
    if item.collide and Path(name).stem == item.src.stem:
        return f'{item.src.name}{Path(name).suffix}'
    return name


def _place(item: Item, result: dict, owned: set) -> list:
    """Mueve las salidas del worker a su destino. Nunca sobrescribe archivos
    ajenos; sí reemplaza los que este mismo flujo generó antes (`owned`)."""
    item.out_dir.mkdir(parents=True, exist_ok=True)
    placed = []
    for f in result.get('files', []):
        dest = item.out_dir / os.path.basename(_final_name(item, f['name']))
        if dest.exists() and str(dest) not in owned:
            dest = unique_path(dest)
        shutil.move(f['tmp'], dest)
        placed.append(dest)
    shutil.rmtree(result.pop('scratch', ''), ignore_errors=True)
    return placed


def _load_report(out_root: Path) -> dict:
    try:
        data = json.loads((out_root / REPORT_NAME).read_text())
        if data.get('version') == REPORT_VERSION:
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {'files': {}}


def run(plan_: Plan, engine: str, route: str, jobs: int = 3, timeout: int = DEFAULT_TIMEOUT,
        force: bool = False, log=print, err=None) -> dict:
    """Ejecuta el plan. Retorna {'converted', 'skipped', 'errors', 'seconds', 'rows'}."""
    err = err or (lambda m: print(m, file=sys.stderr))
    started = time.monotonic()
    total = len(plan_.items)
    lock = threading.Lock()
    counter = {'done': 0}
    stats = {'converted': 0, 'skipped': 0, 'unchanged': 0, 'errors': 0}

    reports = {root: _load_report(root) for root in plan_.out_roots}
    owned_by_root = {root: {str(root / o) for e in rep['files'].values() for o in e.get('outputs', [])}
                     for root, rep in reports.items()}

    def root_of(item: Item):
        for root in plan_.out_roots:
            if item.out_dir == root or root in item.out_dir.parents:
                return root
        return None

    pending = []
    for item in plan_.items:
        root = root_of(item) if item.from_folder else None
        if root is not None:
            item.sha256 = sha256_of(item.src)
            prev = reports[root]['files'].get(item.rel)
            if (not force and prev and prev.get('status') == 'ok' and prev.get('sha256') == item.sha256
                    and prev.get('engine') == engine and prev.get('route') == route
                    and all((root / o).exists() for o in prev.get('outputs', []))):
                item.result = {'status': 'unchanged', 'outputs': prev.get('outputs', []), 'method': prev.get('method', ''),
                               'warnings': prev.get('warnings', []), 'seconds': 0,
                               'pages': prev.get('pages', 0), 'ocr_pages': prev.get('ocr_pages', 0)}
                stats['unchanged'] += 1
                counter['done'] += 1
                log(f'[{counter["done"]}/{total}] {item.src.name} = sin cambios, se omitió')
                continue
        pending.append(item)

    def finish(item: Item, result: dict):
        root = root_of(item) if item.from_folder else None
        with lock:
            counter['done'] += 1
            n = counter['done']
            secs = result.get('seconds', 0)
            if result.get('ok') and result.get('skipped'):
                stats['skipped'] += 1
                item.result = {'status': 'skipped', 'note': result['skipped'], 'seconds': secs}
                log(f'[{n}/{total}] {item.src.name} = omitido: {result["skipped"]}')
                shutil.rmtree(result.get('scratch', ''), ignore_errors=True)
                return
            if not result.get('ok'):
                stats['errors'] += 1
                item.result = {'status': 'error', 'error': result.get('error', 'error desconocido'), 'seconds': secs}
                err(f'Error con {item.src.name}: {item.result["error"]}')
                shutil.rmtree(result.get('scratch', ''), ignore_errors=True)
                return
            owned = owned_by_root.get(root, set()) if root else set()
            try:
                placed = _place(item, result, owned)
            except OSError as e:
                stats['errors'] += 1
                item.result = {'status': 'error', 'error': f'no se pudo escribir el resultado: {e}', 'seconds': secs}
                err(f'Error con {item.src.name}: {item.result["error"]}')
                return
            stats['converted'] += 1
            base = root if root else item.out_dir
            item.result = {
                'status': 'ok', 'method': result.get('method', ''), 'seconds': secs,
                'outputs': [str(p.relative_to(base)) for p in placed], 'warnings': result.get('warnings', []),
                'pages': result.get('pages', 0), 'ocr_pages': result.get('ocr_pages', 0),
                'structured_pages': result.get('structured_pages', 0),
            }
            shown = ', '.join(p.name for p in placed)
            extra = f', {result["pages"]} pág' if result.get('pages') else ''
            log(f'[{n}/{total}] {item.src.name} -> {shown} [{result.get("method", "")}{extra}] {secs:.1f}s')
            for w in result.get('warnings', []):
                log(f'    ! {w}')

    def work(item: Item):
        finish(item, _run_worker(item, engine, route, timeout))

    if pending:
        with ThreadPoolExecutor(max_workers=max(1, min(jobs, len(pending)))) as pool:
            list(pool.map(work, pending))

    # Informe/manifiesto por carpeta seleccionada.
    for root in plan_.out_roots:
        files = {}
        for it in plan_.items:
            if not it.from_folder or root_of(it) != root or not it.result:
                continue
            r = it.result
            files[it.rel] = {
                'sha256': it.sha256, 'size': it.src.stat().st_size, 'engine': engine, 'route': route,
                'status': 'ok' if r['status'] in ('ok', 'unchanged') else r['status'],
                'outputs': r.get('outputs', []), 'method': r.get('method', ''),
                'warnings': r.get('warnings', []), 'error': r.get('error', ''), 'note': r.get('note', ''),
                'seconds': r.get('seconds', 0), 'pages': r.get('pages', 0), 'ocr_pages': r.get('ocr_pages', 0),
            }
        if files:
            root.mkdir(parents=True, exist_ok=True)
            report = {'version': REPORT_VERSION, 'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
                      'engine': engine, 'route': route, 'files': files}
            tmp = root / (REPORT_NAME + '.tmp')
            tmp.write_text(json.dumps(report, ensure_ascii=False, indent=1))
            os.replace(tmp, root / REPORT_NAME)

    stats['seconds'] = round(time.monotonic() - started, 1)
    return stats
