import fitz
import pytest


def make_pdf(texts):
    doc = fitz.open()
    for t in texts:
        doc.new_page().insert_text((72, 72), t, fontsize=14)
    return doc.tobytes()


@pytest.fixture
def pdf3():
    return make_pdf(['uno', 'dos', 'tres'])
