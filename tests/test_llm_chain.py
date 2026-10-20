"""Cadena de modelos (llm_router), estructurado con validación y extractos por texto."""
import json
import urllib.error

import pytest

from backend.engines import llm_router, statement_extractor, structured_ocr, text_structurer
from backend.engines.llm_router import Candidate, LLMRouter, LLMUnavailable


@pytest.fixture
def router(monkeypatch, tmp_path):
    """Router con salud aislada y claves ficticias; los transportes se sustituyen en cada test."""
    monkeypatch.setattr(llm_router, 'HEALTH_FILE', tmp_path / 'health.json')
    monkeypatch.setenv('OMNIROUTE_API_KEY', 'test')
    monkeypatch.setenv('GEMINI_API_KEY', 'test')
    monkeypatch.setattr(llm_router, '_omniroute_up', lambda base: True)
    chain = ['omniroute:m1', 'omniroute:m2', 'gemini:g1']
    r = LLMRouter(text_chain=chain, vision_chain=chain)
    monkeypatch.setattr(llm_router, '_router', r)
    return r


def _http_error(code, body=''):
    import io
    return urllib.error.HTTPError('http://x', code, 'err', {}, io.BytesIO(body.encode()))


def test_falls_back_when_a_model_fails_and_remembers_it(router, monkeypatch):
    calls = []

    def fake(model, *a, **k):
        calls.append(model)
        if model == 'm1':
            raise _http_error(401, 'authentication expired')
        return 'respuesta'

    monkeypatch.setattr(llm_router, '_chat_omniroute', fake)
    reply = router.complete('hola')
    assert reply.model == 'omniroute:m2' and calls == ['m1', 'm2']
    assert llm_router.cooldown_remaining('omniroute:m1') > 5 * 3600  # credenciales vencidas: horas

    calls.clear()
    router.complete('otra vez')
    assert calls == ['m2']  # m1 ya no se vuelve a intentar


def test_rejected_output_tries_next_model_without_penalty(router, monkeypatch):
    monkeypatch.setattr(llm_router, '_chat_omniroute', lambda model, *a, **k: 'corto' if model == 'm1' else 'texto completo y bueno')
    reply = router.complete('x', validate=lambda t: True if 'bueno' in t else 'resumió')
    assert reply.model == 'omniroute:m2'
    assert llm_router.cooldown_remaining('omniroute:m1') == 0
    assert any('resumió' in why for _, why in reply.attempts)


def test_all_failing_raises_with_reasons(router, monkeypatch):
    monkeypatch.setattr(llm_router, '_chat_omniroute', lambda *a, **k: (_ for _ in ()).throw(_http_error(429)))
    monkeypatch.setattr(llm_router, '_chat_gemini', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('429 RESOURCE_EXHAUSTED')))
    with pytest.raises(LLMUnavailable) as e:
        router.complete('x')
    assert 'quota' in str(e.value)


def test_omniroute_down_skips_straight_to_gemini(router, monkeypatch):
    monkeypatch.setattr(llm_router, '_omniroute_up', lambda base: False)
    monkeypatch.setattr(llm_router, '_chat_gemini', lambda model, *a, **k: 'de gemini')
    reply = router.complete('x')
    assert reply.model == 'gemini:g1'


def test_no_keys_means_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_router, 'HEALTH_FILE', tmp_path / 'h.json')
    monkeypatch.delenv('OMNIROUTE_API_KEY', raising=False)
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    monkeypatch.setattr(llm_router, '_load_dotenv', lambda: None)
    r = LLMRouter(text_chain=['omniroute:m1', 'gemini:g1'])
    assert not r.available()
    with pytest.raises(LLMUnavailable):
        r.complete('x')


@pytest.mark.parametrize('exc,kind', [
    (_http_error(401), 'auth'), (_http_error(403), 'auth'), (_http_error(429), 'quota'),
    (_http_error(504), 'timeout'), (_http_error(502), 'down'),
    (_http_error(400, "Model 'x' is not available in the active live catalog"), 'missing'),
    (RuntimeError('503 UNAVAILABLE'), 'down'), (RuntimeError('429 RESOURCE_EXHAUSTED'), 'quota'),
    (TimeoutError('timed out'), 'timeout'),
])
def test_failure_classification(exc, kind):
    assert llm_router.classify(exc) == kind


# --- Validación del estructurado ---------------------------------------------------------

SOURCE = 'Total 7,740.00 pagado el 14/03/1985 por Juan Pérez, NIT 1023456028, cuenta 1000-123456-7. ' * 3


def test_validator_accepts_faithful_output():
    check = text_structurer.validator(SOURCE)
    assert check('| Campo | Valor |\n|---|---|\n' + SOURCE) is True


def test_validator_rejects_summaries_lost_text_and_changed_figures():
    check = text_structurer.validator(SOURCE)
    assert check('Resumen: pagos de Juan.') != True  # noqa: E712
    assert 'perdió' in check('Total pagado por Juan ' * 5) or 'resum' in check('Total pagado por Juan ' * 5)
    assert 'cifras' in check(SOURCE.replace('7,740.00', '7.740.00'))      # coma→punto
    assert 'cifras' in check(SOURCE.replace('1023456028', '1023456029'))  # un dígito


