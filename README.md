# Cookbook challenge

A pipeline that turns a scanned or typed recipe into structured data you can scale and make vegan.
Built for the finlyt engineering case study. Stages: **ingest, extract, manipulate, output**.

## Setup

You need Python 3.10 or newer. From the project folder:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev,web]"
```

On macOS or Linux, activate with `source .venv/bin/activate` instead. Activate the environment again in every new terminal.

If PowerShell refuses to run the activation script, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

### API key (only needed for the extract stage)

The extract stage calls the Gemini API. Copy the example file and put your key in it:

```powershell
Copy-Item .env.example .env
```

Then edit `.env` and set `GEMINI_API_KEY=`. Optionally set `GEMINI_MODEL=` (primary reader), `GEMINI_SECOND_MODEL=` (second reader) and `GEMINI_FALLBACK_MODELS=` (comma-separated). `.env` is gitignored, so never commit it.

**Claude (optional).** A reader can also be a Claude model, which makes the two readings more independent than two Gemini models. Install the extra package and add a key (from the Anthropic Console, not the Claude app) to `.env`:

```powershell
pip install -e ".[claude]"
```

```
ANTHROPIC_API_KEY=...
```

Claude is paid and has no free tier that I know of. The default is `claude-haiku-5-5`, the cheapest current model; set `CLAUDE_MODEL=` or `CLAUDE_SECOND_MODEL=` to change it. Usage is billed to whatever account the key belongs to, so use a key you are allowed to spend on this.

### The recipe PDF

`Recipe.pdf` (the scanned Sauerbraten) is **not** in the repository, because it is a page from a printed book. Put your copy in the project folder next to this README. The name is already in `.gitignore`. Other recipes work the same way: any PDF you pass on the command line is read from where it is.

## Running each stage

### Ingest

Shows how each page of a PDF, an image or a plain text file would be read. The kind of file is detected from its first bytes, not from its name.

```powershell
python -m cookbook.ingest Recipe.pdf
```

Options:
- `--show-text` also prints the text found in each page's text layer.
- `--json` prints machine-readable output instead.

Example output for the scanned recipe:

```
page 1: VISION
  signals: chars=0 image=100% invisible_text=False ocr_producer=False ocr_font=False quality=n/a
  - no text layer (0 chars) and one image covers 100% of the page: a scan
