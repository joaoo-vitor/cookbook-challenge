# Extract: from pages to a checked, structured recipe

Extract takes the pages that ingest decided about and produces a structured recipe in which every value can be traced to the source, and anything the models were unsure of or guessed is marked. It is where the language models work, and where most of the code exists to distrust them.

```
pages --> read (1 or 2 models) --> compare --> transcript --> structure (1 model) --> repair --> check --> recipe + issues
          vision pages only        plain code   line ids,       fills the schema,       plain code   plain code
                                                 doubts          quotes + line ids
```

Code: [llm.py](src/cookbook/llm.py) (model providers), [extract/transcribe.py](src/cookbook/extract/transcribe.py), [compare.py](src/cookbook/extract/compare.py), [transcript.py](src/cookbook/extract/transcript.py), [structure.py](src/cookbook/extract/structure.py), [repair.py](src/cookbook/extract/repair.py), [validate.py](src/cookbook/extract/validate.py), [units.py](src/cookbook/units.py), [models.py](src/cookbook/models.py). Run it with `python -m cookbook.extract file --structure`.

## 1. Reading the pages

Typed pages (`TEXT_DIGITAL`, `TEXT_OCR`) use their text layer and never call a model. Text files are used as they are. `VISION` pages, and images found on typed pages, are rendered (see [ingest.md](ingest.md)) and transcribed by a vision model with one prompt. Its rules, each there because of something that went wrong without it:
- copy exactly, one output line per written line, keep the old spelling, punctuation and line-end hyphens, do not correct, modernise or translate;
- write a number or fraction exactly as shown and never guess one: if it cannot be read, write `[?]`; a hard word goes inside `[? ?]`;
- if the text is in columns, write one column completely before the next, never two columns on one line;
- text inside a drawing (a label on a jar) goes on its own line starting with `[drawing] `;
- do not describe pictures or summarise.

**Two readers.** By default two different models read the same image at the same time, `gemini-3.1-flash-lite` (the first reading is the transcript) and `gemini-3.5-flash-lite`. A reader can instead be a Claude model (`--provider`, `--second-provider claude`), which would make the two readings more independent; that client is built and tested against a stub of the SDK but has not been run on a real page, because there was no key with credits. Using the same model twice is refused. If the second reader fails, the first reading is kept with a warning and no comparison; a blank second reading counts as a failure, not as total disagreement.

### How the two readings are compared ([compare.py](src/cookbook/extract/compare.py))

Plain code, no model. Both readings are normalised (line-end hyphens joined, dashes unified, accents and the `[? ?]` brackets ignored for matching) and their words aligned. Every place they differ is a **flag** on the first reading's words: a difference in a number or fraction is *high*, any other word *medium*. These differences are not content and are ignored: hyphens and spacing ("Korin-then", "Korin then" and "Korinthen" match), punctuation, the same fraction written two ways (`¾` and `3/4`), a drawing label listed in a different order, and a page number that only one reading has.

**Reading order.** On a page with columns one model may read row by row and the other column by column: the same words in a different order. An order-sensitive comparison called such a page unreliable (0.83 agreement and 22 high issues on a two-column old English recipe, although the two readings shared 96% of their words). The comparison now first cuts both readings into blocks of words that are intact in both, found longest first wherever they sit (the idea of greedy string tiling), and keeps the heaviest chain of blocks that kept their order. A block that only moved counts as agreeing and is counted, and the page gets one medium issue `reading_order_differs`. Words in no block are aligned only inside the gap between the same two in-place blocks. What is not forgiven, so an error cannot hide as a move: a lone number (swapped quantities, or a number moved away from its noun, are still flagged); a block of tiny words; a block that ends on a number (it belongs to the unit after it); and a block longer than 12 words, flagged `order`, because the order of the steps of a method matters. After this change the pastry page went to agreement 1.00 with 2 moved blocks, the interleaved ham loaf from 0.83 (unreliable) to 0.99 with 9 moved blocks, and the illegible copies of the Sauerbraten stayed unreliable (0.17 and 0.23): allowing moves did not make unrelated texts look alike.

**Agreement and unreliable pages.** The page agreement is the word similarity of the two readings, 0 to 1. Below 0.85 the whole page is marked unreliable, because on an illegible page each model invents different text. 0.85 is a guess that needs more pages to tune.

**What it cannot tell.** Which reading's order is right (the transcript follows the first reading; check `.txt` against `.second.txt` when a page reports a different order), and mistakes both readers share. Both default readers are Gemini models, so agreement never proves a reading is right.

### What the experiments showed

