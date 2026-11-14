"""Tests for the structure checks. A made-up recipe is checked against its transcript, then errors are planted."""

import pytest

from cookbook.extract.compare import compare_reads
from cookbook.extract.transcribe import PageTranscript
from cookbook.extract.transcript import Note, Transcript, build_transcript
from cookbook.extract.validate import quote_support, validate
from cookbook.ingest.pdf import Strategy
from cookbook.models import Ingredient, Recipe, Servings, Step, Tool

LINES = [
    "Rindergulasch",
    "Für 4 Personen nimmt man 500 g Rindfleisch, 2 Zwiebeln,",
    "etwas Paprika und Salz. Das Fleisch in Würfel schnei-",
    "den und in einem Topf mit Fett anbraten. Die Zwiebeln",
    "dazugeben und 1 1/2 Stunden schmoren lassen.",
    "Wer mag, gibt am Ende einen Löffel saure Sahne dazu.",
    "12",
]


def _transcript() -> Transcript:
    t = Transcript()
    for n, text in enumerate(LINES, start=1):
        t.lines[f"p1:L{n}"] = text
    return t


def _recipe() -> Recipe:
    return Recipe(
        title="Rindergulasch", language="de",
        servings=Servings(value=4, stated=True, quote="Für 4 Personen", lines=["p1:L2"]),
        ingredients=[
            Ingredient(id="i1", name="Rindfleisch", name_en="beef", quantity=500, quantity_text="500 g",
                       unit="g", quote="500 g Rindfleisch", lines=["p1:L2"]),
            Ingredient(id="i2", name="Zwiebeln", name_en="onions", quantity=2, quantity_text="2",
                       quote="2 Zwiebeln", lines=["p1:L2"]),
            Ingredient(id="i3", name="Paprika", name_en="paprika", quantity_text="etwas",
                       quote="etwas Paprika", lines=["p1:L3"]),
            Ingredient(id="i4", name="Salz", name_en="salt", quote="Paprika und Salz", lines=["p1:L3"]),
            Ingredient(id="i5", name="saure Sahne", name_en="sour cream", quantity=1, quantity_text="einen Löffel",
                       unit="Löffel", optional=True, quote="einen Löffel saure Sahne", lines=["p1:L6"]),
        ],
        tools=[
            Tool(id="t1", name="Topf", name_en="pot", quote="in einem Topf", lines=["p1:L4"]),
            Tool(id="t2", name="Messer", name_en="knife", implied=True, quote="in Würfel schneiden",
                 lines=["p1:L3", "p1:L4"]),
        ],
        steps=[
            Step(n=1, text="Das Fleisch in Würfel schneiden", ingredient_ids=["i1", "i3", "i4"], tool_ids=["t2"],
                 quote="Das Fleisch in Würfel schneiden", lines=["p1:L3", "p1:L4"]),
            Step(n=2, text="in einem Topf mit Fett anbraten", ingredient_ids=["i1"], tool_ids=["t1"],
                 quote="in einem Topf mit Fett anbraten", lines=["p1:L4"]),
            Step(n=3, text="Die Zwiebeln dazugeben und 1 1/2 Stunden schmoren lassen", ingredient_ids=["i2"],
                 duration_text="1 1/2 Stunden",
                 quote="Die Zwiebeln dazugeben und 1 1/2 Stunden schmoren lassen", lines=["p1:L4", "p1:L5"]),
            Step(n=4, text="am Ende saure Sahne dazu", ingredient_ids=["i5"],
                 quote="am Ende einen Löffel saure Sahne dazu", lines=["p1:L6"]),
        ],
    )


def _codes(issues):
    return [i.code for i in issues]


def _find(issues, code, refers_to=None):
    return [i for i in issues if i.code == code and (refers_to is None or i.refers_to == refers_to)]


def _with_ingredient(recipe, ing_id, **changes):
    r = recipe.model_copy(deep=True)
    ing = next(i for i in r.ingredients if i.id == ing_id)
    for k, v in changes.items():
        setattr(ing, k, v)
    return r


# --- a correct extraction ------------------------------------------------------
def test_a_correct_extraction_only_notes_the_missing_amount_of_salt():
    issues = validate(_recipe(), _transcript())
    assert [(i.code, i.refers_to, i.severity) for i in issues] == [("no_quantity", "i4", "low")]


def test_a_quote_may_span_a_line_end_hyphen_and_ignore_case_and_punctuation():
    t = _transcript()
    assert quote_support("fleisch in WÜRFEL schneiden", t.text_of(["p1:L3", "p1:L4"])) == 1.0
    assert quote_support("500 g Rindfleisch", t.text_of(["p1:L2"])) == 1.0


