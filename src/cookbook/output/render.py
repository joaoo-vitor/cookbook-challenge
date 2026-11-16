"""Stage 4: a recipe a person can cook from, as Markdown.

The recipe (as read, edited, or changed by the manipulations) becomes a title, a line saying how many
it serves and how sure that is, the ingredients, the tools, the steps, and a list of what to check
before cooking. The review steps show the reviewer everything; this keeps only what the cook needs, in
four markers:
- the servings: stated, estimated, or scaled from a number (which may itself have been an estimate);
- an ingredient that replaces another ("Seitan (replaces Braten)"), read from the change log;
- an ingredient the source gives no amount for: "amount not given", never an invented one;
- the open warnings: a ⚠ on the item they concern, and a list at the end (high and medium only).

Headings and markers are in the recipe's language (German, English, Portuguese; English otherwise).
The text of the recipe, and the warnings (written by the checks, in English), are not translated.
The output uses only headings, lists, **bold** and _italic_, so the web page can show it with a tiny
renderer, and the same input always gives the same text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from cookbook.manipulate.changes import Change
from cookbook.models import Ingredient, Issue, Recipe
from cookbook.units import fold, format_amount, parse_quantity

LABELS: dict[str, dict[str, str]] = {
    "en": dict(ingredients="Ingredients", tools="Tools", steps="Steps", untitled="Recipe", servings="{n} servings",
               serving="1 serving", estimated="estimated", scaled="scaled from {base}", base_estimate="{base} was an estimate",
               vegan="Vegan", no_amount="amount not given", optional="optional", implied="implied by the text",
               replaces="replaces {name}", check="Check before cooking", step="step", high="high", medium="medium"),
    "de": dict(ingredients="Zutaten", tools="Küchengeräte", steps="Zubereitung", untitled="Rezept", servings="{n} Portionen",
               serving="1 Portion", estimated="geschätzt", scaled="umgerechnet von {base}", base_estimate="{base} war eine Schätzung",
               vegan="Vegan", no_amount="Menge nicht angegeben", optional="optional", implied="aus dem Text abgeleitet",
               replaces="ersetzt {name}", check="Vor dem Kochen prüfen", step="Schritt", high="hoch", medium="mittel"),
    "pt": dict(ingredients="Ingredientes", tools="Utensílios", steps="Modo de preparo", untitled="Receita", servings="{n} porções",
               serving="1 porção", estimated="estimado", scaled="ajustado de {base}", base_estimate="{base} era uma estimativa",
               vegan="Vegano", no_amount="quantidade não informada", optional="opcional", implied="implícito no texto",
               replaces="substitui {name}", check="Conferir antes de cozinhar", step="passo", high="alta", medium="média"),
}
WARN = "⚠"
SHOWN = ("high", "medium")  # low issues are notes for the reviewer, not for the cook


def labels_for(language: str | None) -> dict[str, str]:
    code = (language or "").lower().replace("_", "-").split("-")[0]
    return LABELS.get(code, LABELS["en"])


def _escape(text: str) -> str:
    """One line of plain text that the Markdown subset shows as written."""
    return re.sub(r"([\\*_])", r"\\\1", " ".join(text.split()))


def _amount(ing: Ingredient, language: str | None, L: dict[str, str]) -> str:
    """"2 EL", "1/2 l", "1 a 2 colheres", a vague amount as written, or that none is given."""
    unit = ing.unit or ""
    text = ing.quantity_text
    if ing.quantity is not None:
        parsed = parse_quantity(text, language) if text else None
        if not (text and parsed and parsed.value is not None and abs(parsed.value - ing.quantity) <= 0.01 * max(1.0, abs(ing.quantity))):
            text = format_amount(ing.quantity, language)  # no text, or the amount was changed after it was read
    elif not text:
        return L["no_amount"]
    if unit and fold(text or "").endswith(fold(unit)):
        unit = ""  # the words already include the unit
    return f"{text} {unit}".strip()


def _notes(issues: Iterable[Issue], changes: Iterable[Change]) -> list[tuple[str, str, str]]:
    """(severity, what it concerns, text) for what is still open, most serious first."""
    found: list[tuple[str, str, str]] = []
    for i in issues:
        if i.severity in SHOWN and i.code != "servings_inferred":  # the servings line already says it
            found.append((i.severity, i.refers_to or "", i.message))
    for c in changes:
        severity = c.severity or "medium"  # a warning with no level (a pot that may be too small) matters
        if c.kind == "warning" and severity in SHOWN and c.target != "servings":
            found.append((severity, c.target, c.reason))
    unique = list(dict.fromkeys(found))
    return sorted(unique, key=lambda n: SHOWN.index(n[0]))


def _servings_line(recipe: Recipe, changes: list[Change], L: dict[str, str]) -> str:
    parts = []
    if any(c.rule == "make_vegan" for c in changes):
        parts.append(L["vegan"])
    scaled = next((c for c in changes if c.kind == "changed" and c.target == "servings" and c.field == "servings"), None)
    estimate = any(c.kind == "warning" and c.target == "servings" and c.field == "base" for c in changes)
    n = recipe.servings.value
    if n:
        text = L["serving"] if n == 1 else L["servings"].format(n=n)
        if scaled:
            text += ", " + L["scaled"].format(base=scaled.before)
            if estimate:
                text += f" ({L['base_estimate'].format(base=scaled.before)})"
        elif not recipe.servings.stated:
            text += f" ({L['estimated']})"
        parts.append(text)
    return " · ".join(parts)


def render_markdown(recipe: Recipe, *, issues: Iterable[Issue] = (), changes: Iterable[Change] = ()) -> str:
    """The recipe as Markdown for a cook. Nothing passed in is changed."""
    L = labels_for(recipe.language)
    changes = list(changes)
    notes = _notes(issues, changes)
    flagged = {target for _, target, _ in notes}
    names = {i.id: i.name for i in recipe.ingredients} | {t.id: t.name for t in recipe.tools}
    replaced = {c.target: c.before for c in changes
                if c.rule == "make_vegan" and c.kind == "changed" and c.field == "ingredient" and c.before}

    out = [f"# {_escape(recipe.title or L['untitled'])}", ""]
    if line := _servings_line(recipe, changes, L):
        out += [f"_{_escape(line)}_", ""]

    if recipe.ingredients:
        out += [f"## {L['ingredients']}", ""]
        for ing in recipe.ingredients:
            text = f"{_amount(ing, recipe.language, L)} {_escape(ing.name)}"
            if ing.prep_note:
                text += f", {_escape(ing.prep_note)}"
            marks = ([L["optional"]] if ing.optional else []) + \
                    ([L["replaces"].format(name=_escape(replaced[ing.id]))] if ing.id in replaced else [])
            out.append(f"- {text}" + (f" ({', '.join(marks)})" if marks else "") + (f" {WARN}" if ing.id in flagged else ""))
        out.append("")
    if recipe.tools:
        out += [f"## {L['tools']}", ""]
        for tool in recipe.tools:
            out.append(f"- {_escape(tool.name)}" + (f" ({L['implied']})" if tool.implied else "")
                       + (f" {WARN}" if tool.id in flagged else ""))
        out.append("")
    if recipe.steps:
        out += [f"## {L['steps']}", ""]
        for step in recipe.steps:
            out.append(f"{step.n}. {_escape(step.text)}" + (f" {WARN}" if f"step {step.n}" in flagged else ""))
        out.append("")
    if notes:
        out += [f"## {WARN} {L['check']}", ""]
        for severity, target, text in notes:
            what = names.get(target) or (f"{L['step']} {target[5:]}" if target.startswith("step ") else target)
            out.append(f"- **{L[severity]}**" + (f" · {_escape(what)}" if what else "") + f": {_escape(text)}")
        out.append("")
    return "\n".join(out)