Two pages of one recipe with degraded copies (one third and one sixth resolution, heavy JPEG, rotated, 150 px and 90 px wide), then five scanned English recipes. The reference transcript is a model reading checked by eye against the clean pages, not a hand-made ground truth ([evals/](evals/README.md)). A rough signal, not an evaluation.
- **Model names retire.** `gemini-flash-latest` and `gemini-2.5-flash` returned 404 for our key, so the code lists models first (`--list-models`) and keeps names in `.env`.
- **Clean pages:** every tested Gemini model read both pages almost perfectly. `gemini-3.5-flash-lite` made real errors ("Josefs" for "Josefines", a dropped page number) and merged lines, which breaks line ids. On the degraded copies it wrote "Mühren" for "Möhren" and "Bratentond" for "Bratenfond". Numbers ("1/2 l", "4 Zwiebeln", "8 Tagen") were never wrong.
- **The `[?word?]` marker is not a confidence signal.** On the readable pages no model ever wrote one, even when it made a mistake. On the illegible copies one model wrote about 25 lines of plausible, wrong text with no marker and invented numbers ("1884" for 1887, "1/2 Stunde"). So the model's own doubt cannot be what tells us a reading is uncertain.
- **Disagreement between two models is a good signal.** Agreement was 0.92 to 0.99 on readable pages, 0.75 on a barely readable one and 0.05 to 0.19 on the illegible ones. On the five readable variants the comparison flagged 10 of the 11 wrong tokens in the first model's output, and about a third of the flagged spans were noise (a dropped page number, a stray dash, a split drawing label).
- **Hidden thinking tokens:** `gemini-3.5-flash` used 1,650 to 3,400 thinking tokens per page against 130 to 300 visible ones, for little accuracy, so it is not used.
- **Image size caused the slow calls** (see ingest.md): 15 to 75 s for a 41 MB PNG, 3 to 6 s for a 1 MB JPEG with the same transcript.
- **Reliability:** repeated 503 "high demand" errors on `gemini-3.5-flash` and Gemma. Hence retry and fallback below.
- **The column prompt rule is not a guarantee.** It worked for one image and failed for the pastry recipe, where the first model read across the columns again. That is why the comparison tolerates moved blocks instead of relying on the prompt.
- **Failures found on the other scans, now fixed:** a model answered with no text, which was scored as 0.00 agreement and marked the page unreliable (16 high issues), so an empty answer is now a failed call that is retried and never cached or compared; one model wrote `¾` where the other wrote `3/4`, flagged as a number disagreement, so fractions are normalised first; a model cited one line of a quote that spans two, now repaired in code.

## 2. The model layer ([llm.py](src/cookbook/llm.py))

