"""Tests for PDF page-strategy detection. PDFs are generated on the fly, so no real documents are committed."""

import pymupdf
import pytest

from cookbook.ingest.pdf import (
    Signals, Strategy, analyze_pdf, decide, merge_page_text, render_page, render_task,
    text_quality,
)

GOOD = "Zwiebeln in Scheiben schneiden und mit Essig und Wasser aufkochen. Salz und Pfeffer dazugeben."
GARBAGE = "l | ; t i e 3 # ~ Zwi3beln ^ ` | l1 ; ' ,, | I l 0 g § ¦ ¬ | ; l l"


def _text_page(doc, text, **kw):
    page = doc.new_page()
    page.insert_text((50, 100), text, fontsize=11, **kw)
    return page


def _scan_png(text=GOOD) -> bytes:
    """Render text to a bitmap: stands in for a photographed page."""
    tmp = pymupdf.open()
    _text_page(tmp, text)
    return tmp[0].get_pixmap(dpi=100).tobytes("png")


def _scan_page(doc, hidden_text=None):
    page = doc.new_page()
    page.insert_image(page.rect, stream=_scan_png())
    if hidden_text:
        page.insert_text((50, 100), hidden_text, fontsize=11, render_mode=3)
    return page


def _analyze(doc, tmp_path):
    p = tmp_path / "t.pdf"
    doc.save(p)
    return analyze_pdf(p)


def test_digital_page(tmp_path):
    doc = pymupdf.open()
    _text_page(doc, GOOD)
    [r] = _analyze(doc, tmp_path)
    assert r.strategy is Strategy.TEXT_DIGITAL
    assert "Essig" in r.text


def test_scanned_page_has_no_text_layer(tmp_path):
    doc = pymupdf.open()
    _scan_page(doc)
    [r] = _analyze(doc, tmp_path)
    assert r.strategy is Strategy.VISION
    assert r.signals.n_chars == 0 and r.signals.has_big_image


def test_good_ocr_layer_is_used_but_flagged(tmp_path):
    doc = pymupdf.open()
    _scan_page(doc, hidden_text=GOOD)
    [r] = _analyze(doc, tmp_path)
    assert r.signals.has_invisible_text and r.signals.has_big_image
    assert r.strategy is Strategy.TEXT_OCR


def test_garbled_ocr_layer_falls_back_to_vision(tmp_path):
    doc = pymupdf.open()
    _scan_page(doc, hidden_text=GARBAGE)
    [r] = _analyze(doc, tmp_path)
    assert r.strategy is Strategy.VISION
    assert r.text  # layer kept for cross-checking


def test_digital_page_with_image_is_not_mistaken_for_ocr(tmp_path):
    doc = pymupdf.open()
    page = _text_page(doc, GOOD)
    page.insert_image(pymupdf.Rect(300, 300, 400, 400), stream=_scan_png())  # small photo
    [r] = _analyze(doc, tmp_path)
    assert r.strategy is Strategy.TEXT_DIGITAL


def test_blank_page_is_empty(tmp_path):
    doc = pymupdf.open()
    doc.new_page()
    [r] = _analyze(doc, tmp_path)
    assert r.strategy is Strategy.EMPTY


def test_mixed_document_decides_per_page(tmp_path):
    doc = pymupdf.open()
    _text_page(doc, GOOD)
    _scan_page(doc)
    results = _analyze(doc, tmp_path)
    assert [r.strategy for r in results] == [Strategy.TEXT_DIGITAL, Strategy.VISION]
    assert [r.page for r in results] == [1, 2]


def test_ocr_metadata_is_detected(tmp_path):
    doc = pymupdf.open()
    _scan_page(doc, hidden_text=GOOD)
    doc.set_metadata({"producer": "OCRmyPDF 16 / Tesseract"})
    [r] = _analyze(doc, tmp_path)
    assert r.signals.ocr_producer


# --- extra tasks (images on text pages) ----------------------------------------
def _mixed_page(doc, image_rect=pymupdf.Rect(50, 300, 350, 450)):
    page = doc.new_page()
    page.insert_text((50, 100), "Erster Absatz mit Text oben auf der Seite.", fontsize=11)
    page.insert_image(image_rect, stream=_scan_png(), keep_proportion=False)  # fill the box exactly
    page.insert_text((50, 700), "Letzter Absatz mit Text unten auf der Seite.", fontsize=11)
    return page


