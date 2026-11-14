"""Check an extracted recipe against the transcript, in plain code.

The model fills the schema; these checks do not trust it. They find values with no
support in the transcript, amounts that do not match their words, numbers the
recipe has but the extraction dropped, broken references, and values that sit on
lines the two readings disagreed about. Each problem is an `Issue`; nothing is
dropped or corrected silently.
"""

from __future__ import annotations

import difflib
import re

from cookbook.models import Issue, Recipe
from cookbook.units import normalize_fractions, normalize_unit, numbers_in, parse_quantity

from .transcript import Transcript

MIN_SUPPORT = 0.8  # share of a quote's words that must be found, in order, in the transcript
_TOKEN = re.compile(r"\d+/\d+|\w+|[½¼¾⅓⅔⅛]")
_ORDER = {"high": 0, "medium": 1, "low": 2}


def _tokens(text: str) -> list[str]:
    text = re.sub(r"\[drawing\]|\[\?|\?\]", " ", normalize_fractions(text.lower()))
    return _TOKEN.findall(re.sub(r"[‐‑–—]", "-", text))


def quote_support(quote: str, text: str) -> float:
    """Share of the quote's words found, in order, in the text (0 to 1).

    The transcript's hyphenated line ends are joined ("pre-" + "pared" = "prepared"), but a model
    copying a quote often keeps the break ("pre-\\npared" or "pre- pared"). So the quote is tried as
    written and with such breaks joined, and the better match counts.
    """
    t = _tokens(text)
    best = 0.0
    for candidate in (quote, re.sub(r"(?<=[^\W\d_])-\s*\n\s*(?=[^\W\d_])|(?<=[a-zäöüß])-\s+(?=[a-zäöüß])", "", quote)):
        q = _tokens(candidate)
        if q:
            blocks = difflib.SequenceMatcher(None, t, q, autojunk=False).get_matching_blocks()
            best = max(best, sum(b.size for b in blocks) / len(q))
    return best


def validate(recipe: Recipe, transcript: Transcript) -> list[Issue]:
    """All problems found, most serious first."""
    issues: list[Issue] = []
    items = ([(i.id, i.quote, i.lines, i) for i in recipe.ingredients]
             + [(t.id, t.quote, t.lines, t) for t in recipe.tools]
             + [(f"step {s.n}", s.quote, s.lines, s) for s in recipe.steps])
    if recipe.servings.quote or recipe.servings.stated:
        items.append(("servings", recipe.servings.quote or "", recipe.servings.lines, recipe.servings))

    for ref, quote, lines, _ in items:
        issues += _check_support(ref, quote, lines, transcript)
        issues += _check_uncertainty(ref, lines, transcript)
    for ing in recipe.ingredients:
        issues += _check_amount(ing)
    issues += _check_references(recipe)
    issues += _check_duplicates(recipe)
    issues += _check_servings(recipe)
    issues += _check_coverage(recipe, transcript)
    return sort_issues(issues)


def sort_issues(issues: list[Issue]) -> list[Issue]:
    """Most serious first, otherwise in the order found."""
    return sorted(issues, key=lambda i: _ORDER[i.severity])


def _check_support(ref: str, quote: str, lines: list[str], tr: Transcript) -> list[Issue]:
    """The quote must be in the transcript, on the lines the item cites."""
    out = []
    unknown = [i for i in lines if i not in tr.lines]
    if unknown:
        out.append(Issue(code="unknown_line", severity="medium", refers_to=ref, lines=unknown,
                         message=f"cites line(s) {', '.join(unknown)} that are not in the transcript"))
    known = [i for i in lines if i in tr.lines]
    if not quote.strip():
        return out + [Issue(code="unsupported_quote", severity="high", refers_to=ref, lines=lines,
                            message="no quote from the transcript supports this")]
    if known and quote_support(quote, tr.text_of(known)) >= MIN_SUPPORT:
        return out
    if quote_support(quote, tr.full_text()) >= MIN_SUPPORT:
        return out + [Issue(code="wrong_lines", severity="low", refers_to=ref, lines=lines,
                            message="the quote is in the transcript, but not on the lines cited")]
    return out + [Issue(code="unsupported_quote", severity="high", refers_to=ref, lines=lines,
                        message=f"the quote {quote!r} is not in the transcript: possibly invented")]


