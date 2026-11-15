"""Tests for the vegan rule: the table, the checks on the model's answer, and the engine.

The model is replaced by a fake that returns canned plans, so each check is shown to reject its own
planted fault, and nothing is called over the network.
"""

import json

import pytest

from cookbook.llm import LLMResult
from cookbook.manipulate import NeedsModel, VeganError, make_vegan, manipulate, scaling_classes
from cookbook.manipulate.vegan_table import classify, derived_in, find_animal
from cookbook.models import Ingredient, Recipe, Servings, Step


# --- the table -------------------------------------------------------------------
@pytest.mark.parametrize("name_en, name, category, stand_in", [
    ("beef", "Rindfleisch", "meat", "seitan"),
    ("roast", "Braten", "meat", "seitan"),
    ("bacon", "Speck", "meat", "smoked tofu"),
    ("ground beef", "Hackfleisch", "meat", "soy mince"),
    ("chicken stock", "Hühnerbrühe", "meat", "vegetable stock"),
    ("chicken breast", "peito de frango", "poultry", "soy chunks"),
    ("salmon", "Lachs", "fish", "marinated tofu"),
    ("shrimp", "camarão", "seafood", "king oyster mushrooms"),
    ("butter", "Butter", "dairy", "vegan butter"),
    ("butter", "manteiga", "dairy", "vegan butter"),
    ("buttermilk", "Buttermilch", "dairy", "oat milk with a little lemon juice"),
    ("sour cream", "saure Sahne", "dairy", "plant cream"),
    ("cream", "creme de leite", "dairy", "plant cream"),
    ("milk", "Milch", "dairy", "oat milk"),
    ("parmesan", "Käse", "dairy", "vegan cheese"),
    ("egg", "Ei", "egg", "egg replacer"),
    ("eggs", "Eier", "egg", "egg replacer"),
    ("egg", "ovo", "egg", "egg replacer"),
    ("honey", "Honig", "honey", "maple syrup"),
    ("honey", "mel", "honey", "maple syrup"),
    ("gelatine", "Gelatine", "other", "agar-agar"),
    ("lard", "Schmalz", "other", "vegetable oil"),
    ("worcestershire sauce", "molho inglês", "other", "vegan Worcestershire sauce"),
])
def test_animal_ingredients_are_found_in_english_german_and_portuguese(name_en, name, category, stand_in):
    entry = classify(name_en, name)
    assert entry is not None and (entry.category, entry.stand_in) == (category, stand_in)


@pytest.mark.parametrize("name_en, name", [
    ("peanut butter", "Erdnussbutter"), ("coconut milk", "Kokosmilch"), ("almond milk", "Mandelmilch"),
    ("oat milk", "Hafermilch"), ("eggplant", "Aubergine"), ("butternut squash", "Butternusskürbis"),
    ("cocoa butter", "Kakaobutter"), ("cream of tartar", "Weinstein"), ("vegetable stock", "Gemüsebrühe"),
    ("lemon rind", "Zitronenrinde"), ("iron", "Eisen"), ("coconut cream", "creme de coco"),
    ("vegan butter", "vegane Butter"), ("plant-based milk", "pflanzliche Milch"),
    ("salt", "Salz"), ("water", "Wasser"), ("onions", "Zwiebeln"), ("flour", "Mehl"),
    ("red wine", "Rotwein"), ("sugar", "Zucker"), ("vinegar", "Essig"),  # not treated, by decision
])
def test_things_that_only_look_animal_or_are_left_alone_are_not_flagged(name_en, name):
    assert classify(name_en, name) is None


def test_the_more_specific_entry_wins():
    assert classify("ground beef", "").category == "meat" and classify("ground beef", "").stand_in == "soy mince"
    assert classify("buttermilk", "").stand_in.startswith("oat milk with")
    assert classify("chicken stock", "").stand_in == "vegetable stock"


def test_animal_words_are_found_in_a_step_but_not_inside_other_words():
    assert find_animal("Zum Anbraten den Speck auslassen, Butter dazugeben") == ["speck", "butter"]
    assert find_animal("Wenn er von allen Seiten knusprig angebraten ist") == []  # the verb, not the roast
    assert find_animal("Die Zwiebeln hacken und in Scheiben schneiden") == []
    assert find_animal("Mit Eisen und Reis servieren") == []  # "Ei" only as a whole word


