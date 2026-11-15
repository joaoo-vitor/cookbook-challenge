"""Make a recipe vegan: the table knows what is animal, a model writes the words, code checks the model.

1. The table (`vegan_table.py`) says which ingredients are certainly animal and what to use instead.
2. One model call gets the ingredients and the steps and answers with: the ingredients that contain
   animal products (it handles those the table does not know) with a stand-in named in the recipe's
   language, and each step that mentions something animal, rewritten.
3. Plain code verifies the answer and never takes it on trust:
   - an ingredient the table flags is swapped, whatever the model said (the table's stand-in wins);
   - a stand-in must not be animal itself;
   - a rewritten step keeps every number exactly, names no animal ingredient any more (words inside the
     stand-in's own name are fine: "Butter" in "vegane Butter"), and is not far longer;
   - a wrong answer is sent back once with the list of faults. A step that still fails keeps its original
     text and gets a high warning: a wrong rewrite is never shown as a right one.

Everything done is in the change log. An ingredient keeps its id, amount, unit, optional flag and the
quote it came from, so scaling treats the stand-in like the ingredient it replaces.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, ValidationError

from cookbook.llm import LLMResult, TextLLM
from cookbook.models import Recipe
from cookbook.units import numbers_in

from .changes import Change
from .vegan_table import Entry, classify, derived_in, find_animal

RULE = "make_vegan"
MEAT = {"meat", "poultry", "fish", "seafood"}  # their stand-ins change the dish the most


class NeedsModel(ValueError):
    """Making a recipe vegan needs a model for the words, and none was given."""


class VeganError(RuntimeError):
    """The model's answer could not be read, even after one retry."""


class Swap(BaseModel):
    id: str = Field(description="The id of an ingredient that contains an animal product, such as 'i3'.")
    category: str = Field(description="meat, poultry, fish, seafood, dairy, egg, honey or other.")
    substitute_name: str = Field(description="The plant-based stand-in, named in the language of the recipe.")
    substitute_en: str = Field(description="The same stand-in in English.")
    reason: str = Field(description="Why it is replaced, in a few words.")


class StepText(BaseModel):
    n: int = Field(description="The step's number, as given.")
    text: str = Field(description="The whole step, rewritten so that nothing animal is left.")


class VeganPlan(BaseModel):
    swaps: list[Swap] = Field(default_factory=list)
    steps: list[StepText] = Field(default_factory=list)


PROMPT = """You make one recipe vegan. The input is JSON: the recipe's language, its ingredients, and its steps.
An ingredient may carry a `table` verdict: our table already knows it is animal and which stand-in to use.
`animal_words` lists the animal words found in a step.

Answer with `swaps` and `steps`.
- `swaps`: one entry for every ingredient that contains an animal product (meat, poultry, fish, seafood,
  dairy, eggs, honey, gelatine, lard and so on), and none for the others. Wine, beer, sugar and vinegar
  are not changed.
- If an ingredient has a `table` verdict, set `substitute_en` to exactly that stand-in and write its name in the
  recipe's language in `substitute_name`. Otherwise choose a common plant-based stand-in that fits the dish.
- `steps`: for every step that mentions an animal ingredient or an animal-derived thing (pan juices of a roast,
  lard), the whole step rewritten. Leave out the steps that need no change.
Rules for a rewritten step:
- Keep every number exactly as written: amounts, times, temperatures. Do not add, remove or convert numbers.
- Change only what the stand-in needs: the ingredient's name, and an action that only fits the animal product
  (rendering bacon fat becomes frying the stand-in in oil). Keep the language, the tone, the order and the rest
  of the sentence.
- Use the stand-in's name exactly as in `substitute_name`, and use it in every step that needs it.
- A thing made from the animal ingredient (the roast's pan juices) is replaced by something that fits the
  stand-in (a vegetable stock, or the fond of the pan). Never keep the animal ingredient's name inside a new
  word, and never name the old ingredient at all.
Answer with the JSON only."""


@dataclass
class Veganized:
    recipe: Recipe
    changes: list[Change]
    calls: list[LLMResult] = field(default_factory=list)


@dataclass
class _Accepted:
    name: str
    name_en: str
    category: str
    reason: str
    source: str  # "table" or "model"


@dataclass
class _Checked:
    swaps: dict[str, _Accepted] = field(default_factory=dict)
    steps: dict[int, str] = field(default_factory=dict)
    failed_swaps: dict[str, str] = field(default_factory=dict)  # ingredient id -> why
    failed_steps: dict[int, str] = field(default_factory=dict)  # step number -> why

    @property
    def problems(self) -> list[str]:
        return [f"ingredient {i}: {why}" for i, why in self.failed_swaps.items()] + \
               [f"step {n}: {why}" for n, why in self.failed_steps.items()]