def test_most_pages_have_no_extra_tasks(tmp_path):
    doc = pymupdf.open()
    _text_page(doc, GOOD)  # typed
    _scan_page(doc)  # plain scan
    doc.new_page()  # blank
    results = _analyze(doc, tmp_path)
    assert [r.tasks for r in results] == [[], [], []]


def test_mixed_page_gets_one_extra_task_per_image(tmp_path):
    doc = pymupdf.open()
    _mixed_page(doc)
    [r] = _analyze(doc, tmp_path)
    assert r.strategy is Strategy.TEXT_DIGITAL  # page label unchanged
    assert [t.bbox for t in r.tasks] == [(50.0, 300.0, 350.0, 450.0)]


def test_tiny_image_is_ignored(tmp_path):
    doc = pymupdf.open()
    _mixed_page(doc, image_rect=pymupdf.Rect(50, 300, 80, 320))  # ~0.1% of the page
    [r] = _analyze(doc, tmp_path)
    assert r.tasks == []


def test_background_image_behind_text_is_ignored(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(page.rect, stream=_scan_png())
    page.insert_text((50, 100), GOOD, fontsize=11)  # visible text on top
    [r] = _analyze(doc, tmp_path)
    assert r.strategy is Strategy.TEXT_DIGITAL
    assert r.tasks == []


def test_ocr_page_gets_no_extra_tasks(tmp_path):
    doc = pymupdf.open()
    _scan_page(doc, hidden_text=GOOD)
    [r] = _analyze(doc, tmp_path)
    assert r.strategy is Strategy.TEXT_OCR
    assert r.tasks == []


def test_merge_puts_image_text_at_its_position(tmp_path):
    doc = pymupdf.open()
    _mixed_page(doc)
    [r] = _analyze(doc, tmp_path)
    merged = merge_page_text(r, {0: "Rosinen 100 g"})
    top, image, bottom = (merged.index(s) for s in ("Erster", "Rosinen", "Letzter"))
    assert top < image < bottom
    assert "[from image, page 1]" in merged  # origin is marked


@pytest.mark.parametrize("answer", ["NONE", "none", "  ", ""])
def test_merge_ignores_images_without_text(tmp_path, answer):
    doc = pymupdf.open()
    _mixed_page(doc)
    [r] = _analyze(doc, tmp_path)
    assert merge_page_text(r, {0: answer}) == r.text


def test_render_task_returns_png_of_the_region(tmp_path):
    doc = pymupdf.open()
    _mixed_page(doc)
    path = tmp_path / "t.pdf"
    doc.save(path)
    [r] = analyze_pdf(path)
    png = render_task(path, 1, r.tasks[0], dpi=72)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pix = pymupdf.Pixmap(png)
    assert (pix.width, pix.height) == (300, 150)  # the region's size at 72 dpi


def test_render_page_returns_png_of_the_whole_page(tmp_path):
    doc = pymupdf.open()
    _scan_page(doc)
    path = tmp_path / "t.pdf"
    doc.save(path)
    png = render_page(path, 1, dpi=72)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pix = pymupdf.Pixmap(png)
    assert (pix.width, pix.height) == (595, 842)  # A4 at 72 dpi


# --- pure functions ----------------------------------------------------------
def test_quality_separates_words_from_garbage():
    assert text_quality(GOOD) > 0.8
    assert text_quality(GARBAGE) < 0.5
    assert text_quality("kurz") is None  # too little text to judge


def _signals(**kw):
    base = dict(n_chars=500, image_coverage=0.0, has_big_image=False,
                has_invisible_text=False, ocr_producer=False, ocr_font=False,
                text_quality=0.9)
    return Signals(**{**base, **kw})


@pytest.mark.parametrize("kw, expected", [
    ({}, Strategy.TEXT_DIGITAL),
    ({"n_chars": 0, "image_coverage": 1.0, "has_big_image": True, "text_quality": None}, Strategy.VISION),
    ({"has_invisible_text": True, "has_big_image": True, "image_coverage": 1.0}, Strategy.TEXT_OCR),
    ({"has_invisible_text": True, "text_quality": 0.2}, Strategy.VISION),
    ({"text_quality": 0.2}, Strategy.VISION),  # broken encoding in a "digital" PDF
    ({"has_big_image": True, "image_coverage": 0.95}, Strategy.TEXT_DIGITAL),  # image alone is no proof
    ({"has_big_image": True, "image_coverage": 0.95, "ocr_producer": True}, Strategy.TEXT_OCR),
])
def test_decide(kw, expected):
    strategy, reasons = decide(_signals(**kw))
    assert strategy is expected
    assert reasons
