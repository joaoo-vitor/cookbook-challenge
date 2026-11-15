"""What a manipulation reports: the new recipe, and a log of everything it did and why.

The log is the explanation. Every rule adds an entry for each value it changed, for each
value it looked at and deliberately left alone (with the reason), and for each thing the
user should check by hand (a pot that may be too small, an amount that rests on a guess).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from cookbook.models import Recipe


class Change(BaseModel):
    rule: str  # "scale_servings", "make_vegan"
    kind: Literal["changed", "unchanged", "warning"]
    target: str  # an item id ("i3", "t1"), "step 2" or "servings"
    field: str  # what part of it: "quantity", "text", "duration", "size", ...
    before: str | None = None
    after: str | None = None
    reason: str  # in a sentence, for a person
    severity: Literal["high", "medium", "low"] | None = None  # for a warning: how much the person should worry


class Manipulated(BaseModel):
    recipe: Recipe  # the adapted recipe (the original is not touched)
    changes: list[Change] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)  # the rules that ran, in the order they ran
    base_servings: int | None = None
    target_servings: int | None = None
    base_is_estimate: bool = False  # the amounts rest on an estimated number of servings