Every model call goes through one module, so a provider can be replaced. `GeminiClient` and `ClaudeClient` share a base class that holds everything else: a provider only says how to make one call.
- **Cache.** Answers are cached on disk (`.cache/llm/`, gitignored), keyed by provider, model, prompt and input. Rerunning a page is free and returns the same answer. This is how runs are reproducible (see the README's AI challenges).
- **Retries.** A temporary error (408, 429, 5xx, a timeout) is retried up to 3 times per model, waiting about 1, 2, then 4 seconds (plus up to 25% jitter, at most 20 s). A model that cannot work (404) is skipped at once and a fatal error (400, 401, 403) stops. Each request times out after 90 s, because calls hung for five minutes.
- **Fallback.** If a model keeps failing, the next one in the list is tried (`gemini-3.5-flash` by default; none for Claude, whose next model up costs about 20 times more). A fallback answer is cached under the model that gave it. If both readers end up on the same model the comparison is skipped, because two readings from one model are not independent. The result records how many calls it took, which model it fell back from, and the errors.
- **Empty answers** (blocked or cut off) are failures: retried, never cached, never compared as if they were a reading.
- **Accounting.** Each result carries input, output and thinking tokens and the time; a run totals them (`.run.json`, and printed).
- `ClaudeClient`: images as base64 blocks, no temperature (current Claude models reject a non-default one), reasoning effort low for reading and medium for structuring, structured output through the SDK's schema transform, only text blocks count as the answer. The `anthropic` package is an optional extra.

## 3. The transcript ([transcript.py](src/cookbook/extract/transcript.py))

The pages are joined into one transcript in which every written line has an id, `p1:L14`. A sentence that crosses a page break stays whole (`p1:L23` is followed by `p2:L1`). The transcript keeps the written lines; line-end hyphens ("Bra-" and "tenstücke") are joined only when a quote is looked up. It also carries the **uncertain lines**: where the two readings disagreed, with both readings of the word (so a page can underline it and offer either), and the unreliable pages (all their lines). A person can edit lines; an edited or confirmed line is no longer in doubt.

## 4. Structuring ([structure.py](src/cookbook/extract/structure.py), [models.py](src/cookbook/models.py))

One call (the first model) turns the transcript, with its line ids, into the schema, using the provider's JSON-schema output:

```
Ingredient   id, name, name_en, quantity, quantity_text, unit, prep_note, optional, quote, lines
Tool         id, name, name_en, implied, quote, lines
Step         n, text, ingredient_ids, tool_ids, duration_text, condition, quote, lines
Servings     value, stated, basis, quote, lines
Recipe       title, language, servings, ingredients, tools, steps
Issue        code, severity (high | medium | low), message, refers_to, lines    (the checks produce these)
```

The prompt says: use only what the transcript says; never add an ingredient, quantity or step; every item needs a `quote` copied exactly and the ids of every line it touches; names and steps stay in the recipe's language and each ingredient and tool also gets a short English label (`name_en`, which the vegan and scaling rules read); put an amount's exact words in `quantity_text` and its number in `quantity` only if the text writes one (a vague amount such as "einige" or "nach Geschmack" gives `quantity` null); `optional` is true only when the text presents the ingredient as something you may leave out ("auch kann man", "wer mag"); keep the times and conditions in the step text and never merge steps that have different ones; a tool is `implied` when the text only implies it; when the servings are not stated, give a best estimate with its reasoning in `basis`, `stated` false. The schema's field descriptions are part of what the model receives, and fix the id format (`i1`, `t1`).

The first version of the schema had a per-value `origin` and `confidence`. It was dropped: whether something was read or guessed is `stated` (servings) and `implied` (tools); an amount is either read or `None`, never invented; and uncertainty is an issue found by plain code, not a number the model reports about itself.

If the answer is not valid JSON of the right shape, the model is asked once more with the error; after that the command stops.

## 5. Repair and checks (plain code)

Nothing here calls a model, and nothing drops or silently fixes a value.

**Repair.** The quote is the evidence and the line ids are only a pointer. `clean_quotes` removes a line id the model copied into a quote from the prompt (`"1/2 l\np1:L14 Wasser"` becomes `1/2 l Wasser`; without it the real ingredient looked invented) and records a low `quote_cleaned`. `repair_citations` replaces the ids of an item whose quote is in the transcript but on other lines (usually a quote that runs onto the next line) with the shortest run of consecutive lines that holds it, and records a low `lines_corrected`. A quote found nowhere is left alone.

**Checks** ([validate.py](src/cookbook/extract/validate.py)). Each problem is an `Issue`:

| Issue | Meaning |
|---|---|
| `unsupported_quote` (high) | the quote is not in the transcript (fewer than 80% of its words found, in order): possibly invented |
| `invented_quantity`, `quantity_mismatch`, `unit_mismatch` (high) | an amount with no words behind it, or that does not match its words or unit |
| `uncovered_number` (high) | the transcript has a number that nothing extracted from that line contains: a dropped quantity or duration. Medium outside the extracted text (a header), low for a year there; a number-only line (a page number) and the program's own `[from image]` marker are skipped; a list label that equals the step's number is excused |
| `unknown_reference`, `duplicate_id` (high) | a step uses an ingredient or tool that is not in the list |
| `uncertain_reading` (high or medium) | the value sits on a line where the two readings disagreed |
| `reading_order_differs` (medium) | the readings put blocks of words in a different order; one per page |
| `unused_ingredient`, `duplicate_ingredient`, `unknown_unit`, `servings_unknown`, `servings_inferred` (medium) | for example servings the recipe never states are not guessed silently |
| `no_quantity`, `amount_not_verified`, `lines_corrected`, `quote_cleaned`, `wrong_lines`, `step_order` (low) | the source gives no amount (never guessed), a repair was made, or the amount is written in words the tables do not know |

The coverage check counts only the structured fields (amounts, step text, durations, conditions, servings), never the quotes: a quote proves where a value came from, not that it was extracted. Counting quotes first hid a step that had lost its "nach 8 Tagen".

### Amounts and languages ([units.py](src/cookbook/units.py))

An amount's words are parsed (fractions, `½`, decimals with comma, "ein halbes Pfund", "uma xícara e meia", ranges, vague phrases) and compared with the number the model gave. Digits work in every language. Words live in tables: a `Lexicon` per language (German, English, Portuguese: number words, "half" words, vague phrases) and one shared unit table, ignoring case and accents. Number words are per language because a word can mean different things (English "a" is one, Portuguese "a" is an article, as in "a gosto"). The parser uses the language the model reports for the recipe (`pt-BR` counts as `pt`). In a language with no table the digits are still checked, and an amount in words that nothing knows gets a low `amount_not_verified` instead of a false alarm. A vague phrase that starts with a number word ("um pouco", "ein wenig", "a little") is vague, not the number 1; the Portuguese tests found that bug in all three languages. Historic units convert with their usual size and are marked approximate (a Pfund is 500 g, a Loth about 16.7 g, a Schoppen about 250 ml). A container with its size in front ("9-ounce can") counts as the container. To add a language, add a `Lexicon` to `LEXICONS` and its unit words to `_ALIASES`.

## 6. What structuring showed on the Sauerbraten

First run: 18 ingredients, 7 steps. Reading the result against the transcript found misses the checks had not caught: two steps with different times ("nach dem 1. Tag oder 2. Tag" and "nach 8 Tagen") were merged into one without them, and the coverage check was blind to it because it counted numbers in the model's quote. Counting only structured fields fixed that. Prompt changes (the optional markers "auch kann man", vague words into `quantity_text`, keep times, do not merge steps with different conditions) made the apples, carrots and juniper berries `optional`, recorded "einige" and "soviel", and split the step. The recipe has 18 ingredients, 1 tool (the pot), 8 steps, no high issues; servings are not stated, so the model estimates 4 and the page labels it an estimate.

The prompt was adjusted on this one recipe. Its wording is general, but the test is a recipe it has not seen: five scanned English recipes, a typed English recipe and two mixed PDFs (a Portuguese strogonoff and an English omelette) were run since, and found further flaws (listed above and in the README).

## Considered and rejected

- **Dedicated OCR engines (PaddleOCR, GLM-OCR, MinerU).** They are small, run on a CPU, and give per-word confidence and boxes, but the benchmarks we read are about printed documents and say nothing about cursive German, and the vision models also silently "correct" odd old spellings, which is the real risk and not solved by switching tool. Not used for handwriting; they could cross-check typed pages.
- **Running a model locally.** The development machine has no NVIDIA GPU, 13.8 GB of RAM and 2 GB of integrated graphics, so a vision model would run on the CPU, slowly, and a multi-GB download is a poor fit for a reviewer's three setup commands.
- **Hugging Face hosted inference.** The free credit was reported as small, and a post claimed it was removed (unconfirmed). Gemini's free API tier worked from the first request, so it is the default behind a provider interface; Hugging Face and local models stayed as fallbacks that were never needed.
- **A bigger or "thinking" model.** The hidden reasoning tokens cost several times the visible answer for no accuracy gain on this page.
- **The model's own `[?word?]` marker as the confidence signal.** Measured above: it stays silent while the model is wrong.
- **One model reading the same image twice.** It would repeat its own mistake; two different models disagree where one is wrong.
- **A third reader or a re-read to settle disagreements.** Flagged words are shown as not certain and the reviewer decides, which costs nothing and keeps a person in charge.
- **Named-entity recognition for ingredients and quantities.** A schema-guided model call handles classification and relations; the idea of a span survives as the quote and line ids every value must carry.
- **Reading the structuring twice with two models.** One call plus the checks; the checks cover numbers and quotes, not an ingredient the model never lists, so this is open (below).
- **A confidence score per value from the model.** Models do not report their own doubt reliably; uncertainty is computed from the disagreement and the checks.

## Known limits

- The checks cover numbers and quotes, not words: an ingredient the model never lists is only caught if its line has a number; a wrongly optional ingredient, two steps merged into one, or a missing tool is not caught.
- Both default readers are Gemini models, so a shared mistake is not flagged. The Claude reader is untested on real pages.
- Which reading's order to use is not settled: the transcript follows the first reading even when it is the interleaved one (the structuring still worked on the pastry, by luck of the model).
- Derived ingredients (a mixture the recipe names, "Essigwasser", and the pan juices "Bratenfond") and implied tools are not modelled as ingredients.
- An alternative ("manteiga (ou azeite)") becomes two ingredients; a range ("1 a 2 colheres") keeps the lower number in `quantity` and the words in `quantity_text`.
- A numbered step label with no space after the dot ("1.Temperar") is reported as a high `uncovered_number`: a false alarm the reviewer can check off.
- The 0.85 threshold, the 80% quote support and the table of languages were tuned on a few documents.
- One recipe per input: a PDF with a whole cookbook would be merged into one wrong recipe; pick the pages with `--pages`.

## Tests

All offline: the model is replaced by a fake that returns canned text or JSON. `test_compare.py` (flags, ignored differences, moved blocks, what is not forgiven), `test_transcribe.py` (two readers, failures, same-model refusal), `test_llm_retry.py` (retries, fallback, empty answers, timeouts, the cache), `test_claude.py` (the Claude client against a stub), `test_structure.py` (the call, the retry on bad JSON), `test_repair.py` (citation repair, cleaned quotes), `test_validate.py` (each check with a planted fault: a dropped quantity, a changed number, an invented ingredient, a dropped duration, a year in a header, the program's own marker), `test_units.py` (fractions, decimal comma, number words in three languages, vague phrases, units), `test_run_totals.py` (token and time totals).
