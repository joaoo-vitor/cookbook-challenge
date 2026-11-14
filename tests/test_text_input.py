"""Tests for what can come in (PDF, image, plain text) and for plain text files used as they are."""

import codecs

import pymupdf
import pytest

from cookbook.extract.transcribe import number_lines, transcribe_pdf
from cookbook.extract.transcript import build_transcript
from cookbook.ingest.pdf import Strategy, analyze_document, analyze_text, detect_kind
from test_transcribe import FakeLLM

LONG = ("Mix the flour, the sugar, the baking powder and the salt in a large bowl, then add the eggs and "
        "whisk until the batter is smooth, and let it rest for ten minutes before frying it in a hot pan.")
RECIPE = f"Pancakes\n\n200 g flour\n{LONG}\n\nServe warm.\n"


def _write(tmp_path, content, name="recipe.txt", encoding="utf-8"):
    path = tmp_path / name
    path.write_bytes(content if isinstance(content, bytes) else content.encode(encoding))
    return path


def _png() -> bytes:
    tmp = pymupdf.open()
    tmp.new_page().insert_text((50, 100), "Salt and pepper to taste", fontsize=11)
    return tmp[0].get_pixmap(dpi=60).tobytes("png")


# --- what came in -----------------------------------------------------------------
def test_the_kind_comes_from_the_first_bytes_not_from_the_name(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 100), "Salt", fontsize=11)
    pdf = tmp_path / "looks_like_text.txt"  # a PDF with a misleading name
    doc.save(pdf)
    assert detect_kind(pdf) == "pdf"
    assert detect_kind(_write(tmp_path, _png(), "photo.pdf")) == "image"  # an image with a misleading name
    assert detect_kind(_write(tmp_path, b"\xff\xd8\xff\xe0 jpeg", "a.dat")) == "image"
    assert detect_kind(_write(tmp_path, RECIPE, "recipe.pdf")) == "text"  # text named like a PDF


def test_text_in_several_encodings_is_text(tmp_path):
    assert detect_kind(_write(tmp_path, "Käse und Brot")) == "text"
    assert detect_kind(_write(tmp_path, codecs.BOM_UTF16_LE + "Käse".encode("utf-16-le"))) == "text"
    assert detect_kind(_write(tmp_path, b"")) == "text"  # empty: handled as an empty text


def test_a_binary_file_is_refused(tmp_path):
    with pytest.raises(ValueError, match="not a PDF, an image or a text file"):
        detect_kind(_write(tmp_path, b"\x00\x01\x02\x03binary\x00data"))


# --- reading a text file --------------------------------------------------------------
def test_a_text_file_is_one_text_page_used_as_it_is(tmp_path):
    [page] = analyze_text(_write(tmp_path, RECIPE))
    assert (page.page, page.strategy, page.kind) == (1, Strategy.TEXT_DIGITAL, "text")
    assert page.text == RECIPE and page.tasks == []
    assert LONG in page.text.splitlines()  # a long line is not wrapped


def test_an_empty_text_file_is_an_empty_page(tmp_path):
    [page] = analyze_text(_write(tmp_path, "  \n\n"))
    assert page.strategy is Strategy.EMPTY and page.kind == "text"


@pytest.mark.parametrize("raw, expected", [
    (codecs.BOM_UTF8 + "Käse".encode(), "Käse"),                       # UTF-8 with a BOM: the BOM is dropped
    ("Käse".encode(), "Käse"),                                        # UTF-8
    (codecs.BOM_UTF16_LE + "Käse".encode("utf-16-le"), "Käse"),        # UTF-16 with a BOM
    ("Käse".encode("cp1252"), "Käse"),                                # Windows-1252, common on Windows
    ("Zwiebeln\r\nSalz\r\n".encode(), "Zwiebeln\r\nSalz\r\n"),        # line ends are kept as they are read
])
def test_text_encodings(tmp_path, raw, expected):
    [page] = analyze_text(_write(tmp_path, raw))
    assert page.text.replace("\r\n", "\n") == expected.replace("\r\n", "\n")


def test_analyze_document_sends_each_kind_to_the_right_reader(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 100), "Salz und Pfeffer dazugeben, dann kochen.", fontsize=11)
    pdf = tmp_path / "typed.pdf"
    doc.save(pdf)
    [p] = analyze_document(pdf)
    assert (p.kind, p.strategy) == ("pdf", Strategy.TEXT_DIGITAL)
    [i] = analyze_document(_write(tmp_path, _png(), "scan.png"))
    assert i.kind == "image"  # an image is read like a scanned page
    [t] = analyze_document(_write(tmp_path, RECIPE))
    assert (t.kind, t.strategy) == ("text", Strategy.TEXT_DIGITAL)


# --- through the extract stage ---------------------------------------------------------
def test_a_text_file_needs_no_model_and_its_line_ids_are_the_files_line_numbers(tmp_path):
    llm, other = FakeLLM(model="model-a"), FakeLLM(model="model-b")
    [t] = transcribe_pdf(_write(tmp_path, RECIPE), llm, second=other)
    assert t.source == "text_file" and llm.calls == [] and other.calls == []
    transcript = build_transcript([t])
    # the file has a blank line after the title and after the long line: they keep their numbers
    assert list(transcript.lines) == ["p1:L1", "p1:L3", "p1:L4", "p1:L6"]
    assert transcript.lines["p1:L4"] == LONG
    assert transcript.lines["p1:L6"] == "Serve warm."


def test_leading_blank_lines_keep_their_numbers(tmp_path):
    [t] = transcribe_pdf(_write(tmp_path, "\n\nPancakes\n200 g flour\n"), FakeLLM())
    assert list(build_transcript([t]).lines) == ["p1:L3", "p1:L4"]


def test_a_model_reading_does_not_count_blank_lines():
    assert number_lines(1, "one\n\ntwo\n") == ["p1:L1\tone", "p1:L2\ttwo"]
    assert number_lines(1, "one\n\ntwo\n", count_blank=True) == ["p1:L1\tone", "p1:L3\ttwo"]