def test_validator_tolerates_list_numbering():
    numbered = '\n'.join(f'{n}. punto número {n}' for n in range(1, 13))
    out = '\n'.join(f'{n}. punto número {n}' for n in range(1, 13))
    assert text_structurer.validator(numbered)(out) is True


def test_structure_markdown_keeps_chunk_raw_when_every_model_fails(router, monkeypatch):
    text = ('Línea con datos 12345.\n\n' * 900)  # varios fragmentos
    assert len(text_structurer._split_text(text)) > 1
    seen = {'n': 0}

    def fake(model, system, user, *a, **k):
        seen['n'] += 1
        if 'datos' in user and seen['n'] % 2 == 0:
            raise _http_error(502)
        return user

    monkeypatch.setattr(llm_router, '_chat_omniroute', fake)
    monkeypatch.setattr(llm_router, '_chat_gemini', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('503')))
    md, method, warnings = text_structurer.structure_markdown(text)
    assert md.count('datos 12345') == 900 and method.startswith('ia (')


# --- Extractos por texto ---------------------------------------------------------------------

STATEMENT = """BNB - BANCO NACIONAL DE BOLIVIA - ESTADO DE CUENTA
| Fecha | Descripcion | Debito | Credito | Saldo |
|---|---|---|---|---|
| 02/03/2026 | Abono alquiler | | 3,500.00 | 18,420.15 |
| 04/03/2026 | Compra Hipermaxi | 287.40 | | 18,132.75 |
"""


def _fake_json(movs, banco='BNB'):
    return json.dumps({'banco': banco, 'transacciones': movs})


def test_statement_extraction_verifies_amounts_against_text(router, monkeypatch):
    movs = [{'fecha': '2026-03-02', 'descripcion': 'Abono alquiler', 'monto': 3500.0},
            {'fecha': '2026-03-04', 'descripcion': 'Compra Hipermaxi', 'monto': -287.40}]
    monkeypatch.setattr(llm_router, '_chat_omniroute', lambda *a, **k: _fake_json(movs))
    data = statement_extractor.extract(STATEMENT)
    assert [t['monto'] for t in data['transacciones']] == [3500.0, -287.4] and data['banco'] == 'BNB'


def test_statement_extraction_drops_a_few_unverifiable_rows_and_reports_them(router, monkeypatch):
    rows = [{'fecha': f'2026-03-{d:02d}', 'descripcion': f'Mov {d}', 'monto': -(100.0 + d)} for d in range(1, 11)]
    text = '\n'.join(f'{r["fecha"]} {r["descripcion"]} {abs(r["monto"]):.2f}' for r in rows)
    bad = {'fecha': '2026-03-11', 'descripcion': 'Inventado', 'monto': -999.99}
    monkeypatch.setattr(llm_router, '_chat_omniroute', lambda *a, **k: _fake_json(rows + [bad]))
    data = statement_extractor.extract(text)
    assert len(data['transacciones']) == 10 and any('descartado' in w and 'Inventado' in w for w in data['warnings'])


def test_statement_with_untrustworthy_extraction_raises(router, monkeypatch):
    movs = [{'fecha': '2026-03-04', 'descripcion': 'Compra', 'monto': -287.40},
            {'fecha': '2026-03-05', 'descripcion': 'Inventado', 'monto': -999.99}]
    monkeypatch.setattr(llm_router, '_chat_omniroute', lambda *a, **k: _fake_json(movs))
    with pytest.raises(ValueError, match='verificar'):
        statement_extractor.extract(STATEMENT)


def test_statement_bank_name_must_exist_in_document(router, monkeypatch):
    movs = [{'fecha': '2026-03-04', 'descripcion': 'Compra', 'monto': -287.40}]
    monkeypatch.setattr(llm_router, '_chat_omniroute', lambda *a, **k: _fake_json(movs, banco='CO C O O S O CU'))
    assert statement_extractor.extract('Estado de cuenta\n287.40 compra')['banco'] == ''


def test_inconsistent_amounts_are_flagged():
    assert structured_ocr.suspicious_numbers('importe 7.740.00 y 1,234.50 y 15.117.65') == ['7.740.00', '15.117.65']


def test_model_rejected_repeatedly_is_demoted_then_forgiven_on_success(router, monkeypatch):
    monkeypatch.setattr(llm_router, '_chat_omniroute', lambda model, *a, **k: 'mala' if model == 'm1' else 'buena')
    check = lambda t: True if t == 'buena' else 'perdió texto'
    for _ in range(llm_router._REJECTS_BEFORE_DEMOTION):
        router.complete('x', validate=check)
    assert llm_router.cooldown_remaining('omniroute:m1') > 20 * 60  # al final de la cola un rato

    # un acierto borra el historial de rechazos
    llm_router.mark_rejected('omniroute:m2')
    llm_router.mark_ok('omniroute:m2')
    assert 'omniroute:m2' not in llm_router._read_health().get('_rejects', {})