def _deaccent(text: str) -> str:
    """Without accents but with the case kept: a German noun starts with a capital letter."""
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))


def _own_words(name: str) -> list[str]:
    return [w for w in re.findall(r"[^\W\d_]+", _deaccent(re.sub(r"\(.*?\)", " ", name))) if len(w) >= 4]


def _leftovers(text: str, names: list[str], allowed: list[str]) -> list[str]:
    """What is still animal in a text: table words, and the names of the ingredients that were swapped."""
    t = _deaccent(text)
    for phrase in allowed:
        if phrase.strip():
            t = re.sub(re.escape(_deaccent(phrase)), " ", t, flags=re.I)
    found = find_animal(t)
    for word in names:  # a capitalised word is a German noun: "Braten" the roast, not "angebraten"
        if re.search(r"\b" + re.escape(word), t, 0 if word[0].isupper() else re.I):
            found.append(word)
    return found


def _payload(recipe: Recipe, entries: dict[str, Entry | None]) -> str:
    return json.dumps({
        "language": recipe.language,
        "ingredients": [{"id": i.id, "name": i.name, "name_en": i.name_en, "amount": i.quantity_text, "unit": i.unit,
                         "optional": i.optional,
                         "table": {"category": entries[i.id].category, "substitute_en": entries[i.id].stand_in}
                         if entries[i.id] else None} for i in recipe.ingredients],
        "steps": [{"n": s.n, "text": s.text, "ingredient_ids": s.ingredient_ids,
                   "animal_words": find_animal(s.text)} for s in recipe.steps],
    }, ensure_ascii=False, indent=1)


def _check(recipe: Recipe, entries: dict[str, Entry | None], plan: VeganPlan) -> _Checked:
    out = _Checked()
    by_id = {i.id: i for i in recipe.ingredients}
    for sw in plan.swaps:
        ing = by_id.get(sw.id)
        if ing is None:
            out.failed_swaps[sw.id] = "there is no ingredient with this id"
            continue
        entry = entries.get(sw.id)
        name, en = sw.substitute_name.strip(), (entry.stand_in if entry else sw.substitute_en.strip())
        if not name or not en:
            out.failed_swaps[sw.id] = "the stand-in has no name"
        elif find_animal(f"{name} {en}"):
            out.failed_swaps[sw.id] = f"the stand-in {name!r} is itself animal"
        else:
            out.swaps[sw.id] = _Accepted(name, en, entry.category if entry else sw.category, sw.reason.strip(),
                                         "table" if entry else "model")
    for iid, entry in entries.items():  # the table has the last word on what is animal
        if entry and iid not in out.swaps and iid not in out.failed_swaps:
            out.failed_swaps[iid] = f"{by_id[iid].name!r} is animal ({entry.category}): give its stand-in name"

    names = [w for iid in out.swaps for w in _own_words(by_id[iid].name)]
    allowed = [a for s in out.swaps.values() for a in (s.name, s.name_en)]
    rewrites = {s.n: s.text for s in plan.steps}
    for step in recipe.steps:
        old_leftovers = _leftovers(step.text, names, [])
        new = rewrites.get(step.n)
        if new is None or not new.strip():
            if old_leftovers:
                out.failed_steps[step.n] = f"it mentions {', '.join(old_leftovers)} and was not rewritten"
            continue
        new = new.strip()
        if new == step.text:
            if old_leftovers:
                out.failed_steps[step.n] = f"it still mentions {', '.join(old_leftovers)}"
            continue
        if sorted(numbers_in(new)) != sorted(numbers_in(step.text)):
            why = "the numbers changed: it must keep exactly the numbers of the original"
        elif _leftovers(new, names, allowed):
            why = (f"it still mentions {', '.join(_leftovers(new, names, allowed))}, also as the start of a longer word: "
                   "use words that do not contain the old ingredient's name")
        elif len(new) > 2 * len(step.text) + 60:
            why = "it is far longer than the original: change only what is needed"
        else:
            out.steps[step.n] = new
            continue
        if old_leftovers:  # a rewrite that was not needed and does not pass is ignored
            out.failed_steps[step.n] = why
    return out


