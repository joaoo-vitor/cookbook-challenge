"""Tests for amounts and units: parsing what a recipe writes, and converting known units."""

import pytest

from cookbook.units import BaseAmount, normalize_fractions, normalize_unit, numbers_in, parse_quantity, to_base


@pytest.mark.parametrize("text, expected", [
    ("¾ cup", "3/4 cup"),
    ("½ l", "1/2 l"),
    ("1½ Tassen", "1 1/2 Tassen"),
    ("1 ½ Tassen", "1 1/2 Tassen"),
    ("⅓ und ⅔", "1/3 und 2/3"),
    ("3/4 cup", "3/4 cup"),  # already digits
    ("keine Brüche", "keine Brüche"),
])
def test_normalize_fractions(text, expected):
    assert normalize_fractions(text) == expected


@pytest.mark.parametrize("text, value, unit", [
    ("4", 4, None),
    ("1/2", 0.5, None),
    ("½ l", 0.5, "l"),
    ("1 1/2 Pfund", 1.5, "pfund"),
    ("1½ Tassen", 1.5, "tasse"),
    ("1,5 kg", 1.5, "kg"),
    ("1.5 kg", 1.5, "kg"),
    ("1.000 g", 1000, "g"),
    ("250 g", 250, "g"),
    ("ein Pfund", 1, "pfund"),
    ("ein halbes Pfund", 0.5, "pfund"),
    ("zweieinhalb Tassen", 2.5, "tasse"),
    ("anderthalb Liter", 1.5, "l"),
    ("drei Zehen", 3, "zehe"),
    ("a pinch", 1, "prise"),
    ("two cups", 2, "cup"),
    ("half a cup", 0.5, "cup"),
    ("3 tbsp", 3, "el"),
    ("2 EL", 2, "el"),
])
def test_parse_quantity(text, value, unit):
    q = parse_quantity(text)
    assert q.value == pytest.approx(value) and q.unit == unit and q.vague is None


def test_a_range_keeps_both_ends():
    for text in ("2-3 EL", "2 bis 3 EL", "2–3 EL"):
        q = parse_quantity(text)
        assert (q.value, q.high, q.unit) == (2, 3, "el")


@pytest.mark.parametrize("text, vague", [
    ("einige", "einige"),
    ("nach Geschmack", "nach geschmack"),
    ("soviel", "soviel"),
    ("etwas", "etwas"),
    ("to taste", "to taste"),
])
def test_a_vague_amount_has_no_number_and_is_never_guessed(text, vague):
    q = parse_quantity(text)
    assert q.value is None and q.vague == vague


def test_empty_or_unrecognised_text_gives_nothing():
    assert parse_quantity("") == parse_quantity("   ")
    q = parse_quantity("Zwiebeln")
    assert (q.value, q.vague) == (None, None)


@pytest.mark.parametrize("word, canonical", [
    ("l", "l"), ("Liter", "l"), ("ml", "ml"), ("g", "g"), ("Gramm", "g"), ("kg", "kg"),
    ("Pfund", "pfund"), ("Loth", "loth"), ("Schoppen", "schoppen"), ("Maß", "mass"),
    ("EL", "el"), ("Esslöffel", "el"), ("TL", "tl"), ("Tassen", "tasse"), ("cups", "cup"),
    ("Prise", "prise"), ("Stück", "stueck"), ("Zehen", "zehe"), ("Bund", "bund"),
])
def test_normalize_unit(word, canonical):
    assert normalize_unit(word) == canonical


def test_an_unknown_unit_is_none():
    assert normalize_unit("Zwiebel") is None and normalize_unit("") is None


@pytest.mark.parametrize("unit, canonical", [
    ("9-ounce can", "dose"),
    ("9 ounce can", "dose"),
    ("16 oz. jar", "glas"),
    ("500 g Packung", "packung"),
    ("1/2 lb package", "packung"),
    ("12-ounce bottle", "flasche"),
])
def test_a_container_with_its_size_in_front_is_counted_by_the_container(unit, canonical):
    assert normalize_unit(unit) == canonical
    assert to_base(1, unit) == BaseAmount(1, "count", False)


@pytest.mark.parametrize("unit", ["9-ounce", "1/2 l", "2 EL Butter", "9-ounce tub"])
def test_sizes_without_a_known_container_stay_unknown_or_plain(unit):
    assert normalize_unit(unit) in (None, "l", "el") and normalize_unit(unit) != "dose"


def test_exact_conversions():
    assert to_base(1, "pfund") == BaseAmount(500, "g", False)
    assert to_base(2, "kg") == BaseAmount(2000, "g", False)
    assert to_base(0.5, "l") == BaseAmount(500, "ml", False)
    assert to_base(3, "dl") == BaseAmount(300, "ml", False)
    assert to_base(250, "g") == BaseAmount(250, "g", False)


def test_historic_and_rough_units_are_marked_approximate():
    loth = to_base(2, "Loth")
    assert loth.base == "g" and loth.amount == pytest.approx(33.33, abs=0.01) and loth.approx
    assert to_base(1, "Schoppen").approx
    assert to_base(1, "EL") == BaseAmount(15, "ml", True)


def test_counted_units_do_not_convert_to_weight():
    assert to_base(3, "Zehe") == BaseAmount(3, "count", False)
    assert to_base(1, "Prise") == BaseAmount(1, "count", False)


def test_an_unknown_unit_cannot_be_converted():
    assert to_base(1, "Zwiebel") is None


@pytest.mark.parametrize("text, values", [
    ("Man nimmt 1/2 Liter und 4 Zwiebeln", [0.5, 4]),
    ("nach dem 1. Tag oder 2. Tag", [1, 2]),
    ("Zwi3beln", []),  # a digit glued into a word is not a quantity
    ("½ und ¼", [0.5, 0.25]),
    ("188", [188]),
    ("1,5 kg und 1.5 l", [1.5, 1.5]),
    ("nichts hier", []),
])
def test_numbers_in(text, values):
    assert numbers_in(text) == pytest.approx(values)