@pytest.mark.parametrize("quote", [
    "1 teaspoon pre-\npared mustard",   # the model kept the line break
    "1 teaspoon pre- pared mustard",    # ... or flattened it to a space
    "1 teaspoon prepared mustard",      # ... or joined it
])
def test_a_quote_with_a_hyphenated_line_break_still_matches(quote):
    t = Transcript()
    t.lines = {"p1:L1": "1 teaspoon pre-", "p1:L2": "pared mustard"}
    assert quote_support(quote, t.text_of(["p1:L1", "p1:L2"])) == 1.0


def test_a_hyphenated_word_inside_a_line_is_not_damaged():
    assert quote_support("a well- known cook", "a well-known cook") == 1.0
    assert quote_support("a well-known cook", "a well-known cook") == 1.0


def test_the_hyphen_fix_does_not_make_an_invented_quote_match():
    assert quote_support("2 cups pre-\npared flour", "1 teaspoon prepared mustard") < 0.5


def test_a_number_inside_the_unit_counts_as_covered():
    t = Transcript()
    t.lines = {"p1:L1": "Ham Loaf", "p1:L2": "1 9-ounce can pineapple"}
    r = Recipe(
        servings=Servings(),
        ingredients=[Ingredient(id="i1", name="pineapple", name_en="pineapple", quantity=1, quantity_text="1",
                                unit="9-ounce can", quote="1 9-ounce can pineapple", lines=["p1:L2"])],
        steps=[Step(n=1, text="add pineapple", ingredient_ids=["i1"], quote="1 9-ounce can pineapple",
                    lines=["p1:L2"])])
    codes = _codes(validate(r, t))
    assert "uncovered_number" not in codes and "unknown_unit" not in codes


def test_quote_support_measures_the_share_found_in_order():
    assert quote_support("Rindfleisch und Pilze", "Rindfleisch und Salz") == pytest.approx(2 / 3)
    assert quote_support("", "text") == 0.0
    assert quote_support("etwas anderes", "gar nichts davon") == 0.0


# --- planted errors: each must be caught ---------------------------------------
def test_an_invented_ingredient_is_flagged_unsupported():
    r = _recipe()
    r.ingredients.append(Ingredient(id="i6", name="Champignons", name_en="mushrooms", quantity=250,
                                    quantity_text="250 g", unit="g", quote="250 g Champignons", lines=["p1:L3"]))
    issues = validate(r, _transcript())
    assert _find(issues, "unsupported_quote", "i6")[0].severity == "high"
    assert _find(issues, "unused_ingredient", "i6")


def test_a_changed_number_is_flagged():
    issues = validate(_with_ingredient(_recipe(), "i1", quantity=600), _transcript())
    [issue] = _find(issues, "quantity_mismatch", "i1")
    assert issue.severity == "high" and "600" in issue.message


def test_a_quantity_with_no_words_behind_it_is_flagged_as_invented():
    issues = validate(_with_ingredient(_recipe(), "i4", quantity=3), _transcript())
    assert _find(issues, "invented_quantity", "i4")[0].severity == "high"


def test_a_dropped_quantity_is_caught_by_the_number_coverage_check():
    r = _with_ingredient(_recipe(), "i1", quantity=None, quantity_text=None, unit=None, quote="Rindfleisch")
    [issue] = _find(validate(r, _transcript()), "uncovered_number")
    assert issue.refers_to == "p1:L2" and "500" in issue.message and issue.severity == "high"


def test_a_dropped_duration_is_caught_too():
    r = _recipe()
    r.steps[2].duration_text = None
    r.steps[2].text = "Die Zwiebeln dazugeben"
    r.steps[2].quote = "Die Zwiebeln dazugeben"
    r.steps[2].lines = ["p1:L4"]  # no longer cites the line that holds "1 1/2"
    assert "uncovered_number" in _codes(validate(r, _transcript()))


def test_a_number_that_is_only_in_the_quote_is_not_covered():
    # the model summarised the step and dropped "1 1/2 Stunden", though its quote still has them
    r = _recipe()
    r.steps[2].text = "Die Zwiebeln dazugeben und schmoren lassen"
    r.steps[2].duration_text = None
    [issue] = _find(validate(r, _transcript()), "uncovered_number")
    assert issue.refers_to == "p1:L5" and issue.severity == "high"