def _check_uncertainty(ref: str, lines: list[str], tr: Transcript) -> list[Issue]:
    """A value on a line where the two readings disagreed is not certain."""
    notes = [(i, n) for i in lines for n in tr.uncertain.get(i, [])]
    if not notes:
        return []
    severity = "high" if any(n.severity == "high" for _, n in notes) else "medium"
    reasons = "; ".join(dict.fromkeys(f"{i}: {n.reason}" for i, n in notes))
    return [Issue(code="uncertain_reading", severity=severity, refers_to=ref,
                  lines=sorted({i for i, _ in notes}), message=f"read with doubt ({reasons})")]


def _check_amount(ing) -> list[Issue]:
    out = []
    ref = ing.id
    if ing.quantity is None:
        if ing.quantity_text and numbers_in(ing.quantity_text):
            out.append(Issue(code="quantity_mismatch", severity="high", refers_to=ref, lines=ing.lines,
                             message=f"the amount text {ing.quantity_text!r} has a number but no quantity was given"))
        elif not ing.quantity_text:
            out.append(Issue(code="no_quantity", severity="low", refers_to=ref, lines=ing.lines,
                             message="the source gives no amount (it is not guessed)"))
    else:
        if not ing.quantity_text:
            out.append(Issue(code="invented_quantity", severity="high", refers_to=ref, lines=ing.lines,
                             message=f"quantity {ing.quantity:g} has no words in the source that give it"))
        else:
            parsed = parse_quantity(ing.quantity_text)
            if parsed.value is None or abs(parsed.value - ing.quantity) > 1e-6:
                out.append(Issue(code="quantity_mismatch", severity="high", refers_to=ref, lines=ing.lines,
                                 message=f"quantity {ing.quantity:g} does not match the words "
                                         f"{ing.quantity_text!r} (read as {parsed.value})"))
    if ing.unit:
        canonical = normalize_unit(ing.unit)
        if canonical is None:
            out.append(Issue(code="unknown_unit", severity="medium", refers_to=ref, lines=ing.lines,
                             message=f"unit {ing.unit!r} is not known, so it cannot be converted"))
        elif ing.quantity_text:
            in_text = parse_quantity(ing.quantity_text).unit
            if in_text and in_text != canonical:
                out.append(Issue(code="unit_mismatch", severity="high", refers_to=ref, lines=ing.lines,
                                 message=f"unit {ing.unit!r} does not match the amount text {ing.quantity_text!r}"))
    return out


def _check_references(recipe: Recipe) -> list[Issue]:
    out = []
    ing_ids = [i.id for i in recipe.ingredients]
    tool_ids = [t.id for t in recipe.tools]
    for kind, ids in (("ingredient", ing_ids), ("tool", tool_ids)):
        for dup in sorted({i for i in ids if ids.count(i) > 1}):
            out.append(Issue(code="duplicate_id", severity="high", refers_to=dup,
                             message=f"two {kind}s have the id {dup}"))
    used: set[str] = set()
    for step in recipe.steps:
        for ref in step.ingredient_ids:
            used.add(ref)
            if ref not in ing_ids:
                out.append(Issue(code="unknown_reference", severity="high", refers_to=f"step {step.n}",
                                 lines=step.lines, message=f"uses ingredient {ref}, which is not in the list"))
        for ref in step.tool_ids:
            if ref not in tool_ids:
                out.append(Issue(code="unknown_reference", severity="high", refers_to=f"step {step.n}",
                                 lines=step.lines, message=f"uses tool {ref}, which is not in the list"))
    for ing in recipe.ingredients:
        if ing.id not in used:
            out.append(Issue(code="unused_ingredient", severity="medium", refers_to=ing.id, lines=ing.lines,
                             message=f"{ing.name!r} is not used in any step"))
    numbers = [s.n for s in recipe.steps]
    if numbers != list(range(1, len(numbers) + 1)):
        out.append(Issue(code="step_order", severity="low",
                         message=f"steps are numbered {numbers}, not 1 to {len(numbers)} in order"))
    return out


