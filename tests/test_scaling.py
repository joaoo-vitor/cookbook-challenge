"""Tests for changing the number of servings. A made-up recipe, no model, no network."""

import json
import sys

import pytest

from cookbook.manipulate import NeedsBaseServings, manipulate, scale_servings, scaling_classes
from cookbook.manipulate.__main__ import main
from cookbook.manipulate.scaling import _round
from cookbook.models import Ingredient, Recipe, Servings, Step, Tool
from cookbook.units import format_amount


def _ing(id, name, name_en, quantity=None, text=None, unit=None):
    return Ingredient(id=id, name=name, name_en=name_en, quantity=quantity, quantity_text=text, unit=unit,
                      quote=name, lines=["p1:L1"])


def _recipe(servings=4, stated=True, language="de"):
    return Recipe(
        title="Sauerbraten (made up)", language=language,
        servings=Servings(value=servings, stated=stated, basis=None if stated else "a typical roast feeds four"),
        ingredients=[
            _ing("i1", "Essig", "vinegar", 0.5, "1/2", "l"),
            _ing("i2", "Wasser", "water", 0.5, "1/2", "l"),
            _ing("i3", "Zwiebeln", "onions", 4, "4"),
            _ing("i4", "Salz", "salt", 1, "1", "TL"),
            _ing("i5", "Pfeffer", "pepper"),                                  # no amount at all
            _ing("i6", "Zucker", "sugar", None, "soviel"),                    # a vague amount
            _ing("i7", "Eier", "eggs", 2, "2"),
            _ing("i8", "Mehl", "flour", 250, "250", "g"),
        ],
        tools=[Tool(id="t1", name="Topf", name_en="pot", quote="Topf", lines=["p1:L1"]),
               Tool(id="t2", name="Messer", name_en="knife", implied=True, quote="schneiden", lines=["p1:L1"])],
        steps=[
            Step(n=1, text="Zum Einlegen kocht man 1/2 l Essig, 1/2 l Wasser, 4 in Scheiben geschnittene Zwiebeln, "
                           "Pfeffer und 1 TL Salz.", ingredient_ids=["i1", "i2", "i3", "i4", "i5"], quote="x"),
            Step(n=2, text="Soll der Braten nach 8 Tagen zubereitet werden, so gießt man es kalt darauf.",
                 duration_text="nach 8 Tagen", tool_ids=["t1"], quote="x"),
            Step(n=3, text="Mit 2 Eiern und 250 g Mehl binden.", ingredient_ids=["i7", "i8"], quote="x"),
        ],
    )


def _by_id(recipe):
    return {i.id: i for i in recipe.ingredients}


def _changes(result, **match):
    return [c for c in result.changes if all(getattr(c, k) == v for k, v in match.items())]


# --- the arithmetic -----------------------------------------------------------------------
def test_most_ingredients_scale_in_proportion_and_are_rounded_to_what_a_kitchen_measures():
    r = manipulate(_recipe(), servings=6)  # a factor of 1.5
    ing = _by_id(r.recipe)
    assert ing["i1"].quantity == 0.75 and ing["i1"].quantity_text == "3/4"
    assert ing["i3"].quantity == 6 and ing["i3"].quantity_text == "6"
    assert ing["i8"].quantity == 375 and ing["i8"].quantity_text == "375"
    assert ing["i7"].quantity == 3


def test_seasoning_grows_more_slowly_than_the_volume():
    r = manipulate(_recipe(), servings=8)  # a factor of 2
    salt = _by_id(r.recipe)["i4"]
    assert salt.quantity == 1.75  # 2 ** 0.8 = 1.74, rounded to a quarter teaspoon
    [change] = _changes(r, target="i4", field="quantity", kind="changed")
    assert "seasoning" in change.reason and "taste" in change.reason
    assert _by_id(r.recipe)["i8"].quantity == 500  # flour: in proportion


def test_the_exact_arithmetic_without_rounding():
    r = manipulate(_recipe(), servings=8, round_amounts=False)
    ing = _by_id(r.recipe)
    assert ing["i4"].quantity == pytest.approx(2 ** 0.8)
    assert ing["i8"].quantity == pytest.approx(500) and ing["i1"].quantity == pytest.approx(1.0)


def test_scaling_in_two_steps_gives_the_same_amounts_as_scaling_at_once():
    direct = manipulate(_recipe(), servings=9, round_amounts=False).recipe
    first = manipulate(_recipe(), servings=6, round_amounts=False).recipe
    again = manipulate(first, servings=9, round_amounts=False).recipe
    for a, b in zip(direct.ingredients, again.ingredients):
        assert a.quantity == pytest.approx(b.quantity), a.id


def test_a_whole_piece_that_cannot_be_split_gets_a_warning():
    recipe = _recipe()
    recipe.ingredients.append(_ing("i9", "Ei", "egg", 1, "1"))
    r = manipulate(recipe, servings=6)
    assert _by_id(r.recipe)["i9"].quantity == 2  # 1.5 eggs
    [warn] = _changes(r, target="i9", kind="warning")
    assert "1.50" in warn.reason and "whole pieces" in warn.reason