def _numbered_list():
    t = Transcript()
    t.lines = {"p1:L1": "1. Mix the flour and the salt.",
               "p1:L2": "2. Heat the pan and cook for 2 minutes until golden.",
               "p1:L3": "3. Tag danach servieren."}  # an ordinal inside the text, not a list label
    r = Recipe(steps=[
        Step(n=1, text="Mix the flour and the salt", quote="Mix the flour and the salt", lines=["p1:L1"]),
        Step(n=2, text="Heat the pan and cook for 2 minutes until golden", duration_text="2 minutes",
             quote="Heat the pan and cook for 2 minutes", lines=["p1:L2"]),
    ])
    return r, t


def test_the_numbers_of_a_numbered_list_are_labels_not_dropped_quantities():
    r, t = _numbered_list()
    del t.lines["p1:L3"]
    assert not _find(validate(r, t), "uncovered_number")


def test_a_list_label_that_is_not_the_steps_number_is_still_checked():
    r, t = _numbered_list()
    del t.lines["p1:L3"]
    r.steps[0].n = 5  # the step is not number 1, so "1." on its line is a real number nothing covers
    assert [i.refers_to for i in _find(validate(r, t), "uncovered_number")] == ["p1:L1"]


def test_an_ordinal_that_starts_a_line_in_the_middle_of_a_step_is_still_checked():
    r, t = _numbered_list()
    r.steps[1].lines = ["p1:L2", "p1:L3"]  # the second line "3. Tag ..." continues step 2, it is not its start
    assert [i.refers_to for i in _find(validate(r, t), "uncovered_number")] == ["p1:L3"]


def test_a_number_before_the_first_extracted_line_is_only_a_medium_issue():
    t = _transcript()
    t.lines = {"p0:L1": "Anno 1887", **t.lines}  # a header the recipe does not use
    [issue] = _find(validate(_recipe(), t), "uncovered_number")
    assert issue.refers_to == "p0:L1" and issue.severity == "medium" and "header" in issue.message


def test_a_page_number_alone_on_a_line_needs_no_coverage():
    assert not _find(validate(_recipe(), _transcript()), "uncovered_number")


def test_a_wrong_unit_is_flagged():
    assert _find(validate(_with_ingredient(_recipe(), "i1", unit="kg"), _transcript()), "unit_mismatch", "i1")


def test_an_unknown_unit_is_flagged_as_not_convertible():
    r = _with_ingredient(_recipe(), "i1", unit="Zwiebel", quantity_text="500 Zwiebel")
    assert _find(validate(r, _transcript()), "unknown_unit", "i1")


def test_a_number_written_in_words_the_parser_does_not_know_is_not_called_wrong():
    # Spanish "dos tazas" in a recipe marked Spanish: no digits, and no table for the language
    r = _with_ingredient(_recipe(), "i1", quantity=2, quantity_text="dos", unit="tazas", quote="500 g Rindfleisch")
    r.language = "es"
    issues = validate(r, _transcript())
    [note] = _find(issues, "amount_not_verified", "i1")
    assert note.severity == "low" and "dos" in note.message
    assert not _find(issues, "quantity_mismatch", "i1")
    assert _find(issues, "unknown_unit", "i1")  # the unit is still reported


def test_portuguese_amounts_are_verified_when_the_recipe_is_marked_portuguese():
    base = _with_ingredient(_recipe(), "i1", quantity=2, quantity_text="duas", unit="xícaras", quote="500 g Rindfleisch")
    base.language = "pt-BR"
    ok = validate(base, _transcript())
    assert not _find(ok, "amount_not_verified", "i1") and not _find(ok, "quantity_mismatch", "i1")
    assert not _find(ok, "unknown_unit", "i1")
    wrong = _with_ingredient(base, "i1", quantity=3)  # "duas" is 2
    assert _find(validate(wrong, _transcript()), "quantity_mismatch", "i1")[0].severity == "high"


def test_portuguese_half_and_spoon_units_are_verified():
    for text, quantity, unit in (("uma xícara e meia", 1.5, "xícara"), ("meia xícara", 0.5, "xícara"),
                                 ("2 colheres de sopa", 2, "colheres de sopa"), ("1 e 1/2", 1.5, "xícara")):
        r = _with_ingredient(_recipe(), "i1", quantity=quantity, quantity_text=text, unit=unit, quote="500 g Rindfleisch")
        r.language = "pt"
        codes = _codes(i for i in validate(r, _transcript()) if i.refers_to == "i1")
        assert "quantity_mismatch" not in codes and "unit_mismatch" not in codes and "unknown_unit" not in codes, text


