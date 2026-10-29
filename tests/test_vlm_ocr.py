import os

import pytest
from PIL import Image, ImageDraw, ImageFont

from backend.engines import local_converter, vlm_ocr

PLAIN = ('El préstamo será entregado al DEUDOR mediante un desembolso total de Bs 2.060.000,00 '
         'con el que se perfeccionará el contrato, o mediante desembolsos parciales.')


def test_judge_accepts_a_faithful_reading():
    fixed = PLAIN.replace('desembolso total', 'desembolso total')
    text, why = vlm_ocr.judge({'text': fixed, 'truncated': False}, PLAIN.replace('mediante', 'edame'))
    assert text == fixed and why == ''


def test_judge_rejects_loops_truncation_omissions_and_additions():
    loop = ' '.join(['el BANCO se reserva el derecho de efectuar el desembolso'] * 6)
    assert vlm_ocr.judge({'text': loop}, loop)[1] == 'repetía texto en bucle'
    assert vlm_ocr.judge({'text': PLAIN, 'truncated': True}, PLAIN)[1] == 'repetía texto en bucle'
    assert 'omitía texto' in vlm_ocr.judge({'text': PLAIN[:40]}, PLAIN)[1]
    invented = PLAIN + ' ' + 'Cláusula adicional sobre garantías hipotecarias y prendarias del deudor. ' * 3
    assert 'agregaba texto' in vlm_ocr.judge({'text': invented}, PLAIN)[1]
    assert vlm_ocr.judge(None, PLAIN) == (None, 'sin resultado')


def test_clean_unwraps_fences_and_html_tables():
    raw = '```markdown\nTotales\n<table><tr><th>Concepto</th><th>Monto</th></tr><tr><td>Ventas</td><td>1,234.50</td></tr></table>\n```'
    out = vlm_ocr.clean(raw)
    assert out.startswith('Totales') and '| Ventas | 1,234.50 |' in out and '```' not in out


def test_missing_numbers_reports_digits_the_model_dropped():
    assert vlm_ocr.missing_numbers('Testimonio 1565/2026 por Bs 2.060.000,00', 'Testimonio por Bs 2.060.000,00') == ['1565', '2026']


def _page():
    img = Image.new('RGB', (900, 200), 'white')
    ImageDraw.Draw(img).text((40, 80), 'Contrato de prestamo 2026', fill='black',
                             font=ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', 40))
    return img


def test_read_images_falls_back_to_vision_per_page(monkeypatch):
    img = _page()
    monkeypatch.setattr(vlm_ocr, 'cloud_available', lambda: False)
    monkeypatch.setattr(vlm_ocr, 'available', lambda: True)
    monkeypatch.setattr(vlm_ocr, 'recognize', lambda paths: [
        {'text': 'Contrato de préstamo 2026'}, {'text': 'x', 'truncated': True}])
    good, bad = local_converter.read_images([img, img])
    assert good.model == 'glm-ocr' and good.text == 'Contrato de préstamo 2026'
    assert not bad.model and 'prestamo' in bad.text and bad.rejected == ['glm-ocr: repetía texto en bucle']
    warns = local_converter.model_warnings([good, bad], ['pág. 1', 'pág. 2'])
    assert warns == ['OCR: 1 página(s) con un lector de respaldo: pág. 2 → vision (glm-ocr: repetía texto en bucle)']


def test_read_images_prefers_cloud_then_local_then_vision(monkeypatch):
    img = _page()
    monkeypatch.setattr(vlm_ocr, 'cloud_available', lambda: True)
    monkeypatch.setattr(vlm_ocr, 'available', lambda: True)
    monkeypatch.setattr(vlm_ocr, 'read_cloud', lambda paths, plains: [
        ('Contrato de préstamo 2026', 'omniroute:agy/claude-sonnet-4-6', ''),
        (None, '', 'nube: OmniRoute no responde'),
        (None, '', 'nube: OmniRoute no responde')])
    seen = []
    monkeypatch.setattr(vlm_ocr, 'recognize', lambda paths: seen.extend(paths) or [
        {'text': 'Contrato de préstamo 2026.'}, None])
    cloud, local, vision = local_converter.read_images([img, img, img])
    assert len(seen) == 2  # la página que leyó la nube no pasa por GLM-OCR
    assert cloud.model == 'claude-sonnet-4-6' and not cloud.rejected
    assert local.model == 'glm-ocr' and local.rejected == ['nube: OmniRoute no responde']
    assert vision.model == '' and 'prestamo' in vision.text


def test_mode_controls_where_pages_go(monkeypatch):
    monkeypatch.setenv('DOC_CONVERTER_OCR', 'local')
    assert vlm_ocr.mode() == 'local' and not vlm_ocr.cloud_available()
    monkeypatch.setenv('DOC_CONVERTER_OCR', 'vision')
    assert not vlm_ocr.available() and not vlm_ocr.cloud_available()
    monkeypatch.setenv('DOC_CONVERTER_OCR', '')
    assert vlm_ocr.mode() == 'auto'


@pytest.mark.skipif(not os.environ.get('RUN_VLM'), reason='prueba real con el modelo: RUN_VLM=1 (~1 min)')
def test_model_reads_a_rendered_page(monkeypatch):
    monkeypatch.setenv('DOC_CONVERTER_OCR', 'local')
    if not vlm_ocr.available():
        pytest.skip('OCR con modelo no instalado (cli/vlmocr/setup.sh)')
    img = Image.new('RGB', (1240, 600), 'white')
    font = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', 34)
    ImageDraw.Draw(img).multiline_text((80, 80), 'CLÁUSULA TERCERA (Desembolso)\nEl BANCO entregará la suma de\nBs 2.060.000,00 al DEUDOR.', fill='black', font=font, spacing=18)
    read = local_converter.read_images([img])[0]
    assert read.model == 'glm-ocr' and '2.060.000,00' in read.text and 'DEUDOR' in read.text


def test_meta_comments_and_unverifiable_pages_are_dropped():
    assert vlm_ocr.clean('The text is too blurry to be accurately transcribed.') == ''
    assert vlm_ocr.clean('CLÁUSULA PRIMERA\nThe image is blurry.\nEl BANCO') == 'CLÁUSULA PRIMERA\nEl BANCO'
    assert vlm_ocr.judge({'text': 'The text is too blurry to be accurately transcribed.'}, '')[0] is None
    assert vlm_ocr.judge({'text': 'Firma ilegible'}, '')[0] is None
    long_text = 'El BANCO otorga al DEUDOR un préstamo de dinero con intereses y garantía hipotecaria ' * 3
    assert vlm_ocr.judge({'text': long_text}, '')[0] == long_text.strip()


def test_cloud_failure_summary_is_short():
    assert vlm_ocr._cloud_failure('omniroute:agy/claude: down: HTTP Error 503; gemini:x: quota: 429 RESOURCE') == 'sin cuota o no disponible'
    assert vlm_ocr._cloud_failure('gemini:x: respuesta rechazada: omitía texto') == 'sus lecturas no pasaron la validación'
    assert vlm_ocr._cloud_failure('omniroute:agy/claude: OmniRoute no responde') == 'no disponible'
