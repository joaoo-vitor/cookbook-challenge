"""Correct the line ids an item cites when its quote is in the transcript but on other lines.

The quote is the evidence; the line ids are only a pointer, and models often point at one line
when the quote runs on to the next ("8 1/2x4 1/2x2 1/2-" / "inch loaf pan"). When the quote
is found, the pointer is fixed in plain code and the correction is reported as a low issue,
with what the model had cited. Nothing is changed when the quote is not found anywhere: that
stays an `unsupported_quote` for the checks to report.
"""

from __future__ import annotations

import re

from cookbook.models import Issue, Recipe

from .transcript import Transcript
from .validate import MIN_SUPPORT, quote_support

MAX_WINDOW = 4  # the most consecutive lines one quote may span


def _find_lines(quote: str, tr: Transcript, anchor: list[str]) -> list[str] | None:
    """The shortest run of consecutive lines that holds the quote, nearest to where it was cited."""
    order = list(tr.lines)
    where = [order.index(i) for i in anchor if i in tr.lines]
    centre = sum(where) / len(where) if where else 0.0
    for size in range(1, MAX_WINDOW + 1):
        best = None  # (score, -distance, window)
        for start in range(len(order) - size + 1):
            window = order[start:start + size]
            score = quote_support(quote, tr.text_of(window))
            if score >= MIN_SUPPORT:
                candidate = (round(score, 3), -abs(start + (size - 1) / 2 - centre), window)
                if best is None or candidate[:2] > best[:2]:
                    best = candidate
        if best:
            return best[2]
    return None


_LINE_ID = re.compile(r"\s*\bp\d+(?::image\d+)?:L\d+\b\s*")  # "p1:L14", "p3:image0:L2"


def _items(recipe: Recipe) -> list:
    """(reference, item) for everything that carries a quote."""
    items = ([(i.id, i) for i in recipe.ingredients] + [(t.id, t) for t in recipe.tools]
             + [(f"step {s.n}", s) for s in recipe.steps])
    if recipe.servings.quote:
        items.append(("servings", recipe.servings))
    return items


def clean_quotes(recipe: Recipe) -> tuple[Recipe, list[Issue]]:
    """Take out the line ids a model copied into a quote from the numbered transcript it was given.

    The transcript is given to the model as "p1:L14<tab>text", and a quote that runs over two lines
    can pick up the id of the second ("1/2 l p1:L14 Wasser"). The id is not part of the recipe's
    words, so the quote would not be found. Only the id is removed, and a low issue records it.
    """
    fixed = recipe.model_copy(deep=True)
    issues: list[Issue] = []
    for ref, item in _items(fixed):
        if item.quote and _LINE_ID.search(item.quote):
            cleaned = " ".join(_LINE_ID.sub(" ", item.quote).split())
            issues.append(Issue(code="quote_cleaned", severity="low", refers_to=ref, lines=item.lines,
                                message=f"the quote contained a line id copied from the prompt: "
                                        f"{item.quote!r} became {cleaned!r}"))
            item.quote = cleaned
    return fixed, issues


def _describe(cited: list[str], found: list[str]) -> str:
    """What was wrong with a citation, in a sentence."""
    if cited and set(cited) < set(found):  # the model cited part of the quote's lines
        added = [i for i in found if i not in cited]
        return f"the quote also runs onto {', '.join(added)}; the model cited only {', '.join(cited)}: corrected"
    return f"the model cited {', '.join(cited) or 'no lines'}, but the quote is on {', '.join(found)}: corrected"


def repair_citations(recipe: Recipe, tr: Transcript) -> tuple[Recipe, list[Issue]]:
    """A copy of the recipe with corrected line ids, and a low issue for each correction."""
    fixed = recipe.model_copy(deep=True)
    issues: list[Issue] = []
    for ref, item in _items(fixed):
        quote = item.quote or ""
        if not quote.strip():
            continue
        known = [i for i in item.lines if i in tr.lines]
        if known and quote_support(quote, tr.text_of(known)) >= MIN_SUPPORT:
            continue  # the cited lines hold the quote: nothing to fix
        found = _find_lines(quote, tr, known)
        if found and found != item.lines:
            issues.append(Issue(code="lines_corrected", severity="low", refers_to=ref, lines=found,
                                message=_describe(item.lines, found)))
            item.lines = found
    return fixed, issues
