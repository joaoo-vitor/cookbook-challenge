"""The structured recipe: what the extract stage produces and later stages work on.

Written in the source language of the recipe (a German recipe keeps its German
words). Each ingredient and tool also has a short English label (`name_en`) that
rules like "make it vegan" match on.

Every value must be traceable: each item carries a verbatim `quote` from the
transcript and the ids of the lines it came from. Nothing is invented: an
ingredient without an amount has `quantity = None`, and servings the recipe never
states are marked `stated = False`.

The model fills these classes; plain code then checks them (see extract/validate.py).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Ingredient(BaseModel):
    id: str  # "i1", "i2", ...
    name: str  # as written in the source
    name_en: str  # short English label for rules (vegan, scaling)
    quantity: float | None = None  # the number given in the source; None if it gives none
    quantity_text: str | None = None  # the words that give the amount, exactly as written ("1/2 l", "einige")
    unit: str | None = None  # as written ("l", "Pfund")
    prep_note: str | None = None  # "in Scheiben geschnitten"
    optional: bool = False  # only if the source says so ("kann man", "nach Belieben")
    quote: str  # verbatim words from the transcript that support this ingredient
    lines: list[str] = Field(default_factory=list)  # transcript line ids, like "p1:L13"


class Tool(BaseModel):
    id: str  # "t1", ...
    name: str
    name_en: str
    implied: bool = False  # True when the recipe never names it (a knife for "in Scheiben schneiden")
    quote: str  # the words that name or imply it
    lines: list[str] = Field(default_factory=list)


class Step(BaseModel):
    n: int  # 1, 2, ... in the order of the recipe
    text: str  # the step, as in the source
    ingredient_ids: list[str] = Field(default_factory=list)
    tool_ids: list[str] = Field(default_factory=list)
    duration_text: str | None = None  # "eine Stunde", "nach 8 Tagen"
    condition: str | None = None  # "wenn er knusprig ist"
    quote: str
    lines: list[str] = Field(default_factory=list)


class Servings(BaseModel):
    value: int | None = None
    stated: bool = False  # False: the recipe does not say; `value`, if any, is a guess
    basis: str | None = None  # why the guess, when not stated
    quote: str | None = None
    lines: list[str] = Field(default_factory=list)


class Recipe(BaseModel):
    title: str | None = None
    language: str | None = None  # "de", "en", ...
    servings: Servings = Field(default_factory=Servings)
    ingredients: list[Ingredient] = Field(default_factory=list)
    tools: list[Tool] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)


Severity = Literal["high", "medium", "low"]


class Issue(BaseModel):
    """Something a reviewer should look at, found by a check (not by the model)."""

    code: str  # short stable name, like "unsupported_quote"
    severity: Severity
    message: str
    refers_to: str | None = None  # an item id ("i3", "t1"), "step 2", "servings"
    lines: list[str] = Field(default_factory=list)