@pytest.mark.parametrize("value, unit, expected", [
    (0.375, "cup", 0.375), (1.3, "el", 1.25), (123.4, "g", 125), (13.4, "g", 13), (3.3, "g", 3.5),
    (0.53, "l", 0.55), (2.4, None, 2), (0.2, None, 1), (1.5, "prise", 1.5), (0.05, "tl", 0.25), (7.6, "stueck", 8),
])
def test_rounding(value, unit, expected):
    assert _round(value, unit) == pytest.approx(expected)


def test_a_range_is_scaled_at_both_ends():
    recipe = _recipe()
    recipe.ingredients[7] = _ing("i8", "Butter", "butter", 2, "2-3", "EL")
    ing = _by_id(manipulate(recipe, servings=6).recipe)["i8"]
    assert ing.quantity == 3 and ing.quantity_text == "3-4 1/2"


def test_the_decimal_separator_follows_the_language():
    for language, text in (("de", "2,6"), ("en", "2.6")):
        recipe = _recipe(language=language)
        recipe.ingredients[7] = _ing("i8", "Mehl", "flour", 1.3, "1,3", "kg")
        assert _by_id(manipulate(recipe, servings=8).recipe)["i8"].quantity_text == text


# --- the text of the steps ---------------------------------------------------------------------
def test_the_amounts_in_the_text_of_the_steps_follow_the_new_amounts():
    r = manipulate(_recipe(), servings=6)
    step1, step2, step3 = r.recipe.steps
    assert step1.text == ("Zum Einlegen kocht man 3/4 l Essig, 3/4 l Wasser, 6 in Scheiben geschnittene Zwiebeln, "
                          "Pfeffer und 1 1/2 TL Salz.")
    assert "8 Tagen" in step2.text  # a time is not an amount of an ingredient
    assert step3.text == "Mit 3 Eiern und 375 g Mehl binden."
    [change] = _changes(r, target="step 1", field="text")
    assert "Essig" in change.reason and "Zwiebeln" in change.reason


def test_a_number_that_is_not_an_amount_is_never_touched():
    recipe = _recipe()
    recipe.steps[1].text = "Nach 4 Tagen und 2 Stunden ziehen lassen."  # 4 and 2 are also amounts of onions and eggs
    r = manipulate(recipe, servings=6)
    assert r.recipe.steps[1].text == "Nach 4 Tagen und 2 Stunden ziehen lassen."


# --- what is left alone, and the warnings -----------------------------------------------------------
def test_a_vague_amount_is_left_as_written_and_a_missing_amount_is_not_invented():
    r = manipulate(_recipe(), servings=6)
    ing = _by_id(r.recipe)
    assert ing["i6"].quantity is None and ing["i6"].quantity_text == "soviel"
    assert ing["i5"].quantity is None and ing["i5"].quantity_text is None
    assert _changes(r, target="i6", kind="unchanged") and not _changes(r, target="i5")


def test_a_time_is_left_alone_with_a_reminder_to_check():
    r = manipulate(_recipe(), servings=6)
    [change] = _changes(r, target="step 2", field="duration")
    assert change.kind == "unchanged" and "doneness" in change.reason
    assert r.recipe.steps[1].duration_text == "nach 8 Tagen"


def test_a_pot_gets_a_warning_only_for_a_big_change():
    big = manipulate(_recipe(), servings=8)
    [warn] = _changes(big, target="t1", kind="warning")
    assert "bigger" in warn.reason
    assert not _changes(big, target="t2")  # a knife has no size
    assert "smaller" in _changes(manipulate(_recipe(), servings=2), target="t1", kind="warning")[0].reason
    assert not _changes(manipulate(_recipe(), servings=5), target="t1")  # 1.25: no warning


def test_the_servings_change_is_logged_with_the_factor():
    r = manipulate(_recipe(), servings=6)
    [change] = _changes(r, target="servings", kind="changed")
    assert (change.before, change.after) == ("4", "6") and "1.5" in change.reason
    assert (r.base_servings, r.target_servings, r.base_is_estimate, r.rules) == (4, 6, False, ["scale_servings"])
    assert r.recipe.servings.value == 6


# --- the base: stated, estimated, given -----------------------------------------------------------------
def test_an_estimated_base_is_flagged_as_the_ground_for_every_amount():
    r = manipulate(_recipe(servings=3, stated=False), servings=6)
    assert r.base_is_estimate and r.base_servings == 3
    [warn] = _changes(r, target="servings", kind="warning")
    assert "estimate" in warn.reason and "a typical roast feeds four" in warn.reason


def test_a_base_given_by_the_user_replaces_the_estimate_and_the_warning():
    r = manipulate(_recipe(servings=3, stated=False), servings=6, base_servings=4)
    assert not r.base_is_estimate and r.base_servings == 4
    assert not _changes(r, kind="warning", target="servings")
    assert _by_id(r.recipe)["i8"].quantity == 375  # scaled by 6 / 4


