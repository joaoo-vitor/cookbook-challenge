"""Amounts and units: parse "1/2", "1,5", "ein Pfund", "duas xícaras", "einige", and convert known units.

Quantities are read, never guessed: text that gives no number gives `value = None`.
Historic and regional units (Pfund, Loth, Schoppen) are converted with their usual
values and marked approximate where the size varied.

Languages. Digits and fractions are the same everywhere, so amounts written with digits are
understood in any language. Words are not, so they live in tables:
- number words, "half" words and vague phrases belong to one language each (`LEXICONS`: German,
  English, Portuguese), because a word can mean different things in different languages (English
  "a" is one, Portuguese "a" is an article, as in "a gosto"). The parser picks the table of the
  recipe's language; with an unknown language it uses German and English together.
- units sit in one shared table (`_ALIASES`): unit words rarely collide across languages.
To support another language, add a `Lexicon` to `LEXICONS` and its unit words to `_ALIASES`
(and to `_PHRASES` if a unit has several words, like "colher de sopa").
Matching ignores case and accents ("xícara" and "xicara" are the same word).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

_UNICODE_FRACTIONS = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3,
                      "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875}
_FR = "".join(_UNICODE_FRACTIONS)

# A number as written in a recipe: 4, 1/2, 1 1/2, 1,5, 1.5, 1.000, ½, 1½. Not a digit inside a word.
NUMBER = re.compile(
    rf"(?<![\w.,/])(?:\d+\s+\d+/\d+|\d+/\d+|\d+(?:[.,]\d+)*(?:\s?[{_FR}])?|[{_FR}])(?![\w/])")


def _fold(text: str) -> str:
    """Lower case without accents, so 'xícara' and 'Xicara' compare equal.

    Canonical decomposition (NFD) only: the compatibility form (NFKD) would also turn '½' into '1⁄2'.
    """
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if not unicodedata.combining(c))


# --- words that belong to one language -----------------------------------------------
@dataclass(frozen=True)
class Lexicon:
    numbers: dict[str, float]  # "zwei" -> 2
    halves: frozenset[str]  # "halbes", "meia": half of what follows
    vague: tuple[str, ...]  # phrases (regular expressions) that give no amount: "nach Geschmack"
    range_words: frozenset[str] = frozenset({"-", "–", "—"})  # between two numbers: "2 bis 3"
    join_words: frozenset[str] = frozenset()  # "1 und 1/2": the second number is added
    special: dict[str, float] = field(default_factory=dict)  # one-word numbers: "anderthalb" -> 1.5
    compound_half: str | None = None  # a regex whose group 1 is a number word: "zweieinhalb"
    and_half: str | None = None  # a regex for "... and a half": "uma xícara e meia"


LEXICONS: dict[str, Lexicon] = {
    "de": Lexicon(
        numbers={"ein": 1, "eine": 1, "einen": 1, "einem": 1, "einer": 1, "eins": 1, "zwei": 2, "drei": 3,
                 "vier": 4, "fünf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11,
                 "zwölf": 12},
        halves=frozenset({"halb", "halbe", "halbes", "halben", "halber"}),
        vague=(r"nach (?:geschmack|belieben|bedarf)", r"einige", r"etwas", r"ein wenig", r"wenig", r"soviel",
               r"so viel", r"reichlich", r"genug"),
        range_words=frozenset({"-", "–", "—", "bis", "oder"}),
        join_words=frozenset({"und"}),
        special={"anderthalb": 1.5},
        compound_half=r"(\w+?)einhalb",
    ),
    "en": Lexicon(
        numbers={"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                 "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12},
        halves=frozenset({"half"}),
        vague=(r"to taste", r"as needed", r"as much as", r"some", r"a little", r"a few", r"several", r"a bit"),
        range_words=frozenset({"-", "–", "—", "to", "or"}),
        join_words=frozenset({"and"}),
        and_half=r"\band a half\b",
    ),
    "pt": Lexicon(
        numbers={"um": 1, "uma": 1, "dois": 2, "duas": 2, "três": 3, "quatro": 4, "cinco": 5, "seis": 6,
                 "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12},
        halves=frozenset({"meia", "meio", "metade"}),
        vague=(r"a gosto", r"quanto baste", r"q\.?\s?b\.?", r"um pouco", r"um pouquinho", r"pouco",
               r"algumas", r"alguns", r"bastante", r"o suficiente", r"para (?:untar|polvilhar|decorar|empanar|fritar)"),
        range_words=frozenset({"-", "–", "—", "a", "ou"}),
        join_words=frozenset({"e"}),
        and_half=r"\be\s+(?:meia|meio)\b",
    ),
}


def _language_code(language: str | None) -> str | None:
    """'pt-BR', 'PT' and 'pt_br' all give 'pt'; an unsupported or missing language gives None."""
    code = (language or "").lower().replace("_", "-").split("-")[0]
    return code if code in LEXICONS else None


@dataclass(frozen=True)
class _Rules:
    """The lexicons of one language (or German and English when it is unknown), folded and compiled."""

    numbers: dict[str, float]
    halves: frozenset[str]
    special: dict[str, float]
    range_words: frozenset[str]
    join_words: frozenset[str]
    compound_half: tuple[re.Pattern, ...]
    and_half: tuple[re.Pattern, ...]
    vague: re.Pattern | None


@lru_cache(maxsize=None)
def _rules(code: str | None) -> _Rules:
    lexicons = [LEXICONS[code]] if code else [LEXICONS["de"], LEXICONS["en"]]
    fold_all = lambda items: {_fold(k): v for lex_items in items for k, v in lex_items.items()}  # noqa: E731
    vague = [p for lex in lexicons for p in lex.vague]
    return _Rules(
        numbers=fold_all(lex.numbers for lex in lexicons),
        halves=frozenset(_fold(w) for lex in lexicons for w in lex.halves),
        special=fold_all(lex.special for lex in lexicons),
        range_words=frozenset(w for lex in lexicons for w in lex.range_words),
        join_words=frozenset(w for lex in lexicons for w in lex.join_words),
        compound_half=tuple(re.compile(_fold(lex.compound_half)) for lex in lexicons if lex.compound_half),
        and_half=tuple(re.compile(_fold(lex.and_half)) for lex in lexicons if lex.and_half),
        vague=re.compile(r"\b(?:" + "|".join(_fold(p) for p in vague) + r")\b") if vague else None,
    )


# --- units: one table for every language ---------------------------------------------
# word -> canonical unit (German, English, Portuguese)
_RAW_ALIASES = {
    # weight
    "g": "g", "gr": "g", "gramm": "g", "gram": "g", "grams": "g", "grama": "g",
    "kg": "kg", "kilo": "kg", "kilogramm": "kg", "quilo": "kg", "quilograma": "kg", "mg": "mg",
    "pfund": "pfund", "pfd": "pfund", "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb",
    "unze": "unze", "oz": "unze", "ounce": "unze", "loth": "loth", "lot": "loth",
    # volume
    "ml": "ml", "mililitro": "ml", "cl": "cl", "dl": "dl", "l": "l", "liter": "l", "litre": "l", "litro": "l",
    "schoppen": "schoppen", "maß": "mass", "mass": "mass",
    # spoons and cups
    "el": "el", "esslöffel": "el", "eßlöffel": "el", "tbsp": "el", "tablespoon": "el",
    "tl": "tl", "teelöffel": "tl", "tsp": "tl", "teaspoon": "tl", "löffel": "loeffel", "colher": "loeffel",
    "tasse": "tasse", "cup": "cup", "xícara": "xicara", "xicara": "xicara", "copo": "copo",
    # things counted
    "prise": "prise", "pinch": "prise", "pitada": "prise",
    "stück": "stueck", "stk": "stueck", "piece": "stueck", "unidade": "stueck", "pedaço": "stueck",
    "bund": "bund", "bunch": "bund", "maço": "bund",
    "zehe": "zehe", "clove": "zehe", "dente": "zehe",
    "scheibe": "scheibe", "slice": "scheibe", "fatia": "scheibe",
    "blatt": "blatt", "leaf": "blatt", "folha": "blatt",
    "dose": "dose", "can": "dose", "tin": "dose", "lata": "dose",
    "glas": "glas", "jar": "glas", "vidro": "glas",
    "handvoll": "handvoll", "kopf": "kopf", "cabeça": "kopf",
    "packung": "packung", "paket": "packung", "package": "packung", "packet": "packung", "pack": "packung",
    "pacote": "packung", "caixa": "packung", "caixinha": "packung", "sachê": "packung",
    "flasche": "flasche", "bottle": "flasche", "garrafa": "flasche",
}
_ALIASES = {_fold(k): v for k, v in _RAW_ALIASES.items()}

# units of several words, tried first: "colher de sopa", "colheres (chá)", "xícara de café"
_PHRASES: list[tuple[re.Pattern, str]] = [
    (re.compile(rf"colher(?:es)?\s*(?:de\s*|\(\s*){_fold(word)}\)?"), unit)
    for word, unit in (("sopa", "el"), ("chá", "tl"), ("café", "colher_cafe"), ("sobremesa", "colher_sobremesa"))
] + [
    (re.compile(rf"xicara(?:s)?\s*(?:de\s*|\(\s*){_fold(word)}\)?"), unit)
    for word, unit in (("chá", "xicara"), ("café", "xicara_cafe"))
]

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
    "xicara": ("ml", 240, True), "xicara_cafe": ("ml", 50, True), "copo": ("ml", 200, True),
    "colher_cafe": ("ml", 2.5, True), "colher_sobremesa": ("ml", 10, True),
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
    vague: str | None  # "einige", "a gosto", ... when there is no number


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
    """The canonical unit for a written unit ("Liter" and "l" give "l"), or None if unknown.

    Words of any supported language, with or without accents, and units of several words
    ("colher de sopa") are understood.
    """
    folded = _fold(word).strip()
    for pattern, unit in _PHRASES:
        if pattern.fullmatch(folded):
            return unit
    w = folded.strip(".,;:()")
    for candidate in (w, w.rstrip("s"), w[:-1] if w.endswith(("n", "e")) else w,
                      w[:-2] if w.endswith(("en", "es")) else w):
        if candidate in _ALIASES:
            return _ALIASES[candidate]
    if m := _SIZED_CONTAINER.fullmatch(word):  # "9-ounce can": counted by its container; the size is kept in the text
        return normalize_unit(m.group(1))
    return None


def _unit_after(text: str) -> str | None:
    text = text.strip()
    for pattern, unit in _PHRASES:  # a unit of several words right after the number
        if pattern.match(text):
            return unit
    for word in text.split()[:3]:
        if unit := normalize_unit(word):
            return unit
    return None


def parse_quantity(text: str, language: str | None = None) -> Quantity:
    """Read an amount such as "1/2 l", "ein halbes Pfund", "2-3 EL", "uma xícara e meia" or "einige".

    `language` is the recipe's language ("de", "en", "pt", also "pt-BR"). Digits work in any
    language; number words, "half" words and vague phrases need the language's table. With an
    unknown language, German and English are tried together.
    """
    rules = _rules(_language_code(language))
    t = " ".join(_fold(text).split())
    numbers = list(NUMBER.finditer(t))
    value = high = None
    after = t
    # A vague phrase often starts with a number word: "um pouco", "ein wenig", "a little". It is
    # looked for first, and its own words are not read as a number.
    vague_match = rules.vague.search(t) if rules.vague else None
    if numbers:
        value = _to_float(numbers[0].group())
        after = t[numbers[0].end():]
        if len(numbers) > 1:
            between = t[numbers[0].end():numbers[1].start()].strip()
            second = _to_float(numbers[1].group())
            if between in rules.range_words:
                high = second
                after = t[numbers[1].end():]
            elif between in rules.join_words and second is not None and second < 1:  # "1 e 1/2" is 1.5
                value = (value or 0) + second
                after = t[numbers[1].end():]
    else:
        scan = t if not vague_match else t[:vague_match.start()] + " " + t[vague_match.end():]
        words = scan.split()
        for i, word in enumerate(words):
            compound = next((m for p in rules.compound_half if (m := p.fullmatch(word))), None)
            if compound and compound[1] in rules.numbers:  # zweieinhalb
                value, after = rules.numbers[compound[1]] + 0.5, " ".join(words[i + 1:])
                break
            if word in rules.special:  # anderthalb
                value, after = rules.special[word], " ".join(words[i + 1:])
                break
            if word in rules.numbers:
                value, after = rules.numbers[word], " ".join(words[i + 1:])
                break
    and_half = value is not None and any(p.search(t) for p in rules.and_half)
    if and_half:  # "uma xícara e meia", "one and a half cups"
        value += 0.5
    elif any(w in rules.halves for w in t.split()) and value in (None, 1):  # "ein halbes Pfund", "meia xícara"
        value = 0.5
        after = " ".join(w for w in after.split() if w not in rules.halves and w not in {"a", "ein", "um", "uma"})
    vague = vague_match.group().lower() if value is None and vague_match else None
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
