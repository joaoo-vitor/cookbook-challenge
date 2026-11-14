"""Amounts and units: parse "1/2", "1,5", "ein Pfund", "einige", and convert known units.

Quantities are read, never guessed: text that gives no number gives `value = None`.
Historic and regional units (Pfund, Loth, Schoppen) are converted with their usual
values and marked approximate where the size varied.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_UNICODE_FRACTIONS = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3,
                      "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875}
_FR = "".join(_UNICODE_FRACTIONS)

# A number as written in a recipe: 4, 1/2, 1 1/2, 1,5, 1.5, 1.000, ½, 1½. Not a digit inside a word.
NUMBER = re.compile(
    rf"(?<![\w.,/])(?:\d+\s+\d+/\d+|\d+/\d+|\d+(?:[.,]\d+)*(?:\s?[{_FR}])?|[{_FR}])(?![\w/])")

_WORDS = {
    "ein": 1, "eine": 1, "einen": 1, "einem": 1, "einer": 1, "eins": 1, "zwei": 2, "drei": 3, "vier": 4,
    "fünf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12,
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_HALF = {"halb", "halbe", "halbes", "halben", "halber", "half"}
_RANGE_WORDS = {"-", "–", "—", "bis", "to", "oder", "or"}

_VAGUE = re.compile(
    r"nach (?:geschmack|belieben|bedarf)|einige|etwas|ein wenig|wenig|soviel|so viel|reichlich|genug|"
    r"to taste|as needed|as much as|some|a little|a few|several|a bit", re.I)

# word -> canonical unit
_ALIASES = {
    "g": "g", "gr": "g", "gramm": "g", "gram": "g", "grams": "g", "kg": "kg", "kilo": "kg",
    "kilogramm": "kg", "mg": "mg",
    "pfund": "pfund", "pfd": "pfund", "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb",
    "unze": "unze", "oz": "unze", "ounce": "unze", "loth": "loth", "lot": "loth",
    "ml": "ml", "cl": "cl", "dl": "dl", "l": "l", "liter": "l", "litre": "l",
    "schoppen": "schoppen", "maß": "mass", "mass": "mass",
    "el": "el", "esslöffel": "el", "eßlöffel": "el", "tbsp": "el", "tablespoon": "el",
    "tl": "tl", "teelöffel": "tl", "tsp": "tl", "teaspoon": "tl", "löffel": "loeffel",
    "tasse": "tasse", "cup": "cup", "prise": "prise", "pinch": "prise",
    "stück": "stueck", "stk": "stueck", "piece": "stueck", "bund": "bund", "bunch": "bund",
    "zehe": "zehe", "clove": "zehe", "scheibe": "scheibe", "slice": "scheibe", "blatt": "blatt",
    "leaf": "blatt", "dose": "dose", "can": "dose", "tin": "dose", "glas": "glas", "jar": "glas",
    "handvoll": "handvoll", "kopf": "kopf", "packung": "packung", "paket": "packung", "package": "packung",
    "packet": "packung", "pack": "packung", "flasche": "flasche", "bottle": "flasche",
}

# canonical unit -> (base unit, factor, approximate?)
_TO_BASE = {
    "g": ("g", 1, False), "kg": ("g", 1000, False), "mg": ("g", 0.001, False),
    "pfund": ("g", 500, False),  # the German Pfund, 500 g since 1858
    "loth": ("g", 500 / 30, True),  # 1/30 Pfund; varied by region before 1858
    "unze": ("g", 28.35, False), "lb": ("g", 453.6, False),
    "ml": ("ml", 1, False), "cl": ("ml", 10, False), "dl": ("ml", 100, False), "l": ("ml", 1000, False),
    "schoppen": ("ml", 250, True),  # a quarter to half a litre, depending on the region
    "mass": ("ml", 1000, True),  # the Bavarian Maß, about a litre
    "el": ("ml", 15, True), "tl": ("ml", 5, True), "tasse": ("ml", 250, True), "cup": ("ml", 240, True),
    "loeffel": ("ml", 10, True),
}
_COUNT_UNITS = {"prise", "stueck", "bund", "zehe", "scheibe", "blatt", "dose", "glas", "handvoll", "kopf",
                "packung", "flasche"}

# A container with its size in front: "9-ounce can", "16 oz. jar", "500 g Packung".
_SIZED_CONTAINER = re.compile(r"\s*[\d.,/½¼¾⅓⅔⅛]+[\s-]*[^\W\d_]+\.?[\s-]+([^\W\d_]+)\s*", re.I)


_FRACTION_TEXT = {"½": "1/2", "¼": "1/4", "¾": "3/4", "⅓": "1/3", "⅔": "2/3",
                  "⅛": "1/8", "⅜": "3/8", "⅝": "5/8", "⅞": "7/8"}


def normalize_fractions(text: str) -> str:
    """Write unicode fractions as digits, so two readings of the same number match.

    "¾" becomes "3/4" and "1½" becomes "1 1/2".
    """
    text = re.sub(rf"(\d)\s?([{_FR}])", r"\1 \2", text)
    return re.sub(rf"[{_FR}]", lambda m: _FRACTION_TEXT[m.group()], text)


@dataclass(frozen=True)
class Quantity:
    value: float | None  # the (lower) number, None if the text gives none
    high: float | None  # the upper end of a range like "2-3"
    unit: str | None  # canonical unit found after the number, if any
    vague: str | None  # "einige", "nach Geschmack", ... when there is no number


@dataclass(frozen=True)
class BaseAmount:
    amount: float
    base: str  # "g", "ml" or "count"
    approx: bool  # the unit's size varied or is a rough measure


def _to_float(token: str) -> float | None:
    t = token.strip()
    try:
        if t in _UNICODE_FRACTIONS:
            return _UNICODE_FRACTIONS[t]
        if m := re.fullmatch(rf"(\d+)\s*([{_FR}])", t):
            return int(m[1]) + _UNICODE_FRACTIONS[m[2]]
        if m := re.fullmatch(r"(\d+)\s+(\d+)/(\d+)", t):
            return int(m[1]) + int(m[2]) / int(m[3])
        if m := re.fullmatch(r"(\d+)/(\d+)", t):
            return int(m[1]) / int(m[2])
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", t):  # 1.000 or 1.000,5 (German thousands)
            return float(t.replace(".", "").replace(",", "."))
        return float(t.replace(",", "."))
    except (ValueError, ZeroDivisionError):
        return None


def numbers_in(text: str) -> list[float]:
    """Every number written in the text, as values ("1/2", "½" and "0,5" are all 0.5)."""
    values = (_to_float(m.group()) for m in NUMBER.finditer(text))
    return [v for v in values if v is not None]


def normalize_unit(word: str) -> str | None:
    """The canonical unit for a written unit ("Liter" and "l" give "l"), or None if unknown."""
    w = word.lower().strip(".,;:()")
    for candidate in (w, w.rstrip("s"), w[:-1] if w.endswith(("n", "e")) else w, w[:-2] if w.endswith("en") else w):
        if candidate in _ALIASES:
            return _ALIASES[candidate]
    if m := _SIZED_CONTAINER.fullmatch(word):  # "9-ounce can": counted by its container; the size is kept in the text
        return normalize_unit(m.group(1))
    return None


def _unit_after(text: str) -> str | None:
    for word in text.split()[:3]:
        if unit := normalize_unit(word):
            return unit
    return None


def parse_quantity(text: str) -> Quantity:
    """Read an amount such as "1/2 l", "ein halbes Pfund", "2-3 EL" or "einige"."""
    t = " ".join(text.lower().split())
    numbers = list(NUMBER.finditer(t))
    value = high = None
    after = t
    if numbers:
        value = _to_float(numbers[0].group())
        after = t[numbers[0].end():]
        if len(numbers) > 1:
            between = t[numbers[0].end():numbers[1].start()].strip()
            if between in _RANGE_WORDS:
                high = _to_float(numbers[1].group())
                after = t[numbers[1].end():]
    else:
        words = t.split()
        for i, word in enumerate(words):
            if m := re.fullmatch(r"(\w+?)einhalb", word):  # zweieinhalb
                base = _WORDS.get(m[1])
                if base is not None:
                    value, after = base + 0.5, " ".join(words[i + 1:])
                    break
            if word == "anderthalb":
                value, after = 1.5, " ".join(words[i + 1:])
                break
            if word in _WORDS:
                value, after = _WORDS[word], " ".join(words[i + 1:])
                break
    if any(w in _HALF for w in t.split()) and value in (None, 1):  # "ein halbes Pfund", "half a cup"
        value = 0.5
        after = " ".join(w for w in after.split() if w not in _HALF and w not in {"a", "ein"})
    vague = None
    if value is None and (m := _VAGUE.search(t)):
        vague = m.group().lower()
    return Quantity(value, high, _unit_after(after), vague)


def to_base(value: float, unit: str) -> BaseAmount | None:
    """Convert an amount to grams or millilitres (or a count), if the unit is known."""
    canonical = normalize_unit(unit) or unit
    if canonical in _TO_BASE:
        base, factor, approx = _TO_BASE[canonical]
        return BaseAmount(value * factor, base, approx)
    if canonical in _COUNT_UNITS:
        return BaseAmount(value, "count", False)
    return None
