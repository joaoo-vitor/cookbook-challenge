"""Apply manipulations to a recipe, always in the same order.

Rules never depend on the order in which the user asked for them: "vegan, then 6 servings" and
"6 servings, then vegan" give the same recipe, because the engine runs them in one fixed order
(vegan first, then servings) and a substitution keeps the scaling class of the ingredient it
replaces. Everything the rules do is in the change log.
"""

from __future__ import annotations

from cookbook.models import Recipe

from .changes import Manipulated
from .scaling import scale_servings, scaling_classes

RULE_ORDER = ("make_vegan", "scale_servings")  # the order rules run in, whatever the request


def manipulate(recipe: Recipe, *, servings: int | None = None, base_servings: int | None = None,
               round_amounts: bool = True) -> Manipulated:
    """Apply the requested rules to a copy of `recipe`.

    `servings` is the number of servings wanted; `base_servings` is what the recipe makes, if the
    user knows it (it replaces the recipe's own number, stated or estimated).
    """
    classes = scaling_classes(recipe)  # decided from the original recipe, before any rule runs
    result = Manipulated(recipe=recipe.model_copy(deep=True))
    for rule in RULE_ORDER:
        if rule == "scale_servings" and servings is not None:
            scaled = scale_servings(result.recipe, servings, base_servings, classes, round_amounts)
            result.recipe = scaled.recipe
            result.changes += scaled.changes
            result.rules.append(rule)
            result.base_servings, result.target_servings = scaled.base, servings
            result.base_is_estimate = scaled.base_is_estimate
    return result
