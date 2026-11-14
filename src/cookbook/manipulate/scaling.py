"""Change the number of servings: a rule in plain code, with no model.

Not everything scales in proportion, and the rule says so for each value:
- most ingredients scale in proportion (meat, flour, liquids, vegetables);
- seasoning (salt, pepper, cloves, bay leaves, ...) grows more slowly than the volume, so it
  scales by factor ** 0.8, and the log tells the cook to taste and adjust;
- whole items (eggs, onions) are rounded to whole numbers, with a warning when the exact
  amount was far from a whole number;
- a vague amount ("einige", "to taste") has nothing to scale and is left as written;
- times do not grow with the amount, so they are left alone, with a reminder to check;
- the pot or pan may no longer fit: that is a warning.

Amounts are rounded to what a kitchen can measure (a quarter teaspoon, 5 g, a whole egg), and
the exact figure is in the log. The amounts written inside the text of the steps are updated
too, because a step that says "1/2 l Essig" next to an ingredient list that now says 3/4 l
would contradict itself.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from cookbook.models import Ingredient, Recipe, Servings
from cookbook.units import NUMBER, fold, format_amount, normalize_unit, parse_quantity, to_number

from .changes import Change

RULE = "scale_servings"
SEASONING_EXPONENT = 0.8  # a rule of thumb: doubling a recipe needs about 1.7 times the salt, not 2
BIGGER, SMALLER = 1.5, 1 / 1.5  # beyond these factors the pot or pan is worth a second look
WINDOW = 60  # characters after a number in a step that are searched for the ingredient's name

SEASONING_WORDS = frozenset({
    "salt", "pepper", "peppercorns", "clove", "cloves", "bay", "juniper", "cinnamon", "nutmeg", "paprika",
    "cumin", "coriander", "cardamom", "allspice", "saffron", "thyme", "rosemary", "oregano", "marjoram",
    "spice", "spices", "seasoning", "chili", "chilli", "vanilla", "ginger", "turmeric", "curry", "mustard",
})
VESSEL_WORDS = frozenset({"pot", "pan", "saucepan", "skillet", "bowl", "dish", "casserole", "tin", "tray",
                          "mould", "mold", "loaf", "roaster", "cauldron", "kettle"})

_COUNTED = {"stueck", "zehe", "scheibe", "blatt", "dose", "glas", "packung", "flasche", "kopf", "bund", "handvoll"}
_SPOONS = {"tl", "el", "loeffel", "colher_cafe", "colher_sobremesa"}
_CUPS = {"tasse", "cup", "xicara", "xicara_cafe", "copo"}


class NeedsBaseServings(ValueError):
    """The recipe has no number of servings and none was given: there is nothing to scale from."""


@dataclass
class Scaled:
    recipe: Recipe
    changes: list[Change]
    base: int
    base_is_estimate: bool


def scaling_classes(recipe: Recipe) -> dict[str, str]:
    """How each ingredient scales: "linear", "seasoning", "whole" or "to_taste", by its id.

    Decided once, from the recipe as it was read. A later substitution (a vegan stand-in)
    keeps the class of the ingredient it replaces, so the order of the rules cannot matter.
    """
    classes = {}
    for ing in recipe.ingredients:
        words = set(re.findall(r"[a-z]+", fold(ing.name_en or ing.name)))
        if ing.quantity is None:
            classes[ing.id] = "to_taste"
        elif words & SEASONING_WORDS:
            classes[ing.id] = "seasoning"
        elif ing.unit is None:
            classes[ing.id] = "whole"
        else:
            classes[ing.id] = "linear"
    return classes


def resolve_base(recipe: Recipe, base_override: int | None = None) -> tuple[int, bool]:
    """(base servings, whether it is only an estimate). A base given by the user is not an estimate."""
    if base_override:
        return base_override, False
    s = recipe.servings
    if s.value:
        return s.value, not s.stated
    raise NeedsBaseServings(
        "the recipe does not say how many people it serves and the model gave no estimate: "
        "give the number of servings it makes (for example --base-servings 4)")


def _round(value: float, unit: str | None) -> float:
    """Round to what a kitchen can measure."""
    if unit is None or unit in _COUNTED:
        return float(max(1, math.floor(value + 0.5)))
    if unit in ("g", "ml"):
        step = 5 if value >= 100 else 1 if value >= 10 else 0.5
    elif unit in ("kg", "l"):
        step = 0.05
    elif unit in ("dl", "cl", "prise"):
        step = 0.5
    elif unit in _SPOONS:
        step = 0.25
    elif unit in _CUPS:
        step = 0.125
    else:
        step = 0.25
    return round(max(step, math.floor(value / step + 0.5) * step), 6)


def _stem(name: str) -> str:
    f = fold(name)
    return f[:max(4, len(f) - 2)]  # "Zwiebeln" also finds "Zwiebel"


def _replace_amounts(text: str, scaled: dict[str, tuple[Ingredient, float, str]]) -> tuple[str, list[str]]:
    """Update, in a step's text, the amounts of the ingredients that were scaled.

    A number is replaced when its value is an old amount and the ingredient's name comes right
    after it (before the next number), as in "1/2 l Essig, 1/2 l Wasser". Returns the new text
    and the ids of the ingredients whose amounts were replaced.
    """
    matches = list(NUMBER.finditer(text))
    pieces, last, done = [], 0, []
    for i, m in enumerate(matches):
        value = to_number(m.group())
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        window = fold(text[m.end():min(end, m.end() + WINDOW)])
        candidates = [(iid, ing, new) for iid, (ing, old, new) in scaled.items()
                      if value is not None and abs(old - value) < 1e-9]
        pick = next((c for c in candidates if _stem(c[1].name) in window), None)
        if pick is None and len(candidates) == 1 and candidates[0][1].unit:
            unit = fold(candidates[0][1].unit)
            if window.lstrip().startswith(unit):  # the unit follows the number directly
                pick = candidates[0]
        if pick:
            pieces += [text[last:m.start()], pick[2]]
            last = m.end()
            done.append(pick[0])
    pieces.append(text[last:])
    return "".join(pieces), done


def scale_servings(recipe: Recipe, target: int, base_override: int | None = None,
                   classes: dict[str, str] | None = None, round_amounts: bool = True) -> Scaled:
    """The recipe for `target` servings, and a log of what changed and why.

    `base_override` is the number of servings the recipe makes, when the user knows it better
    than the recipe says. `classes` are the scaling classes decided from the original recipe;
    `round_amounts=False` keeps exact amounts (used to test the arithmetic).
    """
    if target <= 0 or (base_override is not None and base_override <= 0):
        raise ValueError("the number of servings must be positive")
    base, is_estimate = resolve_base(recipe, base_override)
    out = recipe.model_copy(deep=True)
    classes = classes or scaling_classes(recipe)
    language = recipe.language
    changes: list[Change] = []

    if is_estimate:
        why = recipe.servings.basis or "no reason was given"
        changes.append(Change(
            rule=RULE, kind="warning", target="servings", field="base", before=str(base), after=str(base),
            reason=f"the recipe does not say how many it serves: {base} is an estimate ({why}). "
                   "Every amount below rests on it; give the real number to correct them."))
    if target == base:
        changes.append(Change(rule=RULE, kind="unchanged", target="servings", field="servings",
                              before=str(base), after=str(target),
                              reason="the target equals the number the recipe makes: nothing to scale"))
        return Scaled(out, changes, base, is_estimate)

    factor = target / base
    out.servings = Servings(
        value=target, stated=False,
        basis=f"set by the user: scaled from {base} servings ({'an estimate' if is_estimate else 'as given'})")
    changes.append(Change(rule=RULE, kind="changed", target="servings", field="servings",
                          before=str(base), after=str(target),
                          reason=f"servings changed from {base} to {target}: every amount is multiplied by "
                                 f"{factor:.3g}, except as noted"))

    scaled: dict[str, tuple[Ingredient, float, str]] = {}
    for ing in out.ingredients:
        cls = classes.get(ing.id, "linear")
        if ing.quantity is None:
            if ing.quantity_text:  # a vague amount: nothing to scale
                changes.append(Change(rule=RULE, kind="unchanged", target=ing.id, field="quantity",
                                      before=ing.quantity_text, after=ing.quantity_text,
                                      reason=f"the amount is vague ({ing.quantity_text!r}): left as written"))
            continue
        unit = normalize_unit(ing.unit) if ing.unit else None
        growth = factor ** SEASONING_EXPONENT if cls == "seasoning" else factor
        exact = ing.quantity * growth
        new = _round(exact, unit) if round_amounts else exact
        parsed = parse_quantity(ing.quantity_text, language) if ing.quantity_text else None
        new_text = format_amount(new, language)
        if parsed and parsed.high is not None:  # a range such as "2-3"
            high = parsed.high * growth
            new_text += "-" + format_amount(_round(high, unit) if round_amounts else high, language)
        before = f"{ing.quantity_text or format_amount(ing.quantity, language)} {ing.unit or ''}".strip()
        after = f"{new_text} {ing.unit or ''}".strip()
        how = (f"seasoning: scaled by {factor:.3g} ** {SEASONING_EXPONENT} = {growth:.3g}, because seasoning grows "
               "more slowly than volume; taste and adjust" if cls == "seasoning"
               else f"scaled by {factor:.3g}")
        if round_amounts and abs(new - exact) > 1e-9:
            how += f" (exactly {exact:.3g}, rounded to {format_amount(new, language)})"
        changes.append(Change(rule=RULE, kind="changed", target=ing.id, field="quantity",
                              before=before, after=after, reason=how))
        if round_amounts and exact > 0 and abs(new - exact) / exact > 0.15 and (unit is None or unit in _COUNTED):
            changes.append(Change(rule=RULE, kind="warning", target=ing.id, field="quantity", before=f"{exact:.2f}",
                                  after=format_amount(new, language),
                                  reason=f"the exact amount is {exact:.2f}, but {ing.name} comes in whole pieces: "
                                         f"shown as {format_amount(new, language)}"))
        scaled[ing.id] = (ing.model_copy(), ing.quantity, new_text)
        ing.quantity, ing.quantity_text = new, new_text

    for step in out.steps:
        new_text, done = _replace_amounts(step.text, scaled)
        if new_text != step.text:
            names = ", ".join(scaled[i][0].name for i in dict.fromkeys(done))
            changes.append(Change(rule=RULE, kind="changed", target=f"step {step.n}", field="text",
                                  before=step.text, after=new_text,
                                  reason=f"the amounts of {names} in the text follow the new amounts"))
            step.text = new_text
        if step.duration_text:
            changes.append(Change(rule=RULE, kind="unchanged", target=f"step {step.n}", field="duration",
                                  before=step.duration_text, after=step.duration_text,
                                  reason="a time does not grow with the amount: check for doneness"))

    if factor >= BIGGER or factor <= SMALLER:
        for tool in out.tools:
            if set(re.findall(r"[a-z]+", fold(tool.name_en or tool.name))) & VESSEL_WORDS:
                size = "bigger" if factor >= BIGGER else "smaller"
                changes.append(Change(rule=RULE, kind="warning", target=tool.id, field="size", before=tool.name,
                                      after=tool.name,
                                      reason=f"{target} servings instead of {base}: you may need a {size} {tool.name}"))
    return Scaled(out, changes, base, is_estimate)
