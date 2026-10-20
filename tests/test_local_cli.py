"""Pruebas del flujo local (CLI/Finder): planificación, lotes, OCR estructurado, planillas."""
import json
import sys

import fitz
import pandas as pd
import pytest
from PIL import Image, ImageDraw, ImageFont

from backend import batch, jobs
from backend.engines import finance_converter as fin
from backend.engines import local_converter, structured_ocr


# --- Importes y categorías -----------------------------------------------------

@pytest.mark.parametrize('raw,expected', [
    ('1,000.00', 1000.0), ('1.000,00', 1000.0), ('(250.50)', -250.5), ('250.50-', -250.5),
    ('Bs -1 000,5', -1000.5), ('12,5', 12.5), ('1,234,567.89', 1234567.89), (7, 7.0),
])
def test_to_number(raw, expected):
    assert fin._to_number(raw) == expected


@pytest.mark.parametrize('raw', ['abc', '', None, '1.2.3'])
def test_to_number_rejects_non_numbers(raw):
    assert pd.isna(fin._to_number(raw))


def test_short_keywords_need_word_boundaries():
    assert fin._detect_categoria('compra en hospital', -5, 'Pago') == 'Salud'
    assert fin._detect_categoria('cuota crédito', -5, 'Pago') == 'Préstamos'
    assert fin._detect_categoria('pago iva', -5, 'Pago') == 'Impuestos'


def test_csv_with_thousands_separators_keeps_every_row():
    csv = 'Fecha,Descripción,Monto\n2026-01-01,Pago,"1,000.00"\n2026-01-02,Cobro,-250.50\n'
    r = fin.convert_to_finance_csv(csv.encode(), 'movs.csv', ai_categorize=False)
    assert r['success'] and r['rows'] == 2


def test_unreadable_dated_row_is_reported_not_lost_silently():
    csv = 'Fecha,Descripción,Monto\n2026-01-01,Pago,100\n2026-01-03,Raro,N/D\nTotal,,\n'
    r = fin.convert_to_finance_csv(csv.encode(), 'movs.csv', ai_categorize=False)
    assert r['rows'] == 1 and any('Raro' in w for w in r['warnings'])


def test_separate_debit_and_credit_columns():
    csv = 'Fecha,Detalle,Débito,Crédito,Saldo\n2026-02-01,Compra,100.00,,900\n2026-02-02,Abono,,50.00,950\n'
    r = fin.convert_to_finance_csv(csv.encode(), 'bnb.csv', ai_categorize=False)
    rows = r['buffer'].getvalue().decode().splitlines()
    assert r['rows'] == 2 and 'Salida,100.0' in rows[1] and 'Entrada,50.0' in rows[2]


# --- OCR estructurado ----------------------------------------------------------

def test_blocks_to_markdown():
    blocks = [
        {'type': 'title', 'text': 'Informe'},
        {'type': 'paragraph', 'text': 'Intro'},
        {'type': 'table', 'rows': [['Concepto', 'Monto'], ['Ventas', '1,234.50'], ['a|b', '']]},
        {'type': 'list', 'items': [{'marker': '1. ', 'text': 'uno'}, {'marker': '• ', 'text': 'dos'}]},
    ]
    md = structured_ocr.blocks_to_markdown(blocks)
    assert md.startswith('# Informe')
    assert '| Concepto | Monto |\n|---|---|\n| Ventas | 1,234.50 |' in md
    assert 'a\\|b' in md and '1. uno\n- dos' in md


def test_choose_falls_back_to_plain_when_structure_loses_text():
    plain = 'Total 1,234.50 pagado el 12 de marzo por Juan Perez'
    assert structured_ocr.choose('Total', plain) == (plain, False)
    assert structured_ocr.choose('# Total\n\n1,234.50 pagado el 12 de marzo por Juan Perez', plain)[1] is True


@pytest.mark.skipif(not structured_ocr.available(), reason='docvision no compilado (macOS 26+)')
def test_docvision_reads_table_cells(tmp_path):
    img = Image.new('RGB', (1000, 500), 'white')
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', 30)
    rows = [['Concepto', 'Monto'], ['Ventas', '1,234.50'], ['Costos', '-800.00']]
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            box = (80 + c * 360, 80 + r * 70, 80 + (c + 1) * 360, 80 + (r + 1) * 70)
            d.rectangle(box, outline='black', width=3)
            d.text((box[0] + 16, box[1] + 18), text, fill='black', font=font)
    text, structured = local_converter.ocr_images([img])[0]
    assert structured and '| Ventas | 1,234.50 |' in text


