"""
store.py — Almacén local de archivos y registro de eventos de solo-anexado.

Estructura en DATA_DIR:
    originals/<sha256>/<nombre>        inmutable (solo lectura), deduplicado por hash
    outputs/<id>/<base>-<op>-v<n>.<ext> una carpeta por resultado
    outputs/<id>/meta.json             operación, hashes de origen y salida, caducidad
    events.jsonl                       registro si no hay DATABASE_URL

Con DATABASE_URL los eventos van a PostgreSQL (tabla con trigger que impide
UPDATE/DELETE); si la base no responde se usa events.jsonl como respaldo.
"""

import hashlib
import json
import os
import shutil
import stat
import threading
import uuid
from datetime import datetime, timedelta, timezone

from backend.utils.http import safe_filename

DATA_DIR = os.getenv('DATA_DIR', '')
EXPIRY_DAYS = float(os.getenv('EXPIRY_DAYS', '30'))
DATABASE_URL = os.getenv('DATABASE_URL', '')

_lock = threading.Lock()
_db_ready = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    action      TEXT NOT NULL,
    outcome     TEXT NOT NULL,
    input_sha256  TEXT[] NOT NULL DEFAULT '{}',
    output_sha256 TEXT,
    output_id   TEXT,
    detail      JSONB NOT NULL DEFAULT '{}'
);
CREATE OR REPLACE FUNCTION events_append_only() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'events es de solo-anexado'; END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS events_no_change ON events;
CREATE TRIGGER events_no_change BEFORE UPDATE OR DELETE OR TRUNCATE ON events
    FOR EACH STATEMENT EXECUTE FUNCTION events_append_only();
"""


def enabled() -> bool:
    return bool(DATA_DIR)


def _dir(*parts: str) -> str:
    path = os.path.join(DATA_DIR, *parts)
    os.makedirs(path, exist_ok=True)
    return path


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Eventos ────────────────────────────────────────────────────────────────

def _db():
    global _db_ready
    import psycopg  # solo en el servidor
    conn = psycopg.connect(DATABASE_URL, connect_timeout=5, autocommit=True)
    if not _db_ready:
        conn.execute(SCHEMA)
        _db_ready = True
    return conn


def log_event(action: str, outcome: str, inputs: list[str] | None = None,
              output_sha: str | None = None, output_id: str | None = None, **detail) -> None:
    row = {'ts': _now().isoformat(), 'action': action, 'outcome': outcome,
           'input_sha256': inputs or [], 'output_sha256': output_sha,
           'output_id': output_id, 'detail': detail}
    if DATABASE_URL:
        try:
            with _db() as conn:
                conn.execute(
                    'INSERT INTO events (action, outcome, input_sha256, output_sha256, output_id, detail) '
                    'VALUES (%s, %s, %s, %s, %s, %s)',
                    (action, outcome, row['input_sha256'], output_sha, output_id, json.dumps(detail)))
            return
        except Exception as e:  # base no disponible: no perder el evento
            row['detail']['db_error'] = str(e)[:200]
    if not DATA_DIR:
        return
    with _lock, open(os.path.join(_dir(), 'events.jsonl'), 'a', encoding='utf-8') as f:
        f.write(json.dumps(row, ensure_ascii=False) + '\n')


def read_events(limit: int = 200) -> list[dict]:
    if DATABASE_URL:
        try:
            with _db() as conn:
                cur = conn.execute(
                    'SELECT ts, action, outcome, input_sha256, output_sha256, output_id, detail '
                    'FROM events ORDER BY id DESC LIMIT %s', (limit,))
                cols = [c.name for c in cur.description]
                return [dict(zip(cols, (v.isoformat() if isinstance(v, datetime) else v for v in r)))
                        for r in cur.fetchall()]
        except Exception:
            pass
    path = os.path.join(DATA_DIR, 'events.jsonl')
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as f:
        lines = f.readlines()[-limit:]
    return [json.loads(l) for l in reversed(lines)]


# ── Archivos ───────────────────────────────────────────────────────────────

def save_original(data: bytes, filename: str) -> str:
    """Guarda el original una sola vez (solo lectura). Devuelve su sha256."""
    digest = sha256(data)
    folder = _dir('originals', digest)
    path = os.path.join(folder, safe_filename(filename))
    if not os.listdir(folder):
        with open(path, 'wb') as f:
            f.write(data)
        os.chmod(path, stat.S_IRUSR | stat.S_IRGRP)
    return digest


def save_output(data: bytes, op: str, source_name: str, ext: str, inputs: list[str], **extra) -> dict:
    """Escribe un resultado nuevo, versionado por (original, operación)."""
    base = os.path.splitext(safe_filename(source_name))[0][:80]
    with _lock:
        prior = [m for m in list_outputs(include_expired=True)
                 if m['op'] == op and m['inputs'][:1] == inputs[:1]]
        version = 1 + max((m['version'] for m in prior), default=0)
        out_id = uuid.uuid4().hex[:12]
        folder = _dir('outputs', out_id)
        name = f'{base}-{op}-v{version}{ext}'
        with open(os.path.join(folder, name), 'wb') as f:
            f.write(data)
        meta = {'id': out_id, 'op': op, 'version': version, 'filename': name,
                'size': len(data), 'sha256': sha256(data), 'inputs': inputs,
                'created': _now().isoformat(),
                'expires': (_now() + timedelta(days=EXPIRY_DAYS)).isoformat(), **extra}
        with open(os.path.join(folder, 'meta.json'), 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    log_event(op, 'success', inputs, meta['sha256'], out_id, filename=name, **extra)
    return meta


def _read_meta(out_id: str) -> dict | None:
    if not out_id.isalnum():
        return None
    try:
        with open(os.path.join(DATA_DIR, 'outputs', out_id, 'meta.json'), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def list_outputs(include_expired: bool = False) -> list[dict]:
    root = os.path.join(DATA_DIR, 'outputs')
    if not os.path.isdir(root):
        return []
    now = _now().isoformat()
    metas = [m for m in (_read_meta(i) for i in os.listdir(root)) if m]
    if not include_expired:
        metas = [m for m in metas if m['expires'] > now]
    return sorted(metas, key=lambda m: m['created'], reverse=True)


def output_path(out_id: str) -> tuple[dict, str] | None:
    meta = _read_meta(out_id)
    if not meta:
        return None
    return meta, os.path.join(DATA_DIR, 'outputs', out_id, meta['filename'])


def delete_output(out_id: str, reason: str = 'user') -> bool:
    meta = _read_meta(out_id)
    if not meta:
        return False
    shutil.rmtree(os.path.join(DATA_DIR, 'outputs', out_id), ignore_errors=True)
    log_event('delete', 'success', meta['inputs'], meta['sha256'], out_id, reason=reason)
    return True


def delete_original(digest: str) -> bool:
    """Borrado explícito del original (lo pide el usuario; no lo hace la caducidad)."""
    if not (len(digest) == 64 and all(c in '0123456789abcdef' for c in digest)):
        return False
    folder = os.path.join(DATA_DIR, 'originals', digest)
    if not os.path.isdir(folder):
        return False
    for name in os.listdir(folder):
        os.chmod(os.path.join(folder, name), stat.S_IWUSR | stat.S_IRUSR)
    shutil.rmtree(folder)
    log_event('delete_original', 'success', [digest])
    return True


def purge_expired() -> int:
    """Borra resultados vencidos. Los originales se conservan."""
    now = _now().isoformat()
    expired = [m['id'] for m in list_outputs(include_expired=True) if m['expires'] <= now]
    for out_id in expired:
        delete_output(out_id, reason='expired')
    return len(expired)
