"""Tests for comparing two reads of a page. Plain text in, flags out: no model, no network."""

import pytest

from cookbook.extract.compare import MIN_AGREEMENT, compare_reads

TEXT = "Zwiebeln in Scheiben schneiden\nPfeffer Nelken und Salz dazugeben\nAlles zusammen gut aufkochen"


def _cmp(primary, secondary, **kw):
    return compare_reads(1, primary, secondary, **kw)


def test_identical_reads_agree_completely():
    c = _cmp(TEXT, TEXT)
    assert c.agreement == 1.0 and not c.unreliable and c.flags == []


def test_a_different_number_is_a_high_severity_flag_with_its_line():
    c = _cmp("Man nimmt 4 Zwiebeln\nund 1/2 Liter Essig", "Man nimmt 5 Zwiebeln\nund 1/2 Liter Essig")
    [f] = c.flags
    assert (f.kind, f.severity, f.line) == ("number", "high", "p1:L1")
    assert (f.primary, f.secondary) == ("4", "5")


def test_a_different_word_is_a_medium_flag_on_the_right_line():
    c = _cmp(TEXT, TEXT.replace("Pfeffer", "Pfetter"))
    [f] = c.flags
    assert (f.kind, f.severity, f.line, f.primary, f.secondary) == (
        "word", "medium", "p1:L2", "Pfeffer", "Pfetter")


def test_a_number_present_in_only_one_read_is_flagged():
    c = _cmp("Man nimmt 4 Zwiebeln und Salz", "Man nimmt Zwiebeln und Salz")
    [f] = c.flags
    assert f.kind == "number" and f.primary == "4" and f.secondary == ""


@pytest.mark.parametrize("primary, secondary", [
    ("Der Braten wird mit Bra-\ntenstücke gar", "Der Braten wird mit Bratenstücke gar"),  # line-end hyphen
    ("Soße mit säuerlich - süßem Geschmack", "Soße mit säuerlich-süßem Geschmack"),  # spacing around a dash
    ("Aus dem Kochbuch —\nAnno dazumal", "Aus dem Kochbuch -\nAnno dazumal"),  # dash character
    ("[drawing] Korin-\n[drawing] then", "[drawing] Korinthen"),  # label split over two lines
    ("Salz, Pfeffer. Nelken", "Salz Pfeffer Nelken"),  # punctuation only
])
def test_differences_that_are_not_content_are_ignored(primary, secondary):
    c = _cmp(primary, secondary)
    assert c.flags == [] and c.agreement == 1.0


def test_a_hyphen_join_is_attributed_to_the_first_line():
    c = _cmp("Der Braten wird mit Bra-\ntenstücke gar\nund Salz", "Der Braten wird mit Brotenstücke gar\nund Salz")
    [f] = c.flags
    assert f.line == "p1:L1" and f.primary == "Bratenstücke" and f.secondary == "Brotenstücke"


@pytest.mark.parametrize("primary, secondary", [
    ("188\nSalz und Pfeffer", "Salz und Pfeffer"),  # page number only in the primary read
    ("Salz und Pfeffer", "189\nSalz und Pfeffer"),  # ... or only in the second
    ("Salz und Pfeffer\n189", "Salz und Pfeffer"),  # at the end of the page
])
def test_a_page_number_in_only_one_read_is_ignored(primary, secondary):
    assert _cmp(primary, secondary).flags == []


def test_a_single_word_that_moved_is_not_a_disagreement():
    # drawing labels listed in a different order by the two readers
    c = _cmp("Soße wird gemacht\n[drawing] Rotwein\n[drawing] Zucker\n[drawing] Mehl",
             "Soße wird gemacht\n[drawing] Zucker\n[drawing] Mehl\n[drawing] Rotwein")
    assert c.flags == [] and c.agreement == 1.0


def test_a_moved_number_is_still_flagged():
    c = _cmp("Nimm 4 Zwiebeln und Salz dazu", "Nimm Zwiebeln und 4 Salz dazu")
    assert [f.kind for f in c.flags] == ["number", "number"]


def test_a_quantity_at_the_start_of_the_page_is_not_mistaken_for_a_page_number():
    c = _cmp("4 Zwiebeln schneiden und kochen", "Zwiebeln schneiden und kochen")
    assert [(f.kind, f.primary) for f in c.flags] == [("number", "4")]


def test_a_primary_unreadable_marker_is_a_flag_and_the_word_is_still_compared():
    c = _cmp("Salz und [?Pfeffer?] dazu", "Salz und Pfeffer dazu")
    [f] = c.flags
    assert (f.kind, f.severity, f.primary) == ("marker", "medium", "Pfeffer")


def test_markers_in_the_second_read_are_counted():
    c = _cmp("Salz und Pfeffer dazu", "Salz und [?Pfeffer?] dazu\nund [?durch den dicken?]")
    assert c.secondary_markers == 2


def test_two_unrelated_reads_make_the_page_unreliable():
    c = _cmp("Zwiebeln in Scheiben schneiden und kochen", "Mehl Zucker Milch Eier verrühren und backen")
    assert c.agreement < MIN_AGREEMENT and c.unreliable and c.flags


def test_a_few_differences_in_a_long_page_stay_reliable():
    c = _cmp(TEXT * 4, (TEXT * 4).replace("Pfeffer", "Pfetter", 1))
    assert not c.unreliable and len(c.flags) == 1 and c.agreement > 0.9


def test_empty_reads():
    assert _cmp("", "").agreement == 1.0
    c = _cmp("Salz und Pfeffer", "")
    assert c.agreement == 0.0 and c.unreliable


def test_flag_lines_follow_the_primary_numbering_and_a_custom_prefix():
    c = compare_reads(2, "eins\n\nzwei drei\nvier", "eins\nzwei dreI\nvier", id_prefix="p2:image0")
    [f] = c.flags
    assert f.line == "p2:image0:L2"  # blank lines are skipped, like number_lines


def test_the_threshold_can_be_changed():
    a = "eins zwei drei vier fünf sechs sieben acht neun zehn"  # one word of ten differs: 0.9
    b = a.replace("zehn", "elf")
    assert not _cmp(a, b).unreliable
    assert _cmp(a, b, min_agreement=0.95).unreliable
