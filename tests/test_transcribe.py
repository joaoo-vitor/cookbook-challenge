"""Offline tests for the transcribe stage and the LLM cache. No network, no API key."""

import pymupdf

from cookbook.extract.transcribe import PROMPT, number_lines, transcribe_pdf
from cookbook.ingest.pdf import Strategy
import pytest

from cookbook.llm import LLMResult, cache_key, read_cache, sniff_mime, write_cache

JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
GOOD = "Zwiebeln in Scheiben schneiden und mit Essig und Wasser aufkochen. Salz und Pfeffer dazugeben."


class FakeLLM:
    """Records every call and returns a fixed answer."""

    name = "fake"

    def __init__(self, answer="Hierzu eignen sich die festen Bratenstücke.", model="fake-model", fail=None,
                 served_by=None):
        self.answer = answer
        self.model = model
        self.fail = fail  # an exception to raise instead of answering
        self.served_by = served_by  # pretend a fallback model answered instead of `model`
        self.calls = []

    def transcribe(self, image, prompt):
        self.calls.append((image, prompt))
        if self.fail:
            raise self.fail
        return LLMResult(self.answer, self.served_by or self.model, 10, 5, 0.0,
                         fell_back_from=self.model if self.served_by else None)


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
    assert image.startswith(JPEG_MAGIC) and prompt == PROMPT


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


# --- two readers --------------------------------------------------------------
def _scan(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(page.rect, stream=_png())
    return _save(doc, tmp_path)


def test_two_models_read_the_same_image_and_differences_are_flagged(tmp_path):
    body = "Man nimmt {} Zwiebeln und Salz\nPfeffer Nelken Lorbeer und Wasser dazugeben\nAlles gut aufkochen"
    first = FakeLLM(body.format(4), model="model-a")
    other = FakeLLM(body.format(5), model="model-b")
    [t] = transcribe_pdf(_scan(tmp_path), first, second=other)
    assert t.text == body.format(4)  # the primary reading is the transcript
    assert t.second_text == body.format(5)
    assert first.calls[0][0] == other.calls[0][0]  # the same image went to both
    [cmp] = t.comparisons
    assert cmp.label == "page" and [f.kind for f in cmp.flags] == ["number"]
    assert not t.unreliable and len(t.second_calls) == 1


def test_unrelated_readings_make_the_page_unreliable(tmp_path):
    first = FakeLLM("Zwiebeln in Scheiben schneiden und kochen", model="model-a")
    other = FakeLLM("Mehl Zucker Milch Eier verrühren und backen", model="model-b")
    [t] = transcribe_pdf(_scan(tmp_path), first, second=other)
    assert t.unreliable


def test_a_failing_second_reader_keeps_the_primary_reading(tmp_path):
    first = FakeLLM("Salz und Pfeffer dazugeben", model="model-a")
    other = FakeLLM(model="model-b", fail=RuntimeError("503 UNAVAILABLE"))
    [t] = transcribe_pdf(_scan(tmp_path), first, second=other)
    assert t.text == "Salz und Pfeffer dazugeben" and t.comparisons == []
    assert len(t.warnings) == 1 and "second read failed" in t.warnings[0] and "503" in t.warnings[0]


def test_a_failing_primary_reader_is_an_error(tmp_path):
    first = FakeLLM(model="model-a", fail=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        transcribe_pdf(_scan(tmp_path), first, second=FakeLLM(model="model-b"))


def test_a_fallback_is_reported_and_the_comparison_still_happens(tmp_path):
    first = FakeLLM("Salz und Pfeffer dazugeben", model="model-a", served_by="model-fb")
    other = FakeLLM("Salz und Pfeffer dazugeben", model="model-b")
    [t] = transcribe_pdf(_scan(tmp_path), first, second=other)
    assert t.warnings == ["primary reader fell back from model-a to model-fb"]
    assert len(t.comparisons) == 1


def test_two_readings_from_the_same_model_are_not_compared(tmp_path):
    first = FakeLLM("Salz und Pfeffer", model="model-a", served_by="model-fb")
    other = FakeLLM("Salz und Pfeffer", model="model-b", served_by="model-fb")  # both fell back to one model
    [t] = transcribe_pdf(_scan(tmp_path), first, second=other)
    assert t.comparisons == [] and t.second_text is None
    assert any("not independent" in w for w in t.warnings)
    assert any("primary reader fell back" in w for w in t.warnings)


def test_the_second_reader_must_be_a_different_model(tmp_path):
    with pytest.raises(ValueError):
        transcribe_pdf(_scan(tmp_path), FakeLLM(model="same"), second=FakeLLM(model="same"))


def test_typed_pages_do_not_call_either_model(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 100), GOOD, fontsize=11)
    first, other = FakeLLM(model="model-a"), FakeLLM(model="model-b")
    [t] = transcribe_pdf(_save(doc, tmp_path), first, second=other)
    assert first.calls == [] and other.calls == [] and t.comparisons == []


def test_images_on_a_typed_page_are_compared_with_their_own_ids(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 100), "Erster Absatz oben auf der Seite.", fontsize=11)
    page.insert_image(pymupdf.Rect(50, 300, 350, 450), stream=_png(), keep_proportion=False)
    first = FakeLLM("Rosinen 100 g", model="model-a")
    other = FakeLLM("Rosinen 150 g", model="model-b")
    [t] = transcribe_pdf(_save(doc, tmp_path), first, second=other)
    [cmp] = t.comparisons
    assert cmp.label == "image 0" and cmp.flags[0].line == "p1:image0:L1"
    assert "Rosinen 100 g" in t.text  # the primary reading is merged into the page


def test_without_a_second_reader_nothing_is_compared(tmp_path):
    [t] = transcribe_pdf(_scan(tmp_path), FakeLLM())
    assert t.comparisons == [] and t.second_text is None and not t.unreliable


def test_sniff_mime_reads_the_first_bytes():
    assert sniff_mime(PNG_MAGIC + b"rest") == "image/png"
    assert sniff_mime(JPEG_MAGIC + b"rest") == "image/jpeg"
    with pytest.raises(ValueError):
        sniff_mime(b"GIF89a....")


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