def test_without_any_base_there_is_nothing_to_scale_from():
    recipe = _recipe()
    recipe.servings = Servings()
    with pytest.raises(NeedsBaseServings, match="base-servings"):
        manipulate(recipe, servings=6)
    assert _by_id(manipulate(recipe, servings=6, base_servings=4).recipe)["i8"].quantity == 375


def test_scaling_to_the_same_number_changes_nothing():
    r = manipulate(_recipe(), servings=4)
    assert r.recipe.ingredients == _recipe().ingredients and r.recipe.steps == _recipe().steps
    assert [c.kind for c in r.changes] == ["unchanged"]


@pytest.mark.parametrize("target, base", [(0, None), (-2, None), (6, 0), (6, -1)])
def test_servings_must_be_positive(target, base):
    with pytest.raises(ValueError):
        manipulate(_recipe(), servings=target, base_servings=base)


def test_the_original_recipe_is_not_changed():
    original = _recipe()
    manipulate(original, servings=12)
    assert original == _recipe()


def test_asking_for_nothing_changes_nothing():
    r = manipulate(_recipe())
    assert r.recipe == _recipe() and r.changes == [] and r.rules == []


# --- how an ingredient scales ---------------------------------------------------------------------------
def test_scaling_classes():
    classes = scaling_classes(_recipe())
    assert classes == {"i1": "linear", "i2": "linear", "i3": "whole", "i4": "seasoning", "i5": "to_taste",
                       "i6": "to_taste", "i7": "whole", "i8": "linear"}


def test_a_replaced_ingredient_keeps_the_scaling_class_of_the_original():
    # the order of two rules can only not matter if a substitution cannot change how an item scales
    recipe = _recipe()
    classes = scaling_classes(recipe)
    recipe.ingredients[3].name, recipe.ingredients[3].name_en = "Meersalzflocken", "chili flakes"  # still seasoning
    recipe.ingredients[7].name, recipe.ingredients[7].name_en = "Salz", "salt"  # flour renamed to something salty
    r = scale_servings(recipe, 8, classes=classes, round_amounts=False)
    assert _by_id(r.recipe)["i8"].quantity == pytest.approx(500)  # still in proportion, as flour


# --- the formatting the rule relies on ----------------------------------------------------------------------
@pytest.mark.parametrize("value, text", [(2, "2"), (0.5, "1/2"), (1.5, "1 1/2"), (0.375, "3/8"), (1 / 3, "1/3"),
                                         (2.6, "2.6"), (4.998, "5"), (0.75, "3/4")])
def test_format_amount(value, text):
    assert format_amount(value) == text


def test_format_amount_uses_the_languages_decimal_separator():
    assert format_amount(2.6, "de") == "2,6" and format_amount(2.6, "pt-BR") == "2,6" and format_amount(2.6, "en") == "2.6"


# --- the command line ---------------------------------------------------------------------------------------------
def _run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["manipulate", *argv])
    return main()


def test_the_command_writes_the_new_recipe_and_the_log(tmp_path, monkeypatch, capsys):
    path = tmp_path / "stew.recipe.json"
    path.write_text(_recipe().model_dump_json(), encoding="utf-8")
    assert _run(monkeypatch, str(path), "--servings", "6") == 0
    shown = capsys.readouterr().out
    assert "servings: 4 -> 6" in shown and "3/4 l" in shown and "check for doneness" in shown
    new = Recipe.model_validate_json((tmp_path / "stew.servings6.recipe.json").read_text(encoding="utf-8"))
    assert new.servings.value == 6 and _by_id(new)["i8"].quantity == 375
    log = json.loads((tmp_path / "stew.servings6.changes.json").read_text(encoding="utf-8"))
    # 1.5 times: amounts changed, a vague amount and a time left alone, and the pot may be too small
    assert {c["kind"] for c in log} == {"changed", "unchanged", "warning"}


def test_the_command_says_when_the_base_is_an_estimate(tmp_path, monkeypatch, capsys):
    path = tmp_path / "stew.recipe.json"
    path.write_text(_recipe(servings=3, stated=False).model_dump_json(), encoding="utf-8")
    assert _run(monkeypatch, str(path), "--servings", "6") == 0
    assert "ESTIMATE" in capsys.readouterr().out
    assert _run(monkeypatch, str(path), "--servings", "6", "--base-servings", "4", "--out", str(tmp_path / "o")) == 0
    assert "ESTIMATE" not in capsys.readouterr().out and (tmp_path / "o" / "stew.servings6.recipe.json").exists()


def test_the_command_reports_a_missing_base_and_a_bad_file(tmp_path, monkeypatch, capsys):
    recipe = _recipe()
    recipe.servings = Servings()
    path = tmp_path / "stew.recipe.json"
    path.write_text(recipe.model_dump_json(), encoding="utf-8")
    assert _run(monkeypatch, str(path), "--servings", "6") == 1
    assert "base-servings" in capsys.readouterr().err
    bad = tmp_path / "bad.recipe.json"
    bad.write_text("not json", encoding="utf-8")
    assert _run(monkeypatch, str(bad), "--servings", "6") == 1
    assert "cannot read" in capsys.readouterr().err
