"""Tests for the cook's recipe: what is shown, in which language, and which markers it carries."""

import re

import pytest

from cookbook.manipulate import manipulate
from cookbook.manipulate.changes import Change
from cookbook.models import Ingredient, Issue, Recipe, Servings, Step, Tool
from cookbook.output import labels_for, render_markdown
from test_validate import _recipe


def _issue(severity, refers_to, message="something to check", code="x"):
    return Issue(code=code, severity=severity, refers_to=refers_to, message=message)


def _warning(target, reason, severity=None, field="size"):
    return Change(rule="scale_servings", kind="warning", severity=severity, target=target, field=field, reason=reason)


def _lines(md):
    return md.splitlines()


def test_the_recipe_is_laid_out_for_a_cook_in_the_recipes_language():
    md = render_markdown(_recipe())
    assert _lines(md)[0] == "# Rindergulasch"
    assert "## Zutaten" in md and "## Küchengeräte" in md and "## Zubereitung" in md
    assert "- 500 g Rindfleisch" in md and "- 2 Zwiebeln" in md
    assert "1. Das Fleisch in Würfel schneiden" in md and "4. am Ende saure Sahne dazu" in md
    assert "- Topf" in md and "Messer (aus dem Text abgeleitet)" in md  # an implied tool is marked
    assert md.endswith("\n") and render_markdown(_recipe()) == md  # the same input, the same text


def test_headings_follow_the_language_with_english_as_the_fallback():
    for language, ingredients, steps in [("en", "Ingredients", "Steps"), ("pt", "Ingredientes", "Modo de preparo"),
                                         ("pt-BR", "Ingredientes", "Modo de preparo"), ("fr", "Ingredients", "Steps"),
                                         (None, "Ingredients", "Steps")]:
        recipe = _recipe()
        recipe.language = language
        md = render_markdown(recipe)
        assert f"## {ingredients}" in md and f"## {steps}" in md, language
    assert labels_for("de-AT")["ingredients"] == "Zutaten"


def test_servings_stated_estimated_and_scaled():
    stated = render_markdown(_recipe())
    assert "_4 Portionen_" in stated
    guess = _recipe()
    guess.servings = Servings(value=4, stated=False, basis="a roast for four")
    assert "_4 Portionen (geschätzt)_" in render_markdown(guess)
    none = _recipe()
    none.servings = Servings()
    assert "Portionen" not in render_markdown(none)
    one = _recipe()
    one.servings = Servings(value=1, stated=True, quote="für 1", lines=["p1:L1"])
    assert "_1 Portion_" in render_markdown(one)


def test_a_scaled_recipe_says_what_it_was_scaled_from_and_whether_that_was_a_guess():
    original = _recipe()
    scaled = manipulate(original, servings=6)
    md = render_markdown(scaled.recipe, changes=scaled.changes)
    assert "_6 Portionen, umgerechnet von 4_" in md
    assert "- 750 g Rindfleisch" in md  # the amounts are the new ones
    guess = _recipe()
    guess.servings = Servings(value=4, stated=False, basis="a roast for four")
    est = manipulate(guess, servings=6)
    assert "umgerechnet von 4 (4 war eine Schätzung)" in render_markdown(est.recipe, changes=est.changes)


def test_the_decimal_comma_follows_the_language():
    recipe = _recipe()
    recipe.ingredients[0].quantity, recipe.ingredients[0].quantity_text = 0.3, None
    assert "- 0,3 g Rindfleisch" in render_markdown(recipe)
    recipe.language = "en"
    assert "- 0.3 g Rindfleisch" in render_markdown(recipe)


def test_a_missing_amount_is_said_and_a_vague_one_is_shown_as_written():
    md = render_markdown(_recipe())
    assert "- Menge nicht angegeben Salz" in md  # never an invented amount
    assert "- etwas Paprika" in md
    recipe = _recipe()
    recipe.language = "en"
    assert "- amount not given Salz" in render_markdown(recipe)


def test_a_range_is_kept_and_an_amount_typed_over_the_text_wins():
    recipe = Recipe(language="pt", ingredients=[
        Ingredient(id="i1", name="molho inglês", name_en="worcestershire", quantity=1, quantity_text="1 a 2", unit="colheres (sopa)",
                   quote="1 a 2 colheres", lines=["p1:L1"])])
    assert "- 1 a 2 colheres (sopa) molho inglês" in render_markdown(recipe)
    recipe.ingredients[0].quantity = 3  # the person changed it after it was read
    assert "- 3 colheres (sopa) molho inglês" in render_markdown(recipe)


def test_the_unit_is_not_written_twice_when_the_words_already_have_it():
    md = render_markdown(_recipe())
    assert "- einen Löffel saure Sahne" in md and "Löffel Löffel" not in md


def test_optional_ingredients_and_preparation_notes_are_marked():
    recipe = _recipe()
    recipe.ingredients[1].prep_note = "in Scheiben geschnitten"
    md = render_markdown(recipe)
    assert "- 2 Zwiebeln, in Scheiben geschnitten" in md
    assert "saure Sahne (optional)" in md


def test_a_swapped_ingredient_says_what_it_replaces():
    recipe = _recipe()
    recipe.ingredients[0].name = "Seitan"
    change = Change(rule="make_vegan", kind="changed", target="i1", field="ingredient", before="Rindfleisch", after="Seitan",
                    reason="plant-based")
    md = render_markdown(recipe, changes=[change])
    assert "- 500 g Seitan (ersetzt Rindfleisch)" in md
    assert "_Vegan · 4 Portionen_" in md
    vegan_scaled = render_markdown(recipe, changes=[change, Change(rule="scale_servings", kind="changed", target="servings",
                                                                  field="servings", before="4", after="6", reason="")])
    assert "Vegan · 4 Portionen, umgerechnet von 4" in vegan_scaled