def test_the_same_portuguese_words_are_not_verified_in_a_recipe_marked_german():
    r = _with_ingredient(_recipe(), "i1", quantity=2, quantity_text="duas", unit="xícaras", quote="500 g Rindfleisch")
    r.language = "de"  # the German table has no "duas"
    assert _find(validate(r, _transcript()), "amount_not_verified", "i1")


def test_digits_are_checked_in_any_language():
    ok = validate(_with_ingredient(_recipe(), "i1", quantity=2, quantity_text="2", unit="xícaras"), _transcript())
    assert not _find(ok, "quantity_mismatch", "i1") and not _find(ok, "amount_not_verified", "i1")
    wrong = validate(_with_ingredient(_recipe(), "i1", quantity=3, quantity_text="2", unit="xícaras"), _transcript())
    assert _find(wrong, "quantity_mismatch", "i1")[0].severity == "high"


def test_a_number_invented_for_a_vague_word_is_still_a_high_mismatch():
    r = _with_ingredient(_recipe(), "i3", quantity=3)  # quantity_text is "etwas"
    assert _find(validate(r, _transcript()), "quantity_mismatch", "i3")[0].severity == "high"


def test_a_wrong_number_in_a_known_language_is_still_a_high_mismatch():
    r = _with_ingredient(_recipe(), "i5", quantity=3, quantity_text="einen")  # "einen" means 1
    assert _find(validate(r, _transcript()), "quantity_mismatch", "i5")[0].severity == "high"


def test_a_number_in_the_amount_text_with_no_quantity_is_flagged():
    r = _with_ingredient(_recipe(), "i2", quantity=None)
    assert _find(validate(r, _transcript()), "quantity_mismatch", "i2")


def test_a_step_that_uses_a_missing_ingredient_or_tool_is_flagged():
    r = _recipe()
    r.steps[0].ingredient_ids.append("i9")
    r.steps[1].tool_ids.append("t9")
    issues = _find(validate(r, _transcript()), "unknown_reference")
    assert {i.refers_to for i in issues} == {"step 1", "step 2"} and all(i.severity == "high" for i in issues)


def test_an_ingredient_no_step_uses_is_flagged():
    r = _recipe()
    r.steps[0].ingredient_ids = ["i1", "i3"]  # salt is now unused
    assert _find(validate(r, _transcript()), "unused_ingredient", "i4")


def test_the_same_ingredient_listed_twice_is_flagged():
    r = _recipe()
    r.ingredients.append(r.ingredients[1].model_copy(update={"id": "i7"}))
    r.steps[0].ingredient_ids.append("i7")
    [issue] = _find(validate(r, _transcript()), "duplicate_ingredient")
    assert "i2" in issue.message and "i7" in issue.message


def test_a_repeated_id_is_flagged():
    r = _recipe()
    r.ingredients[1].id = "i1"
    assert _find(validate(r, _transcript()), "duplicate_id", "i1")


def test_steps_out_of_order_are_flagged():
    r = _recipe()
    r.steps[3].n = 6
    assert "step_order" in _codes(validate(r, _transcript()))


def test_a_quote_on_other_lines_than_cited_is_a_low_issue():
    r = _with_ingredient(_recipe(), "i1", lines=["p1:L5"])
    issue = _find(validate(r, _transcript()), "wrong_lines", "i1")[0]
    assert issue.severity == "low"


def test_a_cited_line_that_does_not_exist_is_flagged():
    r = _with_ingredient(_recipe(), "i1", lines=["p9:L1"])
    assert _find(validate(r, _transcript()), "unknown_line", "i1")


def test_an_item_without_a_quote_is_unsupported():
    r = _with_ingredient(_recipe(), "i1", quote="")
    assert _find(validate(r, _transcript()), "unsupported_quote", "i1")


# --- servings -------------------------------------------------------------------
def test_servings_the_recipe_does_not_state_are_marked_as_a_guess():
    r = _recipe()
    r.servings = Servings(value=4, stated=False, basis="500 g of meat usually feeds four")
    [issue] = _find(validate(r, _transcript()), "servings_inferred")
    assert "guess" in issue.message and "500 g of meat" in issue.message


def test_missing_servings_are_flagged_unknown():
    r = _recipe()
    r.servings = Servings()
    assert _find(validate(r, _transcript()), "servings_unknown")


def test_stated_servings_must_have_a_quote():
    r = _recipe()
    r.servings = Servings(value=6, stated=True, quote="Für 6 Personen", lines=["p1:L2"])
    assert _find(validate(r, _transcript()), "unsupported_quote", "servings")


