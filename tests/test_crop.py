from PIL import Image, ImageDraw, ImageFont

from backend.engines import local_converter


def test_crop_to_document():
    doc = Image.new('RGB', (850, 1100), 'white')
    font = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', 34)
    ImageDraw.Draw(doc).text((80, 100), 'TOTAL 5.595,00', fill='black', font=font)
    photo = Image.new('RGB', (2000, 1800), (40, 40, 45))
    photo.paste(doc.rotate(8, expand=True, fillcolor=(40, 40, 45)), (500, 250))

    cropped, note = local_converter.crop_to_document(photo)
    assert 'recortado' in note and cropped.size[0] < 1000
    assert '5.595,00' in local_converter._ocr_image(cropped)

    empty = Image.new('RGB', (800, 600), (40, 40, 45))
    assert local_converter.crop_to_document(empty) == (empty, '')