def test_open_warnings_mark_their_item_and_fill_the_check_list():
    issues = [_issue("high", "i1", "the amount is doubtful"), _issue("medium", "step 3", "read with doubt"),
              _issue("low", "i2", "a low note"), _issue("medium", "servings", "a guess", code="servings_inferred"),
              _issue("medium", "p1:L6", "a number nothing uses")]
    md = render_markdown(_recipe(), issues=issues)
    assert "- 500 g Rindfleisch ⚠" in md and "3. Die Zwiebeln dazugeben und 1 1/2 Stunden schmoren lassen ⚠" in md
    assert "- 2 Zwiebeln ⚠" not in md  # a low note does not mark the item
    check = md.split("## ⚠ Vor dem Kochen prüfen")[1]
    assert "**hoch** · Rindfleisch: the amount is doubtful" in check
    assert "**mittel** · Schritt 3: read with doubt" in check
    assert "**mittel** · p1:L6: a number nothing uses" in check
    assert "low note" not in check and "a guess" not in check
    assert check.index("hoch") < check.index("mittel")  # most serious first


def test_change_warnings_count_and_a_warning_without_a_level_is_medium():
    changes = [_warning("t1", "4 servings instead of 2: you may need a bigger Topf"),
               _warning("step 1", "left as written: still mentions Butter", severity="high", field="text"),
               _warning("servings", "the base is an estimate", field="base"),
               Change(rule="scale_servings", kind="unchanged", target="i1", field="quantity", reason="left as written")]
    md = render_markdown(_recipe(), changes=changes)
    check = md.split("## ⚠ Vor dem Kochen prüfen")[1]
    assert "**mittel** · Topf: 4 servings instead of 2" in check
    assert "**hoch** · Schritt 1: left as written" in check
    assert "estimate" not in check and "left as written" in check
    assert "- Topf ⚠" in md and "1. Das Fleisch in Würfel schneiden ⚠" in md
    assert check.index("hoch") < check.index("mittel")


def test_there_is_no_check_list_when_nothing_is_open():
    assert "Vor dem Kochen" not in render_markdown(_recipe(), issues=[_issue("low", "i1")])
    assert "⚠" not in render_markdown(_recipe())


def test_the_same_warning_is_listed_once():
    twice = [_issue("high", "i1", "the amount is doubtful")] * 2
    assert render_markdown(_recipe(), issues=twice).count("the amount is doubtful") == 1


def test_inputs_are_not_changed():
    recipe, issues = _recipe(), [_issue("high", "i1")]
    before = recipe.model_dump()
    render_markdown(recipe, issues=issues, changes=[_warning("t1", "x")])
    assert recipe.model_dump() == before and issues == [_issue("high", "i1")]


def test_special_characters_in_the_text_cannot_become_markup():
    recipe = Recipe(title="*Mama's* _best_", steps=[Step(n=1, text="Mix 2 * 3\nlines # and _this_", quote="x", lines=[])],
                    tools=[Tool(id="t1", name="a_b", name_en="x", quote="x", lines=[])])
    md = render_markdown(recipe)
    assert _lines(md)[0] == r"# \*Mama's\* \_best\_"
    assert r"1. Mix 2 \* 3 lines # and \_this\_" in md and r"- a\_b" in md


def test_the_command_finds_the_change_log_and_the_original_issues_next_to_the_recipe(tmp_path):
    from cookbook.output.__main__ import _original_stem, main

    result = manipulate(_recipe(), servings=6)
    (tmp_path / "gulasch.issues.json").write_text(
        "[" + _issue("high", "i1", "the amount is doubtful").model_dump_json() + "]", encoding="utf-8")
    (tmp_path / "gulasch.servings6.recipe.json").write_text(result.recipe.model_dump_json(), encoding="utf-8")
    (tmp_path / "gulasch.servings6.changes.json").write_text(
        "[" + ",".join(c.model_dump_json() for c in result.changes) + "]", encoding="utf-8")
    assert main([str(tmp_path / "gulasch.servings6.recipe.json")]) == 0
    md = (tmp_path / "gulasch.servings6.md").read_text(encoding="utf-8")
    assert "umgerechnet von 4" in md and "the amount is doubtful" in md and "- 750 g Rindfleisch ⚠" in md
    assert [_original_stem(s) for s in ("x", "x.vegan", "x.servings6", "x.vegan.servings6")] == ["x"] * 4


def test_the_command_works_with_only_a_recipe_and_reports_a_bad_file(tmp_path, capsys):
    from cookbook.output.__main__ import main

    (tmp_path / "a.recipe.json").write_text(_recipe().model_dump_json(), encoding="utf-8")
    assert main([str(tmp_path / "a.recipe.json"), "--out", str(tmp_path / "x.md")]) == 0
    assert (tmp_path / "x.md").read_text(encoding="utf-8").startswith("# Rindergulasch")
    (tmp_path / "bad.recipe.json").write_text("not json", encoding="utf-8")
    assert main([str(tmp_path / "bad.recipe.json")]) == 1 and "cannot read" in capsys.readouterr().err


def test_only_the_simple_markdown_the_page_can_show_is_used():
    md = render_markdown(_recipe(), issues=[_issue("high", "i1")], changes=[_warning("t1", "x")])
    for line in _lines(md):
        assert (line == "" or re.fullmatch(r"#{1,2} .+|- .+|\d+\. .+|_.+_", line)), line
    assert "|" not in md and "```" not in md and "<" not in md