# --- PDFs ------------------------------------------------------------------------

def test_pdf_errors_are_clear(pdf3):
    with pytest.raises(local_converter.ConversionError, match='vacío'):
        local_converter.open_pdf(b'')
    with pytest.raises(local_converter.ConversionError, match='dañado'):
        local_converter.open_pdf(b'%PDF-1.4 basura')
    doc = fitz.open(stream=pdf3, filetype='pdf')
    encrypted = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw='x', owner_pw='y')
    with pytest.raises(local_converter.ConversionError, match='contraseña'):
        local_converter.open_pdf(encrypted)


def test_blank_pages_are_reported():
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), 'Texto suficiente en la primera página. ' * 6)
    doc.new_page()  # página en blanco: sin capa de texto y sin nada que leer con OCR
    r = local_converter.convert_pdf_ex(doc.tobytes())
    assert r.pages == 2 and any('sin texto' in w and '2' in w for w in r.warnings)


# --- Planillas ---------------------------------------------------------------------

def test_generic_spreadsheet_exports_each_sheet_untouched(tmp_path):
    path = tmp_path / 'inventario.xlsx'
    with pd.ExcelWriter(path) as w:
        pd.DataFrame({'Item': ['A', 'B'], 'Cantidad': [3, 4]}).to_excel(w, sheet_name='Stock', index=False)
        pd.DataFrame({'Código': ['X1'], 'Precio': [9.5]}).to_excel(w, sheet_name='Precios', index=False)
    assert local_converter.spreadsheet_kind(str(path)) == 'generic'
    outputs = dict(local_converter.convert_spreadsheet_generic(str(path)))
    assert set(outputs) == {'inventario - Stock.csv', 'inventario - Precios.csv'}
    assert outputs['inventario - Stock.csv'].decode().splitlines() == ['Item,Cantidad', 'A,3', 'B,4']


def test_bank_spreadsheet_is_detected(tmp_path):
    path = tmp_path / 'extracto.xlsx'
    pd.DataFrame({'Fecha': ['2026-01-01'], 'Descripción': ['Pago'], 'Monto': [10]}).to_excel(path, index=False)
    assert local_converter.spreadsheet_kind(str(path)) == 'finance'


# --- Planificación -------------------------------------------------------------------

def _tree(tmp_path):
    root = tmp_path / 'docs'
    (root / 'sub' / 'deep').mkdir(parents=True)
    (root / '.oculta').mkdir()
    (root / 'prev_convertida').mkdir()
    for rel in ['a.docx', 'a.rtf', 'sub/b.pdf', 'sub/deep/c.png', '.oculta/x.pdf', 'prev_convertida/y.pdf',
                '~$lock.docx', '.DS_Store', 'notas.md', 'datos.zip', 'otro.zip', 'sin_ext']:
        (root / rel).write_bytes(b'x')
    return root


def test_plan_is_recursive_and_skips_noise(tmp_path):
    p = batch.plan([str(_tree(tmp_path))])
    assert sorted(i.rel for i in p.items) == ['a.docx', 'a.rtf', 'sub/b.pdf', 'sub/deep/c.png']
    assert p.ignored['.zip'] == 2 and p.ignored['ya es texto'] == 1 and p.ignored['sin extensión'] == 1
    assert p.out_roots == [tmp_path / 'docs_convertida']
    by_rel = {i.rel: i for i in p.items}
    assert by_rel['sub/deep/c.png'].out_dir == tmp_path / 'docs_convertida' / 'sub' / 'deep'
    assert by_rel['a.docx'].collide and by_rel['a.rtf'].collide and not by_rel['sub/b.pdf'].collide


def test_plan_non_recursive_and_missing(tmp_path):
    p = batch.plan([str(_tree(tmp_path)), str(tmp_path / 'nada.pdf')], recursive=False)
    assert sorted(i.rel for i in p.items) == ['a.docx', 'a.rtf'] and p.missing == [str(tmp_path / 'nada.pdf')]