def make_vegan(recipe: Recipe, llm: TextLLM | None, *, retries: int = 1) -> Veganized:
    """A vegan copy of the recipe and the log of what was done. The original is not touched."""
    if llm is None:
        raise NeedsModel("making a recipe vegan needs a model to write the new step texts")
    entries = {i.id: classify(i.name_en, i.name) for i in recipe.ingredients}
    payload, prompt, calls, checked = _payload(recipe, entries), PROMPT, [], None
    for attempt in range(retries + 1):
        result = llm.structure(payload, prompt, VeganPlan)
        calls.append(result)
        try:
            plan = VeganPlan.model_validate(json.loads(result.text))
        except (ValueError, ValidationError) as err:  # bad JSON or the wrong shape
            if attempt == retries and checked is None:
                raise VeganError(f"the model's answer is not a valid plan: {str(err)[:300]}") from err
            prompt = PROMPT + f"\n\nYour previous answer was not valid: {str(err)[:400]}\nAnswer again with corrected JSON only."
            continue
        checked = _check(recipe, entries, plan)
        if not checked.problems:
            break
        prompt = PROMPT + "\n\nYour previous answer had these faults. Fix them and answer again with the full JSON:\n- " \
            + "\n- ".join(checked.problems)
    return _apply(recipe, entries, checked, calls)


def _apply(recipe: Recipe, entries: dict[str, Entry | None], checked: _Checked, calls: list[LLMResult]) -> Veganized:
    out = recipe.model_copy(deep=True)
    changes: list[Change] = []
    by_id = {i.id: i for i in out.ingredients}
    original = {i.id: i.name for i in recipe.ingredients}

    for iid, swap in checked.swaps.items():
        ing = by_id[iid]
        ing.name, ing.name_en = swap.name, swap.name_en
        reason = swap.reason or f"it is an animal product ({swap.category})"
        changes.append(Change(rule=RULE, kind="changed", target=iid, field="ingredient", before=original[iid],
                              after=swap.name, reason=f"{reason}; stand-in: {swap.name_en}"))
        if swap.source == "model":
            changes.append(Change(rule=RULE, kind="warning", severity="medium", target=iid, field="ingredient",
                                  after=swap.name, reason=f"{original[iid]!r} is not in the table of animal ingredients: "
                                  "the model judged it animal and chose the stand-in. Check it."))
    for iid, why in checked.failed_swaps.items():
        if iid in by_id and iid in entries and entries[iid]:  # the table is sure: use its stand-in, named in English
            ing, entry = by_id[iid], entries[iid]
            ing.name = ing.name_en = entry.stand_in
            changes.append(Change(rule=RULE, kind="changed", target=iid, field="ingredient", before=original[iid],
                                  after=entry.stand_in, reason=f"it is an animal product ({entry.category}); stand-in: "
                                  f"{entry.stand_in}"))
            changes.append(Change(rule=RULE, kind="warning", severity="medium", target=iid, field="ingredient",
                                  after=entry.stand_in, reason=f"the stand-in is named in English because the model "
                                  f"gave no usable name ({why}). Rename it."))
            checked.swaps[iid] = _Accepted(entry.stand_in, entry.stand_in, entry.category, "", "table")
        else:
            changes.append(Change(rule=RULE, kind="warning", severity="high", target=iid, field="ingredient",
                                  before=original.get(iid), reason=f"not made vegan: {why}. Replace it by hand."))

    step_by_n = {s.n: s for s in out.steps}
    for n, text in checked.steps.items():
        step = step_by_n[n]
        changes.append(Change(rule=RULE, kind="changed", target=f"step {n}", field="text", before=step.text, after=text,
                              reason="rewritten to match the vegan ingredients; its numbers are unchanged"))
        for term in derived_in(step.text):
            changes.append(Change(rule=RULE, kind="warning", severity="medium", target=f"step {n}", field="text",
                                  before=term, reason=f"{term!r} comes from the animal ingredient. The rewrite replaces it: "
                                  "check that it makes sense."))
        step.text = text
    for n, why in checked.failed_steps.items():
        changes.append(Change(rule=RULE, kind="warning", severity="high", target=f"step {n}", field="text",
                              before=step_by_n[n].text, reason=f"left as written: {why}. Rewrite this step by hand."))

    stand_ins = [f"{original[i]} → {s.name}" for i, s in checked.swaps.items() if s.category in MEAT]
    if stand_ins:
        changes.append(Change(rule=RULE, kind="warning", severity="medium", target="recipe", field="stand-ins",
                              after="; ".join(stand_ins),
                              reason="A plant-based stand-in for meat or fish differs in texture, flavour and cooking time. "
                                     "Times are left as written: taste and adjust."))
    kept = [i.name for i in out.ingredients if i.id not in checked.swaps and i.id not in checked.failed_swaps]
    changes.append(Change(rule=RULE, kind="unchanged", target="ingredients", field="kept", after=", ".join(kept) or None,
                          reason="no animal product found in these (wine, beer, sugar and vinegar are not treated)"
                          if checked.swaps or checked.failed_swaps else "no animal ingredient found: nothing to change"))
    return Veganized(out, changes, calls)