```

The strategy per page is one of:

| Strategy | Meaning |
|---|---|
| `TEXT_DIGITAL` | real typed text, use it |
| `TEXT_OCR` | an OCR layer of acceptable quality over a scan, use it but treat it as machine-read |
| `VISION` | no usable text, send the page image to a vision model |
| `EMPTY` | nothing to read |

A typed page with an image that may hold text also lists an `extra task` line for each such image. Thresholds are constants at the top of [src/cookbook/ingest/pdf.py](src/cookbook/ingest/pdf.py).

### Extract

Reads every page and writes a transcript. Typed pages use their text layer and never call a model. `VISION` pages and images on typed pages are rendered (a JPEG at the photo's own resolution, at most 2400 px on the longest side) and sent to Gemini.

**Two readers.** By default two different models read the same image at the same time. The first model's reading is the transcript. Plain code then compares the two readings word by word:
- Every place where they differ is a **flag**: that word or number is not certain. A difference in a number or fraction is high severity, any other word is medium.
- Differences that are not content are ignored: line-end hyphens, spacing and dash characters, punctuation, the same fraction written two ways (`¾` and `3/4`), a drawing label listed in a different order, and a page number that only one reading has.
- **Reading order.** On a page with columns one model may read row by row and the other column by column: the same words in a different order. The comparison first cuts both readings into blocks of words that are intact in both (same words, same order) wherever they sit, and sees which blocks kept their order. A block that only moved is not a disagreement; it counts as agreeing and the page is reported as `reading order differs`. Words that match nowhere still count against the agreement and are flagged. A lone number is never forgiven as a move (a number only moves together with its unit and noun, so swapped quantities are still caught), nor a block of only tiny words, nor a long block of more than 12 words (the order of the steps of a method matters): that one is flagged as `order`.
- **What this cannot tell you** is which reading's order is right. The transcript, and so the structuring, follows the first reading. When a page reports a different reading order, compare `.txt` with `.second.txt` and check that no line mixes two columns.
- The **agreement** (word similarity of the two readings, 0 to 1) is reported for each page. Below 0.85 the page is marked **UNRELIABLE**, because on an illegible page each model invents different text.
- If the second reader fails (for example a 503), you still get the first reading, with a warning and no comparison.
- By default both readers are Gemini models, so a mistake they share is not flagged. A second reader from another provider (`--second-provider claude`) makes shared mistakes less likely. Agreement never proves a reading is right.

First, see which models your key can use. Model names change often, and some are closed to new users:

```powershell
python -m cookbook.extract --list-models
```

Then transcribe:

```powershell
python -m cookbook.extract Recipe.pdf --pages 1,2
```

Options:
- `--provider gemini|claude` sets the provider of the primary reader (default `gemini`) and `--second-provider gemini|claude` the provider of the second reader (default `gemini`). For example `--second-provider claude` reads every page with Gemini and with Claude Haiku.
- `--model NAME` sets the primary reader's model. Without it the code uses `GEMINI_MODEL` (or `CLAUDE_MODEL`) from `.env`, then `gemini-3.1-flash-lite` (or `claude-haiku-5-5`).
- `--second-model NAME` sets the second reader's model. Without it: `GEMINI_SECOND_MODEL` (or `CLAUDE_SECOND_MODEL`), then `gemini-3.5-flash-lite` (or `claude-haiku-5-5`). The two readers must not be the same provider and model.
- `--claude-fallback-models A,B` lists fallbacks for a Claude reader. Without it: `CLAUDE_FALLBACK_MODELS`, then none, because the next model up costs about 20 times more. Gemini fallbacks (`--fallback-models`) are not used for Claude.
- `--fallback-models A,B` lists the models to switch to when a reader's own model keeps failing. Without it: `GEMINI_FALLBACK_MODELS`, then `gemini-3.5-flash`.
- `--no-fallback` never switches to another model.
- `--single` reads with one model only, with no comparison and half the calls.
- `--pages 1,2` limits the run to those pages (1-based).
- `--out DIR` sets the output folder. The default is `data/experiments/`, which is gitignored.

The model names above worked for our key in November 2026. They may be retired, so check `--list-models`.

For each page it writes these files into the output folder, named `<pdf name>_<primary model>_p<N>`:
- `.txt`: the transcript (the first reading), one line per written line, each prefixed with an id like `p1:L3`.
- `.second.txt`: the second reading, in the same format.
- `.flags.json`: the agreement, whether the page is unreliable, and every flag with its line id, both readings, kind and severity.
- `.usage.json`: tokens, seconds and whether the answer came from the cache, for each reader.

And once per run, `<pdf name>_<primary model>.run.json`: what the run used, with the number of calls (and how many came from the cache), fallbacks and extra attempts, the tokens in, out and thinking, the time of the calls added up and the wall-clock time, per model and in total, plus the unreliable pages and the issue counts. The tokens and seconds count every call, cached ones included, so they are what the same run uses with an empty cache. The same totals are printed at the end of the run.

The command also prints a summary. Example for a readable page:

```
page 1 [vision, vision] -> data\experiments\Recipe_gemini-3.1-flash-lite_p1.txt
  page: agreement 0.98 (reads agree), 1 flag(s)
    [medium] p1:L4 word: 'Josefines' vs 'Josefinés'