def test_supported_sets_match_local_converter():
    assert jobs.OFFICE_EXTS == local_converter.OFFICE_EXTS
    assert jobs.IMAGE_EXTS == local_converter.IMAGE_EXTS
    assert jobs.FINANCE_EXTS == local_converter.SPREADSHEET_EXTS


# --- Lote de punta a punta (procesos worker reales) ---------------------------------------

def test_folder_batch_end_to_end(tmp_path):
    root = tmp_path / 'lote'
    (root / 'sub').mkdir(parents=True)
    (root / 'a.html').write_text('<h1>Alfa</h1><p>uno</p>')
    (root / 'a.rtf').write_text(r'{\rtf1 Alfa rtf}')
    (root / 'sub' / 'b.html').write_text('<h1>Beta</h1>')
    (root / 'roto.pdf').write_bytes(b'%PDF-1.4 basura')
    (root / 'vacio.pdf').write_bytes(b'')
    (root / 'mov.csv').write_text('Fecha,Descripción,Monto\n2026-01-01,Pago,"1,000.00"\n')

    plan = batch.plan([str(root)])
    logs, errs = [], []
    stats = batch.run(plan, 'local', 'auto', jobs=2, timeout=120, log=logs.append, err=errs.append)
    out = tmp_path / 'lote_convertida'

    assert stats['converted'] == 4 and stats['errors'] == 2
    assert (out / 'a.html.md').read_text().startswith('# Alfa') or 'Alfa' in (out / 'a.html.md').read_text()
    assert (out / 'a.rtf.md').exists() and (out / 'sub' / 'b.md').exists()  # colisión a.html/a.rtf resuelta
    assert any('dañado' in e for e in errs) and any('vacío' in e for e in errs)
    report = json.loads((out / batch.REPORT_NAME).read_text())
    assert report['files']['sub/b.html']['status'] == 'ok' and report['files']['roto.pdf']['status'] == 'error'

    # Segunda pasada: nada cambió → no reconvierte; si cambia un archivo, solo ese.
    (root / 'sub' / 'b.html').write_text('<h1>Beta 2</h1>')
    again = batch.run(batch.plan([str(root)]), 'local', 'auto', jobs=2, timeout=120, log=lambda m: None, err=lambda m: None)
    assert again['unchanged'] == 3 and again['converted'] == 1
    assert 'Beta 2' in (out / 'sub' / 'b.md').read_text() and not (out / 'sub' / 'b_1.md').exists()


def test_single_file_never_overwrites(tmp_path):
    f = tmp_path / 'nota.html'
    f.write_text('<p>hola</p>')
    for _ in range(2):
        batch.run(batch.plan([str(f)]), 'local', 'auto', jobs=1, timeout=120, log=lambda m: None, err=lambda m: None)
    assert (tmp_path / 'nota.md').exists() and (tmp_path / 'nota_1.md').exists()


def test_worker_timeout_kills_only_that_file(tmp_path):
    f = tmp_path / 'lento.html'
    f.write_text('<p>x</p>')
    item = batch.plan([str(f)]).items[0]
    result = batch._run_worker(item, 'local', 'auto', timeout=0.05)
    assert not result['ok'] and 'tiempo límite' in result['error']


def test_markdown_that_loses_text_falls_back_to_plain_page(monkeypatch):
    """pymupdf4llm 0.0.17 borraba letras de los encabezados; el texto nunca debe perderse."""
    import pymupdf4llm
    real = pymupdf4llm.to_markdown

    def broken(doc, **kw):
        chunks = real(doc, **kw)
        for c in chunks:
            c['text'] = c['text'].replace('BANCO NACIONAL DE BOLIVIA', 'CO C O O S O CU')
        return chunks

    monkeypatch.setattr(pymupdf4llm, 'to_markdown', broken)
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), 'BANCO NACIONAL DE BOLIVIA ESTADO DE CUENTA', fontsize=14)
    page.insert_text((50, 90), 'Saldo anterior 14,920.15 y movimientos del mes de marzo de 2026 detallados abajo. ' * 2, fontsize=9)
    r = local_converter.convert_pdf_ex(doc.tobytes())
    assert 'BANCO NACIONAL DE BOLIVIA' in r.markdown and any('texto plano' in w for w in r.warnings)
