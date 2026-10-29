import os

import fitz
import pytest

# El OCR con modelo tarda decenas de segundos por página: los tests usan Vision salvo
# test_vlm_ocr.py, que lo prueba aparte (RUN_VLM=1 para la prueba real con el modelo).
os.environ['DOC_CONVERTER_OCR'] = 'vision'


def make_pdf(texts):
    doc = fitz.open()
    for t in texts:
        doc.new_page().insert_text((72, 72), t, fontsize=14)
    return doc.tobytes()


@pytest.fixture
def pdf3():
    return make_pdf(['uno', 'dos', 'tres'])
