"""Stage 2b: turn the transcript into a structured recipe, then check it.

One model call fills the schema (ingredients, tools, steps, servings). The model is
not trusted: every value must carry a quote and line ids from the transcript, and
`validate` checks them in plain code. Where the two readings of the transcript
disagreed, the values on those lines are flagged as not certain.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from pydantic import ValidationError

from cookbook.llm import LLMResult, TextLLM
from cookbook.models import Issue, Recipe

from .transcript import Transcript
from .validate import validate

PROMPT = """You turn the transcript of a recipe into structured data. The transcript has one written line
per row, each starting with a line id (like p1:L3), a tab, then the text. Lines marked [drawing] are
labels written inside a drawing.

Rules:
- Use only what the transcript says. Never add an ingredient, a quantity or a step that is not written.
- Every ingredient, tool, step and the servings need a `quote`: words copied exactly from the transcript
  (keep the old spelling), and `lines`: the ids of the lines the quote is on.
- Keep names, steps and notes in the language of the recipe. `name_en` is a short English label.
- Ingredients can appear anywhere in the text, also only inside the method. Include each one once. A
  drawing label that repeats an ingredient is not a separate ingredient: cite both lines.
- `quantity` is a number only if the transcript writes one. Put the words that give the amount exactly as
  written in `quantity_text` and the unit as written in `unit`. A vague amount ("some", "a little",
  "to taste", "einige", "etwas", "soviel", "nach Geschmack") gives `quantity` null, and the vague words go
  in `quantity_text`. If the text gives no amount at all, both are null. Never guess an amount.
- `optional` is true when the text presents the ingredient as something you may add or leave out
  ("auch kann man", "wer mag", "nach Belieben", "if you like", "optional", "can also be added").
  Otherwise it is false.
- Keep the amounts and conditions in the step text: if a step depends on a time or a number written in
  the transcript ("after 2 days", "for 20 minutes"), the step's `text` and `duration_text` must keep it.
  Do not merge steps that have different times or conditions into one.
- Tools are things like pots, pans and utensils. If the text names one, `implied` is false. If it only
  follows from an action (a knife for cutting), `implied` is true and the quote is the words that imply it.
- Steps follow the order of the recipe, numbered from 1. List the ids of the ingredients and tools each
  step uses. Copy durations and conditions as written ("nach 8 Tagen", "wenn er knusprig ist").
- Servings: if the text says how many people it serves, `stated` is true with its quote. If not, `stated`
  is false: give a best estimate in `value` with the reason in `basis`, or null if you cannot estimate.
- `language` is the ISO code of the recipe's language.
Answer with the JSON only."""


@dataclass
class Structured:
    recipe: Recipe
    issues: list[Issue]
    calls: list[LLMResult] = field(default_factory=list)  # the model calls made (more than one if a retry was needed)


class StructureError(RuntimeError):
    """The model's answer could not be read as a recipe, even after one retry."""


def _parse(text: str) -> Recipe:
    return Recipe.model_validate(json.loads(text))


def structure_recipe(transcript: Transcript, llm: TextLLM, retries: int = 1) -> Structured:
    """Fill the schema from the transcript, then check the result against it."""
    prompt, calls = PROMPT, []
    for attempt in range(retries + 1):
        result = llm.structure(transcript.as_prompt(), prompt, Recipe)
        calls.append(result)
        try:
            recipe = _parse(result.text)
            break
        except (ValueError, ValidationError) as err:  # bad JSON or wrong shape (json errors are ValueErrors)
            if attempt == retries:
                raise StructureError(f"the model's answer is not a valid recipe: {str(err)[:300]}") from err
            prompt = (PROMPT + f"\n\nYour previous answer was not valid: {str(err)[:400]}\n"
                      "Answer again with corrected JSON only.")
    return Structured(recipe, validate(recipe, transcript), calls)