def _check_duplicates(recipe: Recipe) -> list[Issue]:
    seen: dict[str, list[str]] = {}
    for ing in recipe.ingredients:
        seen.setdefault(re.sub(r"\W+", " ", (ing.name_en or ing.name).lower()).strip(), []).append(ing.id)
    return [Issue(code="duplicate_ingredient", severity="medium", refers_to=ids[0],
                  message=f"the same ingredient may be listed more than once ({', '.join(ids)}): merge or check")
            for ids in seen.values() if len(ids) > 1]


def _check_servings(recipe: Recipe) -> list[Issue]:
    s = recipe.servings
    if s.value is None:
        return [Issue(code="servings_unknown", severity="medium", refers_to="servings",
                      message="the recipe does not say how many people it serves; scaling needs a base")]
    if not s.stated:
        return [Issue(code="servings_inferred", severity="medium", refers_to="servings",
                      message=f"servings ({s.value}) are a guess" + (f": {s.basis}" if s.basis else ""))]
    return []


def _check_coverage(recipe: Recipe, tr: Transcript) -> list[Issue]:
    """Every number in the transcript must appear in what was extracted from that line.

    This catches dropped quantities and durations, which the other checks cannot see
    because they only look at what the model chose to extract. Only the structured
    fields count (amounts, step text, durations, conditions, servings), not the quotes:
    a quote proves where a value came from, not that it was extracted.

    A number on a line before the first or after the last line any item cites is probably
    a header or footer (a year, a book title), so it is only a medium issue.
    """
    out = []
    texts: dict[str, list[str]] = {}  # line id -> texts of the items that cite it
    cited: set[str] = set()

    def add(lines, *parts):
        cited.update(lines)
        for line in lines:
            texts.setdefault(line, []).extend(p for p in parts if p)

    for i in recipe.ingredients:
        add(i.lines, i.quantity_text, i.unit, f"{i.quantity:g}" if i.quantity is not None else None)
    for t in recipe.tools:
        add(t.lines)
    for s in recipe.steps:
        add(s.lines, s.text, s.duration_text, s.condition)
    add(recipe.servings.lines, str(recipe.servings.value) if recipe.servings.value is not None else None)

    order = list(tr.lines)
    inside = [order.index(i) for i in cited if i in tr.lines]
    first, last = (min(inside), max(inside)) if inside else (0, len(order) - 1)
    # The number of a numbered list ("1. Mix ...") is a label, not a quantity. It is excused only
    # when it equals the step's own number and sits on the step's first line, so an ordinal in
    # the middle of a step ("... nach dem 2. Tag") still counts.
    step_label: dict[str, int] = {}
    for s in recipe.steps:
        known = [i for i in s.lines if i in tr.lines]
        if known:
            step_label.setdefault(min(known, key=order.index), s.n)
    for position, (line_id, line) in enumerate(tr.lines.items()):
        if re.fullmatch(r"\s*\d+\s*", line):  # a page number
            continue
        covered = {n for text in texts.get(line_id, []) for n in numbers_in(text)}
        body = line
        marker = re.match(r"\s*\(?(\d{1,3})[.)]\s+", line)
        if marker and step_label.get(line_id) == int(marker.group(1)):
            body = line[marker.end():]
        missing = [n for n in numbers_in(body) if n not in covered]
        if missing:
            shown = ", ".join(f"{n:g}" for n in missing)
            outside = position < first or position > last
            out.append(Issue(
                code="uncovered_number", severity="medium" if outside else "high", refers_to=line_id, lines=[line_id],
                message=f"the transcript has {shown} on this line, but nothing extracted from it has that number"
                        + (" (the line is outside the extracted text, maybe a header or footer)" if outside else "")))
    return out
