# Output: the recipe a cook can use

The review steps (the page's steps 1 and 2, and the change log of step 3) are for the person checking the pipeline. The output is for the person standing at the stove. It is Markdown written by [render.py](src/cookbook/output/render.py): `python -m cookbook.output`, the page's **Preview recipe** and **Download .md**, and the files in `examples/` all use the same function, so they cannot disagree.

## What it contains

A title; a line with the servings; the ingredients; the tools; the numbered steps; and a list to check before cooking. It shows the recipe **as it is now**: edited by the reviewer, scaled, made vegan.

The assignment asks how a cook knows what was read and what was guessed, and what to show when the program is not sure. A downloaded file has lost the page that explained it, so four markers stay with the recipe and nothing else:

| Marker | Where | Meaning |
|---|---|---|
| "4 servings (estimated)" | servings line | the text never says how many it serves; the model estimated |
| "6 servings, scaled from 4 (4 was an estimate)" | servings line | the amounts rest on a number that was a guess |
| "Seitan (replaces Braten)" | ingredient | the vegan rule swapped it; the original is named |
| "amount not given" | ingredient | the source gives no amount; none is invented (a vague one such as "soviel" is shown as written) |
| ⚠ and "Check before cooking" | item and a list at the end | an open high or medium warning: a doubt from the two readings, a check that failed, a step the vegan rule could not rewrite, a pot that may be too small |

Low issues are notes for the reviewer and are not shown. A warning the reviewer checked off in the page is not sent, so it does not reach the cook. A warning without a level (the scaling notes) counts as medium. The servings guess is not repeated in the list, because the servings line already says it.

## Decisions

- **Headings in the recipe's language.** A table for German, English and Portuguese, English as the fallback. The recipe's own text is never translated. The warnings are written by the checks in English, so they stay in English.
- **The cook's recipe only.** No "what changed" section: the page's step 3 shows the change log, and the Markdown stays a recipe. The one trace of the changes is the "replaces" and the servings line.
- **A few kinds of Markdown.** Headings, lists, bold and italic, with `\` escapes for `*` and `_` in the recipe's text. The page shows it with a ~40-line renderer that builds text nodes, so a recipe line that looks like HTML is shown as text.
- **Amounts.** An ingredient shows the words as read ("1 a 2 colheres") while they still agree with the number; if the reviewer changed the number, the number wins. Fractions and the decimal comma follow the language (`format_amount`).
- **Deterministic.** No model is used here; the same inputs give the same text.

## Limits

- Only de, en and pt headings; another language gets English ones.
- The check list repeats warnings in the code's English, and a doubt about a transcript line ("p1:L6") names a line the cook never sees. The reviewer can check those off before it reaches the cook.
- No print layout, no shopping list, no unit conversion for the cook (metric to cups).

## Tests

`tests/test_output.py` covers the languages and the fallback, every servings marker, the decimal comma, missing and vague amounts, a kept range and an amount typed over it, the unit not written twice, a swapped ingredient, the check list (only high and medium, sorted, once each, none when nothing is open), escaping, the command line, and that only the simple Markdown subset is written. `tests/test_web.py` covers `/api/render`.