def test_a_stand_ins_own_name_and_vegan_phrases_are_allowed():
    assert find_animal("vegane Butter dazugeben") == []
    assert find_animal("Butter dazugeben", allowed=["Butter"]) == []
    assert find_animal("1/2 l Hafermilch und Erdnussbutter") == []
    assert find_animal("Mit Kokosmilch aufgießen und Mandelmilch zugeben") == []


def test_the_stand_ins_in_the_table_are_not_animal_themselves():
    from cookbook.manipulate.vegan_table import TABLE

    for entry in TABLE:
        assert find_animal(entry.stand_in) == [], entry.stand_in


def test_animal_derived_words_are_found_in_a_step():
    assert derived_in("Für die Soße schwitzt man den Bratenfond mit Mehl an") == ["bratenfond"]
    assert derived_in("Mit dem Bratensatz ablöschen") == ["bratensatz"]
    assert derived_in("Mit Mehl anschwitzen") == []


# --- the rule ---------------------------------------------------------------------
class FakeLLM:
    name = "fake"

    def __init__(self, *answers):
        self.answers, self.prompts, self.texts = list(answers), [], []

    def structure(self, text, prompt, schema):
        self.texts.append(text)
        self.prompts.append(prompt)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return LLMResult(answer if isinstance(answer, str) else json.dumps(answer), "fake-model", 10, 10, 0.0)


def _small(extra=False) -> Recipe:
    ingredients = [
        Ingredient(id="i1", name="Butter", name_en="butter", quantity=2, quantity_text="2", unit="EL",
                   quote="2 EL Butter", lines=["p1:L1"]),
        Ingredient(id="i2", name="Mehl", name_en="flour", quantity=100, quantity_text="100", unit="g",
                   quote="100 g Mehl", lines=["p1:L2"]),
        Ingredient(id="i3", name="Salz", name_en="salt", quote="Salz", lines=["p1:L3"]),
    ]
    steps = [
        Step(n=1, text="2 EL Butter in der Pfanne schmelzen.", ingredient_ids=["i1"], quote="2 EL Butter", lines=["p1:L1"]),
        Step(n=2, text="100 g Mehl einrühren und 5 Minuten rühren.", ingredient_ids=["i2"], duration_text="5 Minuten",
             quote="100 g Mehl", lines=["p1:L2"]),
        Step(n=3, text="Mit Salz abschmecken.", ingredient_ids=["i3"], quote="Mit Salz", lines=["p1:L3"]),
    ]
    if extra:
        ingredients.append(Ingredient(id="i4", name="Gruyère", name_en="gruyere", optional=True, quote="Gruyère", lines=["p1:L4"]))
        steps.append(Step(n=4, text="Mit Gruyère überbacken.", ingredient_ids=["i4"], quote="Gruyère", lines=["p1:L4"]))
    return Recipe(title="Mehlschwitze", language="de", servings=Servings(value=2, stated=True, quote="für 2", lines=["p1:L1"]),
                  ingredients=ingredients, steps=steps)


def _swap(iid, name, en, category="dairy", reason="an animal product"):
    return {"id": iid, "category": category, "substitute_name": name, "substitute_en": en, "reason": reason}


GOOD = {"swaps": [_swap("i1", "vegane Butter", "vegan butter")],
        "steps": [{"n": 1, "text": "2 EL vegane Butter in der Pfanne schmelzen."}]}


def _changes(result, kind=None, target=None, severity=None):
    return [c for c in result.changes if (kind is None or c.kind == kind) and (target is None or c.target == target)
            and (severity is None or c.severity == severity)]


def test_an_animal_ingredient_is_swapped_and_its_step_rewritten():
    recipe, llm = _small(), FakeLLM(GOOD)
    result = make_vegan(recipe, llm)
    butter = result.recipe.ingredients[0]
    assert (butter.name, butter.name_en) == ("vegane Butter", "vegan butter")
    assert (butter.id, butter.quantity, butter.unit, butter.quote, butter.lines) == ("i1", 2, "EL", "2 EL Butter", ["p1:L1"])
    assert result.recipe.steps[0].text == "2 EL vegane Butter in der Pfanne schmelzen."
    assert result.recipe.steps[1] == recipe.steps[1] and result.recipe.steps[2] == recipe.steps[2]  # untouched
    assert recipe.ingredients[0].name == "Butter" and recipe.steps[0].text.startswith("2 EL Butter")  # the original stays
    assert len(llm.prompts) == 1


