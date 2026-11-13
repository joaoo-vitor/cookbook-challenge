"""Offline tests for the transcribe stage and the LLM cache. No network, no API key."""

import pymupdf

from cookbook.extract.transcribe import PROMPT, number_lines, transcribe_pdf
from cookbook.ingest.pdf import Strategy
from cookbook.llm import LLMResult, cache_key, read_cache, write_cache

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
GOOD = "Zwiebeln in Scheiben schneiden und mit Essig und Wasser aufkochen. Salz und Pfeffer dazugeben."


class FakeLLM:
    """Records every call and returns a fixed answer."""

    name = "fake"

    def __init__(self, answer="Hierzu eignen sich die festen Bratenstücke."):
        self.answer = answer
        self.calls = []

    def transcribe(self, image_png, prompt):
        self.calls.append((image_png, prompt))
        return LLMResult(self.answer, "fake-model", 10, 5, 0.0)


def _png(text=GOOD) -> bytes:
    tmp = pymupdf.open()
    tmp.new_page().insert_text((50, 100), text, fontsize=11)
    return tmp[0].get_pixmap(dpi=100).tobytes("png")


def _save(doc, tmp_path):
    p = tmp_path / "t.pdf"
    doc.save(p)
    return p


def test_scanned_page_is_rendered_and_sent_to_the_model(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(page.rect, stream=_png())
    llm = FakeLLM()
    [t] = transcribe_pdf(_save(doc, tmp_path), llm)
    assert t.strategy is Strategy.VISION and t.source == "vision"
    assert t.text == llm.answer
    [(image, prompt)] = llm.calls
    assert image.startswith(PNG_MAGIC) and prompt == PROMPT


def test_digital_page_never_calls_the_model(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 100), GOOD, fontsize=11)
    llm = FakeLLM()
    [t] = transcribe_pdf(_save(doc, tmp_path), llm)
    assert t.source == "text_layer" and "Essig" in t.text
    assert llm.calls == []


def test_image_on_a_typed_page_is_transcribed_and_merged_by_position(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 100), "Erster Absatz oben auf der Seite.", fontsize=11)
    page.insert_image(pymupdf.Rect(50, 300, 350, 450), stream=_png(), keep_proportion=False)
    page.insert_text((50, 700), "Letzter Absatz unten auf der Seite.", fontsize=11)
    llm = FakeLLM("Rosinen 100 g")
    [t] = transcribe_pdf(_save(doc, tmp_path), llm)
    assert len(llm.calls) == 1
    assert t.text.index("Erster") < t.text.index("Rosinen") < t.text.index("Letzter")


def test_pages_filter_and_empty_page(tmp_path):
    doc = pymupdf.open()
    doc.new_page()  # blank
    doc.new_page().insert_text((50, 100), GOOD, fontsize=11)
    llm = FakeLLM()
    results = transcribe_pdf(_save(doc, tmp_path), llm, pages={1})
    assert [(r.page, r.source) for r in results] == [(1, "none")]


def test_number_lines_skips_blank_lines():
    assert number_lines(2, "eins\n\nzwei\n") == ["p2:L1\teins", "p2:L2\tzwei"]


def test_prompt_forbids_correction_and_guessing_numbers():
    assert "Do not correct" in PROMPT and "Never guess a number" in PROMPT


# --- cache --------------------------------------------------------------------
def test_cache_key_depends_on_every_part():
    base = cache_key("gemini", "m1", "prompt", b"img")
    assert base == cache_key("gemini", "m1", "prompt", b"img")
    assert len({base, cache_key("hf", "m1", "prompt", b"img"), cache_key("gemini", "m2", "prompt", b"img"),
                cache_key("gemini", "m1", "other", b"img"), cache_key("gemini", "m1", "prompt", b"IMG")}) == 5


def test_cache_roundtrip_marks_hits(tmp_path):
    res = LLMResult("Text", "m", 1, 2, 0.5)
    assert read_cache(tmp_path, "k") is None
    write_cache(tmp_path, "k", res)
    hit = read_cache(tmp_path, "k")
    assert hit.text == "Text" and hit.cached is True


def test_damaged_cache_entry_is_a_miss(tmp_path):
    (tmp_path / "k.json").write_text("{not json", encoding="utf-8")
    assert read_cache(tmp_path, "k") is None
