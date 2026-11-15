# Manipulate: change servings, make vegan

The stage takes the structured recipe from extract and changes it. Two rules exist, they can be combined, every value they change is logged with the reason, and the same input gives the same result. Entry points: `python -m cookbook.manipulate`, `manipulate()` in [engine.py](src/cookbook/manipulate/engine.py), and the web page's third step.

## One order, always

`RULE_ORDER = (make_vegan, scale_servings)`. The engine runs the requested rules in that order whatever the order of the request, so "vegan then 6 servings" and "6 servings then vegan" are the same call. What makes this sound is that scaling classes are decided from the *original* recipe, before any rule runs (`scaling_classes`), and a vegan stand-in keeps the id of the ingredient it replaces: a seasoning stays a seasoning, a counted item stays counted.

## The change log

Every rule adds `Change` entries ([changes.py](src/cookbook/manipulate/changes.py)): `changed` (before, after, reason), `unchanged` (looked at and left, with the reason) and `warning` (something to check by hand, with a severity of high, medium or low where it matters). The log is the explanation; the page and the CLI both print it, warnings first.

## Servings ([scaling.py](src/cookbook/manipulate/scaling.py))

Plain code, no model. Most ingredients scale in proportion; seasoning scales by factor^0.8 (doubling needs about 1.7 times the salt) with a "taste and adjust" note; whole items (eggs, onions) are rounded to whole numbers with a warning when the exact figure was far from one; a vague amount ("einige", "to taste") has nothing to scale and is left as written; times are left alone with a reminder; a pot that may no longer fit is a warning. Amounts are rounded to what a kitchen can measure and written as halves, thirds and quarters (decimal comma in German and Portuguese), and the amounts written inside the step text are updated by matching each number to the ingredient named right after it. When the recipe does not state its servings, the model's estimate is used and flagged as an estimate; the user can give the real number.

## Vegan ([vegan.py](src/cookbook/manipulate/vegan.py), [vegan_table.py](src/cookbook/manipulate/vegan_table.py))

Three layers, so nothing rests on the model alone.

1. **The table** keys animal ingredients by English words (each ingredient has a `name_en`) with the German and Portuguese words beside them, matches at the start of a word on text folded to lower case without accents (German compounds put the animal word first), and removes look-alikes first ("peanut butter", "Kokosmilch", "eggplant", "butternut", "cream of tartar", the stand-ins' own names). Entries are ordered, so the specific wins ("ground beef" before "beef", "chicken stock" before "chicken"). It also lists animal-derived words that are not ingredients (Bratenfond, Bratensatz, pan juices).
2. **One model call** (cached, so the same input gives the same answer) receives the ingredients with the table's verdict and the steps with the animal words found in them. It returns the ingredients that contain animal products with a stand-in named in the recipe's language (for those the table knows, the stand-in is the table's), and the rewritten text of every step that mentions something animal.
3. **Checks in code** decide what is accepted:
   - an ingredient the table flags is swapped whatever the model said; one only the model flags is swapped with a medium warning ("not in the table");
   - a stand-in must not be animal itself;
   - a rewritten step must keep exactly the same numbers (amounts, times, temperatures), must not be far longer, and must name nothing animal: no table word, no derived word, and not the swapped ingredient's own name, also as the start of a longer word (`Braten` rejects `Bratensud`, while the verb `angebraten` is fine). Words inside the stand-in's own name are allowed ("Butter" in "vegane Butter");
   - a wrong answer is sent back once with the list of faults. A step that still fails keeps its original text and gets a **high** warning ("rewrite this step by hand"); an ingredient that could not be swapped gets one too. A wrong rewrite is never shown as a right one.

What it changes: an ingredient's `name` and `name_en`; its amount, unit, optional flag, quote and line ids stay. Optional animal ingredients are swapped and stay optional. A meat or fish stand-in always gets a medium warning, because texture, flavour and cooking time differ, and times are left as written.

On the real Sauerbraten: Speck becomes Räuchertofu, Butter vegane Butter, Braten Seitan; the five steps that named them are rewritten with their numbers unchanged; "Bratenfond" in step 7 becomes "Sud", reported as a derived word; Essig, Wasser, Rotwein and Zucker stay.

## Decisions and limits

- Wine, beer, sugar and vinegar are not treated (only clearly animal ingredients), by decision. A strict vegan should still check labels.
- The stand-in is the table's default (seitan for beef and roast, soy chunks for poultry, and so on); the page only shows the result, it does not offer a choice.
- Cooking times are not adapted to a stand-in; they are kept and the log says so.
- Derived words are only found if they are in the table's list; an unlisted one is caught only if the model rewrites its step.
- The ratio of a stand-in to the original (an egg replacer per egg) is 1:1; the amount is not converted.
- Tools implied by an animal ingredient (a knife for cutting meat) are not changed.
- The quality of the German (or Portuguese) rewrite is the model's; that is why it is verified, and why a failed one is shown as failed.

## Tests

`tests/test_scaling.py` covers the servings rule. `tests/test_vegan.py` covers the table (animal words in three languages, look-alikes that must not match, the ordering) and each check rejecting its own planted fault with a fake model (a changed number, a leftover animal word, a stand-in that is animal, a missing answer, a longer-word leftover), the retry, the high warning, the table overriding the model, and the engine combining vegan with scaling.