def test_every_change_is_in_the_log_with_its_reason():
    result = make_vegan(_small(), FakeLLM(GOOD))
    [swap] = _changes(result, "changed", "i1")
    assert (swap.rule, swap.field, swap.before, swap.after) == ("make_vegan", "ingredient", "Butter", "vegane Butter")
    [step] = _changes(result, "changed", "step 1")
    assert step.before.startswith("2 EL Butter") and step.after.startswith("2 EL vegane Butter") and "numbers" in step.reason
    [kept] = _changes(result, "unchanged")
    assert kept.after == "Mehl, Salz"


def test_the_model_gets_the_table_verdicts_and_the_animal_words():
    llm = FakeLLM(GOOD)
    make_vegan(_small(), llm)
    sent = json.loads(llm.texts[0])
    assert sent["language"] == "de"
    assert sent["ingredients"][0]["table"] == {"category": "dairy", "substitute_en": "vegan butter"}
    assert sent["ingredients"][1]["table"] is None
    assert sent["steps"][0]["animal_words"] == ["butter"] and sent["steps"][1]["animal_words"] == []


def test_the_table_stand_in_wins_over_the_models():
    plan = {"swaps": [_swap("i1", "vegane Butter", "ghee")], "steps": GOOD["steps"]}  # the model chose another one
    result = make_vegan(_small(), FakeLLM(plan))
    assert result.recipe.ingredients[0].name_en == "vegan butter"


def test_an_ingredient_the_table_flags_but_the_model_leaves_out_is_asked_for_again():
    llm = FakeLLM({"swaps": [], "steps": []}, GOOD)
    result = make_vegan(_small(), llm)
    assert len(llm.prompts) == 2 and "ingredient i1" in llm.prompts[1] and "stand-in name" in llm.prompts[1]
    assert result.recipe.ingredients[0].name == "vegane Butter"
    assert not _changes(result, "warning", severity="high")


def test_if_the_model_never_names_the_stand_in_the_table_stand_in_is_used_with_a_warning():
    result = make_vegan(_small(), FakeLLM({"swaps": [], "steps": []}))
    assert result.recipe.ingredients[0].name == "vegan butter"
    assert _changes(result, "warning", "i1", "medium")
    assert _changes(result, "warning", "step 1", "high")  # and the step still names the butter


def test_a_rewrite_that_changes_a_number_is_rejected_then_the_step_is_left_as_written():
    bad = {"swaps": GOOD["swaps"], "steps": [{"n": 1, "text": "3 EL vegane Butter in der Pfanne schmelzen."}]}
    llm = FakeLLM(bad)
    result = make_vegan(_small(), llm)
    assert "numbers changed" in llm.prompts[1]  # the second try was told why
    assert result.recipe.steps[0].text == "2 EL Butter in der Pfanne schmelzen."  # original kept
    [warn] = _changes(result, "warning", "step 1", "high")
    assert "numbers" in warn.reason and "by hand" in warn.reason
    assert result.recipe.ingredients[0].name == "vegane Butter"  # the ingredient list is still vegan


def test_a_rewrite_that_still_names_the_animal_ingredient_is_rejected():
    bad = {"swaps": GOOD["swaps"], "steps": [{"n": 1, "text": "2 EL Butter in der Pfanne schmelzen und zugeben."}]}
    result = make_vegan(_small(), FakeLLM(bad))
    assert result.recipe.steps[0].text.startswith("2 EL Butter in der Pfanne schmelzen.")
    [warn] = _changes(result, "warning", "step 1", "high")
    assert "still mentions" in warn.reason


def test_a_step_the_model_forgot_to_rewrite_is_a_high_warning():
    result = make_vegan(_small(), FakeLLM({"swaps": GOOD["swaps"], "steps": []}))
    [warn] = _changes(result, "warning", "step 1", "high")
    assert "not rewritten" in warn.reason