```

Things to know:
- **Sending data:** this uploads images of your pages to Google. Free-tier prompts may be used for training in some regions, so do not send documents you must keep private.
- **Cache:** answers are cached in `.cache/llm/`, keyed on provider, model, prompt and image. Running the same page again costs nothing. Delete the folder to force new calls.
- **Empty answers:** a model that answers with no text (blocked or cut off) counts as a failed call: it is retried, falls back to another model, and is never cached or compared as if it were a reading.
- **Retries and fallback:** a temporary error (a 503 "high demand", a 429 rate limit, a timeout) is retried up to 3 times per model, waiting about 1, 2, then 4 seconds. If the model still fails, or is not available to your key (a 404), the next fallback model is tried. Errors that retrying cannot fix (a bad key, a bad request) stop at once. Each request times out after 90 seconds. The summary shows when a call needed retries or a fallback, with the errors in `usage.json`.
- **Fallback and the comparison:** a fallback answer is cached under the model that gave it. If both readers end up on the same model, the comparison is skipped with a warning, because two readings from one model are not independent.

**Structuring.** Add `--structure` to also turn the transcript into a structured recipe and check it:

```powershell
python -m cookbook.extract Recipe.pdf --pages 1,2 --structure
```

One more model call (the primary model) fills the schema in [src/cookbook/models.py](src/cookbook/models.py): title, language, servings, ingredients (name, amount, unit, preparation note, optional), tools (named or implied) and steps (with the ingredients, tools, durations and conditions each uses). Names and steps stay in the recipe's own language, and each ingredient and tool also gets a short English label for rules like "make it vegan".

The model is not trusted. Every item must carry a quote copied from the transcript and the ids of the lines it came from, and plain code ([validate.py](src/cookbook/extract/validate.py)) checks the result. It never fixes or drops anything: each problem becomes an issue with a severity.

| Issue | Meaning |
|---|---|
| `unsupported_quote` (high) | the quote is not in the transcript: possibly invented |
| `invented_quantity`, `quantity_mismatch`, `unit_mismatch` (high) | an amount with no words behind it, or that does not match its words or unit |
| `uncovered_number` (high) | the transcript has a number that nothing extracted from that line contains: a dropped quantity or duration (medium if the line is outside the extracted text, such as a header) |
| `unknown_reference`, `duplicate_id` (high) | a step uses an ingredient or tool that is not in the list |
| `uncertain_reading` (high or medium) | the value sits on a line where the two readings disagreed |
| `reading_order_differs` (medium) | the two readings put blocks of words in a different order (usually columns); one note per page |
| `unused_ingredient`, `duplicate_ingredient`, `unknown_unit`, `servings_unknown`, `servings_inferred` (medium) | for example servings the recipe never states are not guessed silently |
| `no_quantity`, `amount_not_verified`, `lines_corrected`, `wrong_lines`, `step_order` (low) | the source gives no amount (it is never guessed); the model cited the wrong line numbers for a quote that is in the transcript, and they were corrected; and similar notes |

It writes three more files per run, named `<pdf name>_<primary model>`:
- `.transcript.txt`: all pages joined, one written line per row with its id.
- `.recipe.json`: the structured recipe.
- `.issues.json`: every issue, most serious first.

The summary lists the high and medium issues. If the model's answer is not valid JSON of the right shape it is asked once more with the error; after that the command stops with an error.

### Manipulate (vegan, servings)

Takes the `.recipe.json` that extract wrote and changes it: `--servings N` for the number of servings, `--vegan` to make it vegan, or both. The rules always run in the same order (vegan, then servings), so the result does not depend on the order you ask for them.

```powershell
python -m cookbook.manipulate data/my_tests/pancakes_gemini-3.1-flash-lite.recipe.json --servings 6
python -m cookbook.manipulate data/my_tests/pancakes_gemini-3.1-flash-lite.recipe.json --vegan --servings 6
```

**Vegan.** A table in [vegan_table.py](src/cookbook/manipulate/vegan_table.py) knows what is certainly animal (meat, poultry, fish, seafood, dairy, eggs, honey, gelatine, lard; English, German and Portuguese words) and its stand-in (beef becomes seitan, bacon smoked tofu, butter vegan butter, and so on). One model call names each stand-in in the recipe's language, finds animal ingredients the table does not know, and rewrites every step that mentions something animal. Plain code checks the answer: the table has the last word on what is animal, a stand-in must not be animal itself, a rewritten step must keep every number and name nothing animal (also not inside a longer word), and a wrong answer is sent back once. A step that still fails keeps its original text with a **high** warning, so a wrong rewrite is never shown as a right one. Wine, beer, sugar and vinegar are not treated. Needs the Gemini key like extract. Details and limits: [manipulate.md](manipulate.md).

It writes `<name>.servings6.recipe.json` and `<name>.servings6.changes.json` next to the recipe, and prints what changed, what was left alone and why, and what to check by hand. If the recipe does not state its servings, the base is the model's estimate (flagged in the output); give the real number with `--base-servings 4`. Amounts scale by the kind of ingredient (seasonings grow more slowly than the rest, and vague amounts such as "a little" are kept), are rounded to values a kitchen uses, and the amounts written inside the steps are updated too.

### Output (the recipe for a cook)

```powershell
python -m cookbook.output data/my_tests/pancakes_gemini-3.1-flash-lite.vegan.servings6.recipe.json
```

Writes `<name>.md` next to the recipe: the title, how many it serves and how sure that is (stated, estimated, or scaled from a number that may itself have been a guess), the ingredients and tools, the numbered steps, and a "check before cooking" list. An ingredient that replaces another says so ("Seitan (replaces Braten)"), an amount the source never gives says "amount not given", and the items with an open warning carry a ⚠. It finds the change log (`<name>.changes.json`) and the original's `<name>.issues.json` next to the recipe by itself; `--issues` and `--changes` name them explicitly. Headings are in the recipe's language (German, English, Portuguese). See [output.md](output.md).

### The web page

```powershell
python -m cookbook.web
```

Opens http://127.0.0.1:8000 (this computer only). Drop a PDF, an image or a text file; the two models read it; then three steps: check the text (words the models read differently are underlined, click one to choose a reading or type your own), check the recipe (each value with its quote, and the doubts the checks found, coloured by severity), and change the servings (with the log of what was done and why). It needs the same `.env` key as the command line and the `web` extra from Setup. See [web.md](web.md) for how it works.

## Try it on your own recipes

Any PDF works: put it anywhere and pass its path (in quotes if the path has spaces). Keep the results inside `data/`, which is gitignored, so a recipe from a book never ends up in git:

```powershell
python -m cookbook.ingest "C:\path\to\my recipe.pdf"
python -m cookbook.extract "C:\path\to\my recipe.pdf" --structure --out data/my_tests
```

What to expect:
- **Supported:** PDFs that are scanned or photographed, typed, or a mix; images (PNG, JPEG, GIF, TIFF, WebP), which are read like a scanned page; and plain text files, in any language. A text file needs no model reading at all: it is used as it is (UTF-8, UTF-16 with a BOM, or Windows-1252) and goes straight to the structuring call. Any other kind of file is refused.
- **Line ids.** An id like `p1:L3` points at line 3 of page 1 of the *transcript*, which is saved as `.transcript.txt`. For a scan those are the written lines the model read (roughly the third visible line); for a typed PDF the lines of the text extraction; for a text file the file's own line numbers (blank lines count, and long lines are not wrapped). A PDF has no line numbers of its own, so the quote stored with every value is the stable anchor: search for it in the source.
- **Languages.** The model reads and structures a recipe in any language. The checks on amounts understand digits and fractions everywhere (`1/2`, `½`, `1,5`). Words are tables in [src/cookbook/units.py](src/cookbook/units.py): number words ("duas", "zwei"), "half" words ("meia", "halbes"), vague phrases ("a gosto", "nach Geschmack", "a little") and units ("xícara", "colher de sopa", "Esslöffel", "cup"). **German, English and Portuguese** are covered, ignoring accents, and the parser uses the language the model reports for the recipe (`pt-BR` counts as `pt`). In another language the amount checks still work for digits, and an amount written in words that is not in a table gets a low `amount_not_verified` note instead of a false alarm. To add a language, add a `Lexicon` to `LEXICONS` and its unit words to `_ALIASES`.
- **One recipe per PDF.** The schema holds a single recipe, so a PDF with a whole cookbook would be merged into one wrong recipe. Pick the pages of one recipe with `--pages`, for example `--pages 12,13`.
- **Calls and time.** A typed page needs no reading call, so only the structuring call is made (about 4 seconds). A scanned page is read by two models, then one structuring call is made (about 10 seconds for two pages). Results are cached, so running the same command again is free and gives the same answer. Delete `.cache/llm/` to start over.
- **Privacy.** Scanned pages (as images) and the transcript text are sent to Google. Do not use documents you must keep private.

How to check a result. Open the files written to the `--out` folder (named after the PDF and the primary model) in this order:
1. **The console summary.** For each scanned page it shows the agreement of the two readings and `UNRELIABLE` if they mostly disagree (a blurry or illegible page). Each flag is a word or number the two models read differently.
2. **`.transcript.txt`.** Read it against the page. Everything after this depends on it, and it is the one place the checks cannot verify, because they compare the recipe to the transcript, not to the page.
3. **`.recipe.json`.** Compare it with the page: amounts and units, which ingredients are optional, the order and separation of the steps, the tools, the servings. Each item has a `quote` and the `lines` it came from, so you can find its words in the transcript.
4. **`.issues.json`.** The problems the checks found, most serious first. `refers_to` is the ingredient ("i3"), tool, step or transcript line, and `lines` says where to look. See the table under Extract for what each code means.

What the checks cannot see, so look for it yourself: an ingredient the model never listed (only numbers are checked), a wrongly optional or non-optional ingredient, two steps merged into one, tools that are missing, and servings that are guessed.

Example for a typed one-page recipe (a made-up pancake recipe):

```
page 1 [text_digital, text_layer] -> data\my_tests\pancakes_gemini-3.1-flash-lite_p1.txt

structured recipe -> data\my_tests\pancakes_gemini-3.1-flash-lite.recipe.json
  structuring tokens in/out/thinking: 893/1677/None, 3.58s
  9 ingredients, 4 tools, 5 steps; servings: 4 (stated)
  issues: 0 high, 0 medium, 1 low -> data\my_tests\pancakes_gemini-3.1-flash-lite.issues.json
```

If something goes wrong:
- `error: No Gemini API key found`: create `.env` as described under Setup.
- A 404 for a model: its name was retired. Run `python -m cookbook.extract --list-models` and set `GEMINI_MODEL` in `.env`.
- A 503: the command retries and may switch to a fallback model by itself; the summary says when it did.
- `error: the model's answer is not a valid recipe`: the model returned something that was not valid twice in a row. Run the command again.

## Tests

```powershell
pytest
```

All tests run offline: the PDFs are generated on the fly and the model is replaced by a fake, so no API key is needed. Run one file with `pytest tests/test_ingest_pdf.py`.

## Comparing models

The scripts in [evals/](evals/README.md) build degraded copies of the scan and score each model's transcript against a reference. See that file for the steps.