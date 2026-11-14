"""Tests for correcting the line ids an item cites. The quote is evidence; the ids are only a pointer."""

from cookbook.extract.repair import repair_citations
from cookbook.extract.transcript import Transcript
from cookbook.extract.validate import validate
from cookbook.models import Ingredient, Recipe, Servings, Step, Tool

LINES = [
    "Ham Loaf (Makes 6 servings)",
    "Combine meats; add eggs, cereal,",
    "milk. Mix well; pat into 8 1/2x4 1/2x2 1/2-",
    "inch loaf pan. Mix mustard, cloves,",
    "brown sugar. Place on ham mixture.",
    "Bake at 350 degrees for 1 1/2 hours.",
]


def _transcript():
    t = Transcript()
    t.lines = {f"p1:L{n}": text for n, text in enumerate(LINES, start=1)}
    return t


def _tool(lines, quote="8 1/2x4 1/2x2 1/2-\ninch loaf pan"):
    return Tool(id="loaf_pan", name="loaf pan", name_en="loaf pan", quote=quote, lines=lines)


def _recipe(tool):
    return Recipe(servings=Servings(), tools=[tool])


def test_a_quote_that_runs_on_to_the_next_line_gets_both_lines():
    fixed, issues = repair_citations(_recipe(_tool(["p1:L3"])), _transcript())
    assert fixed.tools[0].lines == ["p1:L3", "p1:L4"]
    [issue] = issues
    assert (issue.code, issue.severity, issue.refers_to) == ("lines_corrected", "low", "loaf_pan")
    assert "p1:L3" in issue.message and "p1:L4" in issue.message  # what was cited, and what it is now


def test_the_original_recipe_is_not_changed():
    original = _recipe(_tool(["p1:L3"]))
    repair_citations(original, _transcript())
    assert original.tools[0].lines == ["p1:L3"]


def test_a_quote_on_the_wrong_line_is_moved_to_the_right_one():
    ing = Ingredient(id="i1", name="sugar", name_en="sugar", quote="brown sugar", lines=["p1:L1"])
    fixed, issues = repair_citations(Recipe(ingredients=[ing]), _transcript())
    assert fixed.ingredients[0].lines == ["p1:L5"] and [i.code for i in issues] == ["lines_corrected"]


def test_correct_citations_are_left_alone():
    fixed, issues = repair_citations(_recipe(_tool(["p1:L3", "p1:L4"])), _transcript())
    assert issues == [] and fixed.tools[0].lines == ["p1:L3", "p1:L4"]


def test_a_quote_that_is_nowhere_in_the_transcript_is_not_touched():
    fixed, issues = repair_citations(_recipe(_tool(["p1:L3"], quote="a copper preserving pan")), _transcript())
    assert issues == [] and fixed.tools[0].lines == ["p1:L3"]
    # ... so the checks still report it as possibly invented
    assert "unsupported_quote" in [i.code for i in validate(fixed, _transcript())]


def test_unknown_line_ids_are_replaced_when_the_quote_is_found():
    fixed, _ = repair_citations(_recipe(_tool(["p9:L1"])), _transcript())
    assert fixed.tools[0].lines == ["p1:L3", "p1:L4"]


def test_the_nearest_match_wins_when_a_quote_is_found_twice():
    t = Transcript()
    t.lines = {"p1:L1": "add salt", "p1:L2": "stir", "p1:L3": "add salt", "p1:L4": "serve"}
    step = Step(n=1, text="add salt", quote="add salt", lines=["p1:L4"])
    fixed, _ = repair_citations(Recipe(steps=[step]), t)
    assert fixed.steps[0].lines == ["p1:L3"]


def test_servings_and_steps_are_repaired_too():
    r = Recipe(servings=Servings(value=6, stated=True, quote="Makes 6 servings", lines=["p1:L5"]),
               steps=[Step(n=1, text="Bake", quote="Bake at 350 degrees", lines=["p1:L1"])])
    fixed, issues = repair_citations(r, _transcript())
    assert fixed.servings.lines == ["p1:L1"] and fixed.steps[0].lines == ["p1:L6"]
    assert {i.refers_to for i in issues} == {"servings", "step 1"}


def test_an_item_without_a_quote_is_skipped():
    ing = Ingredient(id="i1", name="x", name_en="x", quote="", lines=["p1:L1"])
    fixed, issues = repair_citations(Recipe(ingredients=[ing]), _transcript())
    assert issues == [] and fixed.ingredients[0].lines == ["p1:L1"]


def test_after_repair_the_checks_no_longer_report_wrong_lines():
    fixed, _ = repair_citations(_recipe(_tool(["p1:L3"])), _transcript())
    assert "wrong_lines" not in [i.code for i in validate(fixed, _transcript())]