def test_a_much_longer_rewrite_is_rejected():
    bad = {"swaps": GOOD["swaps"], "steps": [{"n": 1, "text": "2 EL vegane Butter " + "in der Pfanne schmelzen " * 12}]}
    result = make_vegan(_small(), FakeLLM(bad))
    assert result.recipe.steps[0].text.startswith("2 EL Butter") and _changes(result, "warning", "step 1", "high")


def test_a_wrong_rewrite_is_fixed_by_the_second_answer():
    bad = {"swaps": GOOD["swaps"], "steps": [{"n": 1, "text": "9 EL vegane Butter in der Pfanne schmelzen."}]}
    llm = FakeLLM(bad, GOOD)
    result = make_vegan(_small(), llm)
    assert len(llm.prompts) == 2 and result.recipe.steps[0].text == "2 EL vegane Butter in der Pfanne schmelzen."
    assert not _changes(result, "warning", severity="high")


def test_a_stand_in_that_is_itself_animal_is_rejected():
    plan = {"swaps": [_swap("i4", "Käse", "cheese")], "steps": []}
    llm = FakeLLM(plan)
    result = make_vegan(_small(extra=True), llm)
    assert result.recipe.ingredients[3].name == "Gruyère"  # not changed
    [warn] = [c for c in _changes(result, "warning", "i4", "high")]
    assert "itself animal" in warn.reason and "by hand" in warn.reason


def test_an_ingredient_only_the_model_knows_is_swapped_with_a_medium_note():
    plan = {"swaps": GOOD["swaps"] + [_swap("i4", "Hefeflocken", "nutritional yeast", reason="a cheese")],
            "steps": GOOD["steps"] + [{"n": 4, "text": "Mit Hefeflocken überbacken."}]}
    result = make_vegan(_small(extra=True), FakeLLM(plan))
    gruyere = result.recipe.ingredients[3]
    assert (gruyere.name, gruyere.optional) == ("Hefeflocken", True)  # swapped and still optional
    [note] = _changes(result, "warning", "i4", "medium")
    assert "not in the table" in note.reason
    assert result.recipe.steps[3].text == "Mit Hefeflocken überbacken."


def test_a_step_that_needs_no_change_cannot_be_rewritten_wrongly():
    plan = {"swaps": GOOD["swaps"], "steps": GOOD["steps"] + [{"n": 3, "text": "Mit 9 Prisen Salz abschmecken."}]}
    result = make_vegan(_small(), FakeLogging := FakeLLM(plan))
    assert result.recipe.steps[2].text == "Mit Salz abschmecken."  # the bad optional rewrite is ignored
    assert len(FakeLogging.prompts) == 1 and not _changes(result, "warning", severity="high")


def test_derived_things_are_reported_and_must_go_with_the_meat():
    recipe = _small()
    recipe.steps[2].text = "Den Bratenfond mit Salz abschmecken."
    plan = {"swaps": GOOD["swaps"], "steps": GOOD["steps"] + [{"n": 3, "text": "Die Brühe mit Salz abschmecken."}]}
    result = make_vegan(recipe, FakeLLM(plan))
    assert result.recipe.steps[2].text == "Die Brühe mit Salz abschmecken."
    assert [c.before for c in _changes(result, "warning", "step 3", "medium")] == ["bratenfond"]
    # and a rewrite that keeps it is refused
    keep = {"swaps": GOOD["swaps"], "steps": GOOD["steps"] + [{"n": 3, "text": "Den Bratenfond kräftig mit Salz abschmecken."}]}
    again = make_vegan(recipe, FakeLLM(keep))
    assert again.recipe.steps[2].text == "Den Bratenfond mit Salz abschmecken." and _changes(again, "warning", "step 3", "high")


