import fitz
import pytest
from fastapi import HTTPException

from backend.engines import pdf_tools
from backend.utils.http import content_disposition, safe_filename, validate_upload
from tests.conftest import make_pdf


def texts(data):
    with fitz.open(stream=data, filetype='pdf') as d:
        return [p.get_text().strip() for p in d]


def test_parse_pages():
    assert pdf_tools.parse_pages('1-2, 4', 5) == [0, 1, 3]
    for bad in ('0', '6', '3-1', 'x', ''):
        with pytest.raises(pdf_tools.ToolInputError):
            pdf_tools.parse_pages(bad, 5)


def test_merge_split_reorder(pdf3):
    merged = pdf_tools.merge([pdf3, make_pdf(['cuatro'])])
    assert texts(merged) == ['uno', 'dos', 'tres', 'cuatro']
    a, b = pdf_tools.split(merged, '1-2;3-4')
    assert texts(a) == ['uno', 'dos'] and texts(b) == ['tres', 'cuatro']
    assert texts(pdf_tools.reorder(pdf3, '3,1')) == ['tres', 'uno']
    with pytest.raises(pdf_tools.ToolInputError):
        pdf_tools.merge([pdf3])


def test_rotate_only_selected(pdf3):
    out = pdf_tools.rotate(pdf3, 90, '2')
    with fitz.open(stream=out, filetype='pdf') as d:
        assert [p.rotation for p in d] == [0, 90, 0]
    with pytest.raises(pdf_tools.ToolInputError):
        pdf_tools.rotate(pdf3, 45)


def test_redact_removes_text():
    out, hits = pdf_tools.redact(make_pdf(['cuenta 12345 privada']), ['12345'])
    assert hits == 1 and '12345' not in texts(out)[0] and 'privada' in texts(out)[0]


def test_watermark_and_compress_keep_pages(pdf3):
    assert 'BORRADOR' in texts(pdf_tools.watermark(pdf3, 'BORRADOR'))[0]
    small = pdf_tools.compress(pdf3)
    assert len(small) <= len(pdf3) and texts(small) == ['uno', 'dos', 'tres']


def test_inspect_warns_on_forms():
    doc = fitz.open(); page = doc.new_page()
    w = fitz.Widget(); w.field_name = 'nombre'; w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    w.rect = fitz.Rect(50, 50, 200, 80); page.add_widget(w)
    info = pdf_tools.inspect(doc.tobytes())
    assert info['forms'] and any('formulario' in x for x in info['warnings'])


def test_encrypted_pdf_rejected(pdf3):
    doc = fitz.open(stream=pdf3, filetype='pdf')
    locked = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw='x', owner_pw='y')
    assert pdf_tools.inspect(locked)['encrypted']
    with pytest.raises(pdf_tools.ToolInputError):
        pdf_tools.rotate(locked, 90)


def test_safe_names_and_headers():
    assert safe_filename('../../etc/pass"wd\r\n.pdf') == 'passwd.pdf'
    header = content_disposition('Año "fiscal"\n.pdf')
    assert '\n' not in header and "filename*=UTF-8''A%C3%B1o" in header


def test_validate_upload(pdf3):
    assert validate_upload(pdf3, 'a.PDF', {'.pdf'}) == '.pdf'
    for data, name, code in ((b'', 'a.pdf', 400), (b'hola', 'a.pdf', 415), (pdf3, 'a.exe', 415)):
        with pytest.raises(HTTPException) as e:
            validate_upload(data, name, {'.pdf'})
        assert e.value.status_code == code
