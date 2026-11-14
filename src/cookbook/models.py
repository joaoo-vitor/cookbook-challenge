"""The structured recipe: what the extract stage produces and later stages work on.

Written in the source language of the recipe (a German recipe keeps its German
words). Each ingredient and tool also has a short English label (`name_en`) that
rules like "make it vegan" match on.

Every value must be traceable: each item carries a verbatim `quote` from the
transcript and the ids of the lines it came from. Nothing is invented: an
ingredient without an amount has `quantity = None`, and servings the recipe never
states are marked `stated = False`.

The model fills these classes; plain code then checks them (see extract/validate.py).
The `description` of each field is part of the schema the model receives, so it is
written for the model.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Ingredient(BaseModel):
    id: str = Field(description="Short id: i1, i2, i3, ... in the order the ingredients appear in the text.")
    name: str = Field(description="The ingredient as written in the source, without amount or unit.")
    name_en: str = Field(description="A short English label, such as 'sour cream'.")
    quantity: float | None = Field(
        default=None, description="The number the text gives, as a decimal (1/2 is 0.5). Null if the text gives none.")
    quantity_text: str | None = Field(
        default=None, description="The words that give the amount, exactly as written, such as '1/2' or 'einige'.")
    unit: str | None = Field(default=None, description="The unit exactly as written, such as 'l' or 'cup'.")
    prep_note: str | None = Field(default=None, description="How it is prepared, such as 'in Scheiben geschnitten'.")
    optional: bool = Field(default=False, description="True only if the text presents it as something you may add or leave out.")
    quote: str = Field(description="Words copied exactly from the transcript that show this ingredient.")
    lines: list[str] = Field(
        default_factory=list,
        description="Ids of every transcript line the quote touches, such as ['p1:L13', 'p1:L14'].")


class Tool(BaseModel):
    id: str = Field(description="Short id: t1, t2, ... in order of appearance.")
    name: str
    name_en: str = Field(description="A short English label, such as 'frying pan'.")
    implied: bool = Field(
        default=False, description="True if the text never names the tool and it only follows from an action.")
    quote: str = Field(description="Words copied exactly from the transcript that name or imply it.")
    lines: list[str] = Field(default_factory=list, description="Ids of every transcript line the quote touches.")


class Step(BaseModel):
    n: int = Field(description="1, 2, 3, ... in the order of the recipe.")
    text: str = Field(description="The step, keeping any time, number or condition the transcript gives.")
    ingredient_ids: list[str] = Field(default_factory=list, description="Ids from the ingredient list that the step uses.")
    tool_ids: list[str] = Field(default_factory=list, description="Ids from the tool list that the step uses.")
    duration_text: str | None = Field(default=None, description="A duration as written, such as 'eine Stunde'.")
    condition: str | None = Field(default=None, description="A condition as written, such as 'wenn er knusprig ist'.")
    quote: str = Field(description="Words copied exactly from the transcript for this step.")
    lines: list[str] = Field(default_factory=list, description="Ids of every transcript line the quote touches.")


class Servings(BaseModel):
    value: int | None = Field(
        default=None,
        description="Number of servings. If the text does not say, your best estimate of how many people the dish "
                    "typically serves (the user will see it as an estimate).")
    stated: bool = Field(
        default=False, description="True only if the text says how many it serves. Otherwise false, and value is an estimate.")
    basis: str | None = Field(default=None, description="When stated is false: the reasoning behind the estimate.")
    quote: str | None = Field(default=None, description="When stated is true: words copied exactly from the transcript.")
    lines: list[str] = Field(default_factory=list, description="Ids of every transcript line the quote touches.")


class Recipe(BaseModel):
    title: str | None = None
    language: str | None = Field(default=None, description="ISO code of the recipe's language, such as 'de' or 'en'.")
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