def test_a_new_word_that_still_starts_with_the_old_ingredients_name_is_refused():
    roast = Recipe(language="de", ingredients=[Ingredient(id="i1", name="Braten", name_en="roast", quote="Braten", lines=["p1:L1"])],
                   steps=[Step(n=1, text="Den Bratenfond mit Mehl anschwitzen.", ingredient_ids=["i1"], quote="Bratenfond", lines=["p1:L1"])])
    swaps = [_swap("i1", "Seitan", "seitan", "meat")]
    bad = {"swaps": swaps, "steps": [{"n": 1, "text": "Den Bratensud mit Mehl anschwitzen."}]}
    llm = FakeLLM(bad)
    result = make_vegan(roast, llm)
    assert "do not contain the old ingredient's name" in llm.prompts[1]
    assert result.recipe.steps[0].text == "Den Bratenfond mit Mehl anschwitzen." and _changes(result, "warning", "step 1", "high")
    good = {"swaps": swaps, "steps": [{"n": 1, "text": "Den Sud mit Mehl anschwitzen."}]}
    assert make_vegan(roast, FakeLLM(good)).recipe.steps[0].text == "Den Sud mit Mehl anschwitzen."
    # the verb "angebraten" is not the roast
    verb = {"swaps": swaps, "steps": [{"n": 1, "text": "Den Sud, wenn er angebraten ist, mit Mehl anschwitzen."}]}
    assert make_vegan(roast, FakeLLM(verb)).recipe.steps[0].text.startswith("Den Sud, wenn er angebraten")


def test_meat_stand_ins_get_a_loud_warning_and_other_stand_ins_do_not():
    meat = Recipe(language="de", ingredients=[Ingredient(id="i1", name="Rindfleisch", name_en="beef", quantity=500, quantity_text="500",
                                                         unit="g", quote="500 g Rindfleisch", lines=["p1:L1"])],
                  steps=[Step(n=1, text="Das Fleisch anbraten.", ingredient_ids=["i1"], quote="Fleisch", lines=["p1:L1"])])
    plan = {"swaps": [_swap("i1", "Seitan", "seitan", "meat")], "steps": [{"n": 1, "text": "Den Seitan anbraten."}]}
    result = make_vegan(meat, FakeLLM(plan))
    [warn] = _changes(result, "warning", "recipe", "medium")
    assert "Rindfleisch → Seitan" in warn.after and "cooking time" in warn.reason
    assert not _changes(make_vegan(_small(), FakeLLM(GOOD)), "warning", "recipe")


def test_a_recipe_with_nothing_animal_is_left_as_it_is_and_says_so():
    recipe = _small()
    recipe.ingredients.pop(0)
    recipe.steps.pop(0)
    result = make_vegan(recipe, FakeLLM({"swaps": [], "steps": []}))
    assert result.recipe == recipe
    [kept] = _changes(result, "unchanged")
    assert "nothing to change" in kept.reason and not _changes(result, "changed")


def test_an_unreadable_answer_gets_one_retry_then_an_error():
    ok = make_vegan(_small(), FakeLLM("not json", GOOD))
    assert ok.recipe.ingredients[0].name == "vegane Butter"
    with pytest.raises(VeganError):
        make_vegan(_small(), FakeLLM("not json", "still not json"))


def test_a_model_is_required():
    with pytest.raises(NeedsModel):
        make_vegan(_small(), None)
    with pytest.raises(NeedsModel):
        manipulate(_small(), vegan=True)


# --- with the servings rule, through the engine -------------------------------------
def test_vegan_and_servings_together_scale_the_stand_in_and_its_step_text():
    result = manipulate(_small(), servings=4, vegan=True, llm=FakeLLM(GOOD))
    assert result.rules == ["make_vegan", "scale_servings"]  # always this order
    butter = result.recipe.ingredients[0]
    assert (butter.name, butter.quantity) == ("vegane Butter", 4)
    assert result.recipe.steps[0].text == "4 EL vegane Butter in der Pfanne schmelzen."  # new name, new amount
    assert result.recipe.ingredients[1].quantity == 200
    assert result.base_servings == 2 and result.target_servings == 4


def test_a_stand_in_keeps_the_scaling_class_of_the_ingredient_it_replaces():
    original = _small()
    result = manipulate(original, vegan=True, llm=FakeLLM(GOOD))
    assert scaling_classes(result.recipe) == scaling_classes(original)


def test_only_vegan_or_only_servings_work_too():
    assert manipulate(_small(), vegan=True, llm=FakeLLM(GOOD)).rules == ["make_vegan"]
    assert manipulate(_small(), servings=4).rules == ["scale_servings"]  # no model needed without vegan
