"""Camino feliz completo: subir → unir → descargar → auditar → borrar."""
import hashlib
import os
import stat

import pytest
from fastapi.testclient import TestClient

from backend import store
from backend.main import app
from tests.conftest import make_pdf


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(store, 'DATABASE_URL', '')
    return TestClient(app)


def test_merge_happy_path(client, tmp_path):
    a, b = make_pdf(['a1', 'a2']), make_pdf(['b1'])
    r = client.post('/api/pdf/merge', files=[('files', ('Año 1.pdf', a, 'application/pdf')),
                                             ('files', ('b.pdf', b, 'application/pdf'))])
    assert r.status_code == 200, r.text
    out = r.json()['outputs'][0]
    assert out['filename'] == 'Año 1-merge-v1.pdf' and out['version'] == 1

    # Original inmutable y deduplicado por hash
    orig = tmp_path / 'originals' / hashlib.sha256(a).hexdigest() / 'Año 1.pdf'
    assert orig.read_bytes() == a and not os.stat(orig).st_mode & stat.S_IWUSR

    # Segunda ejecución: nueva versión, la primera intacta
    r2 = client.post('/api/pdf/merge', files=[('files', ('Año 1.pdf', a, 'application/pdf')),
                                              ('files', ('b.pdf', b, 'application/pdf'))])
    assert r2.json()['outputs'][0]['version'] == 2

    dl = client.get(f"/api/files/{out['id']}")
    assert dl.status_code == 200 and hashlib.sha256(dl.content).hexdigest() == out['sha256']
    assert "filename*=UTF-8''A%C3%B1o" in dl.headers['content-disposition']

    assert client.delete(f"/api/files/{out['id']}").status_code == 200
    assert client.get(f"/api/files/{out['id']}").status_code == 404

    actions = [(e['action'], e['outcome']) for e in client.get('/api/events').json()]
    assert ('merge', 'success') in actions and ('download', 'delivered') in actions and ('delete', 'success') in actions
    assert client.get('/api/export').status_code == 200


def test_recoverable_errors(client):
    bad = client.post('/api/pdf/rotate', files=[('files', ('x.pdf', b'no soy pdf', 'application/pdf'))])
    assert bad.status_code == 415
    rng = client.post('/api/pdf/split', data={'pages': '9'},
                      files=[('files', ('x.pdf', make_pdf(['1']), 'application/pdf'))])
    assert rng.status_code == 422 and 'fuera de rango' in rng.json()['detail']
    assert client.post('/convert', files={'file': ('a.exe', b'MZ', 'application/octet-stream')}).status_code == 415


def test_expired_outputs_are_purged(client, monkeypatch):
    monkeypatch.setattr(store, 'EXPIRY_DAYS', -1)
    client.post('/api/pdf/compress', files=[('files', ('c.pdf', make_pdf(['c']), 'application/pdf'))])
    assert client.get('/api/files').json() == []


def test_tools_disabled_without_store(monkeypatch):
    monkeypatch.setattr(store, 'DATA_DIR', '')
    assert TestClient(app).get('/api/files').status_code == 503