# --- uncertainty from the two readings ------------------------------------------
def test_values_on_a_line_the_readings_disagreed_about_are_flagged_uncertain():
    t = _transcript()
    t.uncertain["p1:L2"] = [Note("high", "number: one reading has '4', the other '5'")]
    refs = {i.refers_to for i in _find(validate(_recipe(), t), "uncertain_reading")}
    assert refs == {"i1", "i2", "servings"}  # everything extracted from that line


def test_uncertain_reading_takes_the_highest_severity():
    t = _transcript()
    t.uncertain["p1:L6"] = [Note("medium", "word: 'Löffel' vs 'Loffel'")]
    assert _find(validate(_recipe(), t), "uncertain_reading", "i5")[0].severity == "medium"
    t.uncertain["p1:L6"].append(Note("high", "page unreliable"))
    assert _find(validate(_recipe(), t), "uncertain_reading", "i5")[0].severity == "high"


def test_issues_come_most_serious_first():
    r = _with_ingredient(_recipe(), "i1", quantity=600)
    severities = [i.severity for i in validate(r, _transcript())]
    assert severities == sorted(severities, key=["high", "medium", "low"].index)


# --- building the transcript -----------------------------------------------------
def _page(page, text, comparisons=()):
    return PageTranscript(page, Strategy.VISION, "vision", text, comparisons=list(comparisons))


def test_pages_are_joined_in_order_with_their_line_ids():
    t = build_transcript([_page(1, "eins\n\nzwei"), _page(2, "drei")])
    assert list(t.lines.items()) == [("p1:L1", "eins"), ("p1:L2", "zwei"), ("p2:L1", "drei")]
    assert t.as_prompt() == "p1:L1\teins\np1:L2\tzwei\np2:L1\tdrei"


def test_text_of_joins_hyphenated_line_ends():
    t = build_transcript([_page(1, "Der Bra-\ntenstücke gar")])
    assert t.text_of(["p1:L1", "p1:L2"]) == "Der Bratenstücke gar"


def test_a_difference_between_the_readings_makes_that_line_uncertain():
    primary = "Man nimmt 4 Zwiebeln und Salz\nPfeffer Nelken Lorbeer und Wasser dazugeben\nAlles gut aufkochen"
    cmp = compare_reads(1, primary, primary.replace("4", "5"))
    t = build_transcript([_page(1, primary, [cmp])])
    assert list(t.uncertain) == ["p1:L1"]
    assert t.uncertain["p1:L1"][0].severity == "high" and "'4'" in t.uncertain["p1:L1"][0].reason
    assert t.unreliable_pages == []


def test_an_unreliable_page_makes_every_line_uncertain():
    cmp = compare_reads(1, "Zwiebeln in Scheiben schneiden\nund kochen", "Mehl Zucker Milch\nEier verrühren")
    t = build_transcript([_page(1, "Zwiebeln in Scheiben schneiden\nund kochen", [cmp])])
    assert t.unreliable_pages == [1] and set(t.uncertain) >= {"p1:L1", "p1:L2"}
    for line_id in ("p1:L1", "p1:L2"):
        assert any(n.severity == "high" and "unreliable" in n.reason for n in t.uncertain[line_id])


def test_a_reordered_page_is_recorded_in_the_transcript():
    row = "2 cups flour 1 tablespoon sugar\n1 cup milk 1/2 teaspoon salt\n3 large eggs 1 pinch nutmeg"
    col = "2 cups flour\n1 cup milk\n3 large eggs\n1 tablespoon sugar\n1/2 teaspoon salt\n1 pinch nutmeg"
    t = build_transcript([_page(1, row, [compare_reads(1, row, col)])])
    assert t.reordered_pages[1] >= 1 and t.uncertain == {} and t.unreliable_pages == []


def test_a_reordered_page_gets_one_medium_issue_and_nothing_else():
    t = _transcript()
    t.reordered_pages = {1: 3}
    [issue] = _find(validate(_recipe(), t), "reading_order_differs")
    assert (issue.severity, issue.refers_to) == ("medium", "page 1") and "3 block" in issue.message


def test_no_reordering_no_issue():
    assert not _find(validate(_recipe(), _transcript()), "reading_order_differs")


def test_a_page_without_a_second_reading_has_no_uncertainty():
    t = build_transcript([_page(1, "eins zwei drei")])
    assert t.uncertain == {} and t.unreliable_pages == []
