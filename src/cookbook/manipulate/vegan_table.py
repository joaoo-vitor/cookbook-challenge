"""What is animal, and what to use instead: the knowledge behind the vegan rule, in plain code.

The table is keyed by English words (every ingredient has a `name_en`) with the German and
Portuguese words next to them, so one table reads recipes in all three languages. It decides
what is *certainly* animal; the model only handles what the table does not know, and its answers
are checked against this table (see `vegan.py`).

Matching works on text folded to lower case without accents ("Käse" -> "kase"), at the start of a
word, because German writes compounds ("Rinderbraten", "Butterschmalz") and the animal word comes
first. Short words ("Ei", "mel") must match the whole word. Words that look animal but are not
("peanut butter", "Kokosmilch", "eggplant") are removed before matching. Wine, beer, sugar and
vinegar are not treated: only clearly animal ingredients are.

A new word: add it to the entry it belongs to (a fragment of a regular expression, folded form),
and add a test in tests/test_vegan.py.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from cookbook.units import fold


@dataclass(frozen=True)
class Entry:
    category: str  # "meat", "poultry", "fish", "seafood", "dairy", "egg", "honey", "other"
    stand_in: str  # the plant-based stand-in, in English (the model gives its name in the recipe's language)
    pattern: re.Pattern


def _en(*words: str) -> str:
    """English words: whole words, with or without a plural ending."""
    return "(?:" + "|".join(words) + r")(?:s|es)?\b"


def _entry(category: str, stand_in: str, en: tuple[str, ...], local: tuple[str, ...] = ()) -> Entry:
    return Entry(category, stand_in, re.compile(r"\b(?:" + "|".join([_en(*en), *local]) + ")"))


# The first entry that matches wins, so the more specific ones come first ("ground beef" before "beef").
TABLE: tuple[Entry, ...] = (
    _entry("other", "vegan Worcestershire sauce", ("worcestershire",), ("worcester\\w*", "molho ingles")),
    _entry("dairy", "oat milk with a little lemon juice", ("buttermilk",), ("buttermilch\\w*", "leitelho")),
    _entry("meat", "soy mince", ("mince", "ground beef", "ground meat", "minced meat", "ground pork"),
           ("hackfleisch\\w*", "gehacktes", "carne moida", "carne picada")),
    _entry("meat", "vegetable stock", ("broth", "stock", "bouillon", "consomme"), ("fleischbruhe\\w*", "caldo de carne", "caldo de galinha")),
    _entry("meat", "smoked tofu", ("bacon", "ham", "pancetta", "prosciutto", "speck", "lardons"),
           ("speck\\w*", "schinken\\w*", "bacon\\b", "presunto\\w*", "toucinho\\w*")),
    _entry("meat", "vegan sausage", ("sausage", "salami", "chorizo", "frankfurter", "hot dog"),
           ("wurst\\w*", "salsicha\\w*", "linguica\\w*", "chourico\\w*", "mortadela\\w*", "calabresa\\w*")),
    _entry("poultry", "soy chunks", ("chicken", "turkey", "duck", "goose", "poultry", "hen"),
           ("huhn\\w*", "hahnchen\\w*", "hendl\\w*", "pute\\b", "puten\\w*", "truthahn\\w*", "ente\\b", "enten\\b",
            "gans\\b", "ganse\\b", "geflugel\\w*", "frango\\w*", "galinha\\w*", "pato\\b", "patos\\b")),
    _entry("seafood", "king oyster mushrooms", ("shrimp", "prawn", "crab", "lobster", "mussel", "clam", "oyster", "squid", "scallop"),
           ("garnele\\w*", "krabbe\\w*", "hummer\\b", "muschel\\w*", "camarao", "camaroes", "lagosta\\w*", "lula\\w*", "polvo\\b")),
    _entry("fish", "marinated tofu", ("fish", "salmon", "tuna", "cod", "trout", "herring", "anchovy", "anchovies", "sardine", "mackerel", "carp"),
           ("fisch\\w*", "lachs\\w*", "thunfisch\\w*", "kabeljau\\w*", "forelle\\w*", "hering\\w*", "sardell\\w*", "anchovis\\w*",
            "karpfen\\w*", "peixe\\w*", "salmao\\b", "atum\\b", "bacalhau\\w*", "sardinha\\w*", "anchova\\w*")),
    _entry("meat", "seitan", ("beef", "veal", "steak", "roast", "pork", "lamb", "mutton", "venison", "game", "oxtail", "brisket", "rib", "meat", "meatball"),
           ("rind(?:er\\w*|s\\w*|fleisch\\w*)?\\b", "kalb\\w*", "schwein\\w*", "lamm\\w*", "hirsch\\w*", "wildfleisch\\w*",
            "fleisch\\w*", "carne\\w*", "boi\\b", "bovin\\w*", "vitela\\w*", "porco\\w*", "suin\\w*", "cordeiro\\w*", "costela\\w*", "picanha\\w*")),
    _entry("dairy", "vegan butter", ("butter", "ghee"), ("butter\\w*", "manteiga\\w*", "ghee\\b")),
    _entry("dairy", "plant cream", ("sour cream", "cream", "creme fraiche", "whipping cream", "heavy cream", "double cream", "custard"),
           ("sahne\\w*", "schmand\\w*", "creme fraiche", "rahm\\b", "obers\\b", "creme de leite", "nata\\b", "natas\\b")),
    _entry("dairy", "plant yogurt", ("yogurt", "yoghurt", "quark", "kefir"),
           ("joghurt\\w*", "jogurt\\w*", "quark\\b", "iogurte\\w*")),
    _entry("dairy", "vegan cheese", ("cheese", "parmesan", "mozzarella", "cheddar", "feta", "gouda", "ricotta", "mascarpone"),
           ("kase\\w*", "parmesan\\w*", "queijo\\w*", "requeijao\\w*")),
    _entry("dairy", "oat milk", ("milk",), ("milch\\w*", "leite\\b", "leites\\b")),
    _entry("egg", "egg replacer", ("egg", "egg yolk", "egg white", "yolk"),
           ("ei\\b", "eier\\b", "eiern\\b", "eigelb\\w*", "eiweiss\\w*", "eiweiß\\w*", "eidotter\\w*", "ovo\\b", "ovos\\b")),
    _entry("honey", "maple syrup", ("honey",), ("honig\\w*", "mel\\b")),
    _entry("other", "agar-agar", ("gelatin", "gelatine"), ("gelatine\\w*", "gelatina\\w*")),
    _entry("other", "vegetable oil", ("lard", "tallow", "suet", "dripping"), ("schmalz\\w*", "talg\\w*", "banha\\w*", "gordura animal")),
)

# Words that name something made from an animal but are not an ingredient of their own: they show up
# in the steps ("füllt mit Essigwasser", "schwitzt man den Bratenfond") and must go with the meat.
DERIVED = re.compile(r"\b(?:bratenfond\w*|bratensatz\w*|bratensaft\w*|fond\b|pan juices|cooking juices|pan drippings|drippings|suco do assado)")

# Look animal, are not.
LOOKALIKES = re.compile(
    r"\b(?:peanut|nut|almond|cocoa|cacao|shea|coconut|cashew|soy|soya|oat|rice|hazelnut|plant|vegetable|veggie)\s+"
    r"(?:butter|milk|cream|yogurt|yoghurt|cheese|stock|broth|bouillon)"
    r"|butternut|butternuss\w*|eggplant|egg plant|cream of tartar|\bvegan\w*\s+\w+|plant[- ]based\s+\w+|aubergine|pflanzen\w*|pflanzlich\w*\s+\w+|(?:lemon|orange|lime|citrus|grapefruit)\s+rind"
    r"|erdnussbutter|kokosmilch|kokossahne|mandelmilch|hafermilch|sojamilch|reismilch|nussmilch|gemusebruhe|gemusefond"
    r"|manteiga de (?:amendoim|cacau)|leite de (?:coco|soja|aveia|amendoa\w*)|leite vegetal|creme de coco"
    r"|caldo de legumes|pasta de amendoim|margarine"
    # the stand-ins themselves
    r"|egg replacer|egg substitute|eiersatz|oyster mushroom|king oyster|kraut\w*seitling"
    r"|(?:soy|soya|plant|vegetable|lentil|wheat)\s+(?:mince|sausage|bacon|ham|meat|chunks|burger|roast)")


def _clean(text: str, allowed: Iterable[str] = ()) -> str:
    """Folded text without the phrases that are allowed (a stand-in's own name) or only look animal."""
    t = fold(text)
    for phrase in allowed:
        if phrase.strip():
            t = t.replace(fold(phrase), " ")
    return LOOKALIKES.sub(" ", t)


def classify(*names: str) -> Entry | None:
    """The table entry for an ingredient (given its English and its own name), or None if the table does not know it."""
    cleaned = [_clean(n) for n in names if n]
    for entry in TABLE:
        if any(entry.pattern.search(c) for c in cleaned):
            return entry
    return None


def find_animal(text: str, allowed: Iterable[str] = ()) -> list[str]:
    """Animal words in a text (folded), in order, without those inside `allowed` phrases or vegan look-alikes."""
    t = _clean(text, allowed)
    hits = [(m.start(), m.group().strip()) for e in TABLE for m in e.pattern.finditer(t)]
    hits += [(m.start(), m.group().strip()) for m in DERIVED.finditer(t)]
    return [w for _, w in sorted(set(hits))]


def derived_in(text: str) -> list[str]:
    """Animal-derived words that are not ingredients ("Bratenfond"), in a step's text."""
    return [m.group().strip() for m in DERIVED.finditer(_clean(text))]
