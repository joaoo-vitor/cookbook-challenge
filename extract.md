# Extract stage: working plan

Status: transcription with Gemini and the two-model check are built and tested (see [README.md](README.md)). Retry with fallback, the schema, unit parsing, structuring and the checks are built too. A hand-checked eval and a second recipe are still to do. Items marked **(unverified)** came from web searches or my own recollection and must be checked before relying on them.

## 1. Goal

Turn the pages that ingest produces into a structured recipe: ingredients, tools, steps, servings. Every value must be traceable to the source, and anything guessed must be marked as a guess.

Input: `analyze_pdf` results, `render_page` (JPEG for `VISION` pages), `render_task` (JPEG for an image region), `merge_page_text`. See [ingest.md](ingest.md).

## 2. Decisions so far

| Topic | Decision |
|---|---|
| Call design | Two calls: **transcribe** (vision), then **structure** (text to schema) |
| Confidence | **Two reads, one per model.** Where the two reads differ, the word or number is flagged as not certain. A page where the two reads mostly disagree is flagged as unreliable. The `[?word?]` marker stays in the prompt, but only as an extra warning: models use it unreliably (section 5.3) |
| Failed checks | Keep the value and flag it. Never drop silently |
| Check style | Plain deterministic code (grounding, number match, coverage, integrity) |
| Model | **Gemini on the free API tier**, behind a provider interface. Primary reader `gemini-3.1-flash-lite`, second reader `gemini-3.5-flash-lite` (section 5.3). Hugging Face and Claude Haiku 5.5 stay as options |
| Images sent | A JPEG at the photo's own resolution, at most 2400 px on the longest side (never a fixed 200 dpi, see [ingest.md](ingest.md)) |
| NER | Not used. The schema-guided model call handles classification and relations. We keep the "span" idea: each value cites a transcript line and quote |

## 3. Constraints we found

**Hardware (this machine):** AMD Ryzen 5 5600G (6 cores), integrated Radeon graphics with 2 GB, 13.8 GB RAM, **no NVIDIA GPU**. Running a vision model locally means CPU only. Expect it to be slow, and a 7B model is a tight fit in memory.

**Free hosted inference is uncertain (unverified):**
- The official Hugging Face pricing page says free accounts get a small monthly credit (listed as $0.10), with no pay-as-you-go beyond it.
- One unofficial post dated 2026-10-15 claims free credits were removed. I could not confirm that against the live page.
- Aggregator sites list some vision models as free with rate limits, but those sites may be out of date.
- **Action:** read the live pricing page and try one real request before designing around it.

**Reviewer constraint (from the assignment):** the repo must run in at most three setup commands, and the reviewers run it on an unseen recipe. A free Hugging Face token is easy for them to create. A multi-GB local model download is not.

## 4. Where the model could run

| Option | Cost | Pros | Cons |
|---|---|---|---|
| **HF Inference Providers** (hosted) | free credit, possibly none (unverified) | No local setup, works on this machine, easy for reviewers (token in `.env`) | Credit may run out. Which models are served changes. Rate limits |
| **Local on CPU** (for example a small Qwen VL via `transformers` or a GGUF runtime) | free | No account, no limits | Slow on this CPU (minutes per page is plausible), memory-tight, large download |
| **Free GPU notebook** (for example Colab) | free | Fast enough for a 7B model | Not part of the repo's run path. Only useful for trying models and building the eval |

Superseded: the Gemini free tier worked from the first request and is what we use (section 5.3). Hugging Face and local models stay as fallbacks if Gemini becomes unavailable.

## 5. Candidate models (all unverified, check each model card)

- **Qwen2.5-VL (3B or 7B):** general vision-language model with OCR ability. Open license on the model card (verify). Has been recommended for OCR on small GPUs.
- **Small dedicated OCR models** (PaddleOCR-VL, GLM-OCR, MinerU): top of a 2026 printed-document benchmark. That benchmark is not about handwriting, so they may not suit this recipe.
- **Llama 3.2 11B Vision:** listed by aggregators as served on HF. Gated, so you must accept a license first.
- **A historical-text VLM (CHURRO, 3B):** a paper reports it beats larger open models on historical handwriting. Check whether weights are public and whether German is covered.

**How to choose:** run 2 or 3 models on page 1 of `Recipe.pdf` and compare against a few lines you transcribe by hand. Prefer the smallest model that gets the quantities right. Known trouble spot: the word "Pfeffer" looked like "Pfetter" in my own reading.

### 5.1 OCR engines versus vision-language models

Source: a Roboflow article from 2026-10-01, read through a subagent. The benchmark is Roboflow's own and the article does not describe its dataset or metric, so treat the numbers as vendor-provided. It reports no handwriting scores and never mentions German.

| | Dedicated OCR (PaddleOCR, GLM-OCR, ...) | Vision-language model |
|---|---|---|
| Cost and speed | small, runs on CPU | pricier and slower per page |
| Failure mode | recognition errors on poor images | may silently "correct" odd wording or invent plausible values, risky for numbers |
| Confidence scores | usually per word | often none |
| Locations | bounding boxes | less precise |
| Repeatability | stable | can vary between runs |

Reported OCR scores (all within about 90% to 94%, so the gaps are small): Claude Fable 5 94.0, Muse Spark 1.2 93.6, Qwen3.8 Max 93.4, Kimi K3 (open) 93.0, Gemini 3.1 Pro 92.6, Qwen3.8 27B (open) 92.2, Gemma 4 31B (open) 90.8.

What this means for us:
- The silent-correction problem is real, so the grounding, number and coverage checks (section 7.3) are the real safeguard, not the model choice.
- The open models in that list are too big for this machine (CPU, 13.8 GB RAM). They would have to be hosted.
- A dedicated OCR engine could give per-word confidence and boxes on **typed** pages as a cross-check. For cursive handwriting we still need a vision model. (The article has no data on dedicated OCR with handwriting.)

### 5.2 Gemini first

Gemini's own chat answer about free versus paid tiers was pasted in. It is model output, not documentation, so I treat it as a lead to verify:
- Credible and matching the Roboflow article: the model may **quietly normalize** an ambiguous word into modern spelling. For us that means the transcript must be checked against hand-read lines, and the prompt must forbid correcting.
- Doubtful or unverified: that structured JSON output is limited to paid tiers (I believe schema-constrained output is a feature of the API, not of the tier), that the free tier downsamples images, and that context size differs by tier. "Gemini Advanced" is a consumer subscription, separate from the API.
- What probably does differ on the free API tier: rate and daily limits, which models are available, and possibly data use (free-tier prompts may be used for training, so a scanned book page could be used).
- **Action:** check the limits for your own key in Google AI Studio, and keep the model name configurable (`GEMINI_MODEL`), because names change.

Experiment plan: transcribe page 1 and page 2 of `Recipe.pdf` with Gemini, save the text and token usage to `data/experiments/` (gitignored), then compare against lines you read by hand. Repeat with the next candidate and compare. Record for each: misread words and quantities, notes marked `[?...?]`, tokens, latency, and whether it corrected old spelling.

### 5.3 What the experiments showed

All numbers come from two pages of one recipe, so they are a rough signal, not an evaluation. The reference transcript is a model output (`gemini-3.8-flash`) that was checked by eye against the clean pages; there is no hand-made ground truth yet. Scripts: [evals/](evals/README.md).

- **Model names change and retire.** `gemini-flash-latest` and `gemini-2.5-flash` returned 404 for our key. Always list the models first (`python -m cookbook.extract --list-models`).
- **Clean pages:** all tested Gemini models read both pages almost perfectly. `gemini-3.1-flash-lite` matched the reference apart from a dash and a hyphen. `gemini-3.5-flash-lite` made real errors ("Josefs" for "Josefines", dropped the page number) and merged lines, which breaks line ids.
- **Degraded pages** (one third resolution, heavy JPEG, rotated, one sixth resolution): similarity to the reference stayed at 0.96 to 0.99. Numbers ("1/2 l", "4 Zwiebeln", "8 Tagen") were never wrong. The weaker model made small word errors ("Mühren" for "Möhren", "Bratentond").
- **The `[?word?]` marker is unreliable.** On the readable pages (including degraded ones) no model ever wrote one, even when it made a mistake. On illegible copies (150 px and 90 px wide) the models wrote some markers, but one model also wrote about 25 lines of plausible, wrong text with no marker, and invented numbers ("1884" for 1887, "1/2 Stunde"). So the model's own doubt cannot be the confidence signal.
- **Disagreement between two models is a good signal.** Word agreement between `gemini-3.1-flash-lite` and `gemini-3.5-flash-lite` was 0.92 to 0.99 on readable pages, 0.75 on one barely readable page, and 0.05 to 0.19 on the illegible ones. On the five readable variants, comparing the two flagged 10 of the 11 wrong tokens in the primary model's output, and about a third of the flagged spans were noise (a dropped page number, a stray dash, a split drawing label). Both readers are Gemini models, so a mistake they share would not be flagged. The 0.85 page threshold below is a guess to tune.
- **Hidden thinking tokens:** `gemini-3.5-flash` used roughly 1,650 to 3,400 thinking tokens per page against 130 to 300 visible tokens, with little accuracy gain, so we do not use it.
- **Image size was the main cause of slow calls.** Sending a 41 MB PNG (a fixed 200 dpi render) took 15 to 75 s per page. A JPEG of about 1 MB takes 3 to 6 s with the same transcript quality.
- **Reliability:** repeated 503 "high demand" errors on `gemini-3.5-flash` and Gemma. The client needs retry with backoff and a fallback model.
- **Columns (a two-column ingredient list, an old English recipe):** with the first prompt the two readers read the same words in a different order. `gemini-3.1-flash-lite` read row by row across the columns, so each line mixed two ingredients, while `gemini-3.5-flash-lite` read column by column. Ignoring order the readings shared 96% of their words, but the comparison is order-sensitive, so it gave 0.83 agreement, marked the page unreliable and produced 22 high issues. After adding the column rule to the prompt (one column completely before the next) both readers read column by column, agreement was 1.00, and the structured recipe had all 10 ingredients with correct amounts; the issues fell to 2 high, 1 medium. The prompt rule worked for this image; it is not a guarantee, because a model may still ignore it. The comparison was order-sensitive at that point; that is fixed by the block comparison (7.1, item 3b). Layout is still not detected in the image.
- **The two issues left on that recipe came from the checks, not the reading, and are fixed:** a quote that kept a hyphenated line break ("pre-" and "pared") was reported unsupported, and a package size inside a unit ("1 9-ounce can") was an unknown unit and left the "9" uncovered. The quote is now also tried with such breaks joined, and a container with its size in front ("9-ounce can", "16 oz. jar") is counted by its container, with the numbers in the unit counting as covered. The recipe now has 0 high and 0 medium issues. Pancakes and the Sauerbraten still have no high issues.
- **Four more scanned English recipes** (one or two columns, from old magazine pages) found three more flaws, now fixed: (1) a model answered with no text; the empty answer was treated as a real reading, scored 0.00 agreement and marked the page unreliable (16 high issues), so an empty answer is now a failed call that is retried, falls back and is never cached; (2) one model wrote `¾` where the other wrote `3/4`, which was flagged as a number disagreement, so fractions are normalised before comparing; (3) the model cited one line of a quote that spans two, so the citations are repaired in code. After the fixes: ham loaf, baked apple dumplings, cheese pudding and eggless plum pudding all have 0 high issues (servings unknown on the last two is correct, the pages do not say). The prompt rule for columns did **not** hold for the fifth, a pastry recipe: the primary model read row by row across the columns again, so the comparison still reported whole ingredient lines as moved (3 high flags, 4 high issues). With the block comparison the pastry has agreement 1.00, 2 moved blocks, no flags and no high issues (one medium note that the reading order differs), and the original interleaved ham loaf went from 0.83 (unreliable) to 0.99 with 9 moved blocks. The illegible copies of the Sauerbraten stay unreliable (0.17 and 0.23): allowing moves did not make unrelated texts look alike.

## 6. Provider interface (`src/cookbook/llm.py`)

All model calls go through one small module so the backend can change:

```python
class VisionLLM(Protocol):
    def transcribe(self, image: bytes, prompt: str) -> LLMResult: ...
    # planned: def structure(self, text: str, schema: dict, prompt: str) -> dict: ...
```

- `GeminiClient` and `ClaudeClient` implement it. The cache, retries and fallback live in a shared base class, so a provider only says how to make one call. `make_client("gemini" | "claude")` builds either.
- `ClaudeClient` (built, tested offline with a stub of the SDK; **not yet run against the real API**: there was no key): images go as base64 blocks (type from the first bytes), no temperature (the current Claude models reject a non-default one), `output_config.effort` low for reading and medium for structuring (reasoning is billed as output), structured output through `output_config.format` with the schema passed through the SDK's `transform_schema` (the API needs `additionalProperties: false` on every object), only text blocks count as the answer, and an empty, refused or cut-off answer is an error that is retried like any other. The SDK's own retries are off. The `anthropic` package is an optional extra. Default model `claude-haiku-5-5`, with no default fallback (the next model up costs about 20 times more). Each reader chooses its provider independently (`--provider`, `--second-provider`).
- Responses are cached by `hash(provider, model, prompt, image)` in `.cache/llm/` (gitignored), so reruns are reproducible and free.
- The key goes in `.env` as `GEMINI_API_KEY`, with a `.env.example` containing no real value.
- Each result carries input, output and thinking tokens and the time taken, for the README's cost section.
- The image type is read from the first bytes (PNG or JPEG), not assumed.
- Retry and fallback (built): temporary errors (408, 429, 5xx, timeouts) are retried up to 3 times per model with waits of 1, 2, 4 s (plus up to 25% jitter, at most 20 s). A model that cannot work (404) is skipped at once, and a fatal error (400, 401, 403) is raised. Then the next model in the fallback list is tried (`gemini-3.5-flash` by default). A fallback answer is cached under the model that gave it, and the primary model is tried again on the next call. Each request has a 90 s timeout, because we saw calls hang for 5 minutes. Each result records how many calls it took, which model it fell back from, and the errors.

## 7. Pipeline

### 7.1 Transcribe (vision, only for `VISION` pages and image tasks)
- Prompt: transcribe exactly, one line per output line, keep old spelling, write `[?word?]` for anything unreadable, do not interpret, do not fix, and write one column completely before the next (never two columns on one line). (Built.)
- Output: one transcript with line ids (`p1:L1`, ...), pages joined so a sentence crossing a page break stays whole. Hyphenated line ends (like "Bra-tenstücke") are joined by plain code, and the join is recorded. (Line ids exist in the CLI output; the joined, structured transcript is to do.)
- Digital pages (`TEXT_*`) skip this and use the text layer. Image tasks use `merge_page_text`. (Built.)
- **Two reads (built, `compare.py`).** Each page is read by two different models (primary `gemini-3.1-flash-lite`, second `gemini-3.5-flash-lite`), in parallel. The primary reading is the transcript. Then plain code compares the two:
  1. Normalise both (join line-end hyphens, unify dashes, ignore the `[?` `?]` brackets), then align the words.
  2. Every span where they differ becomes a **flag** on the primary's words: "not certain". A difference in a number or fraction is a higher severity than a difference in a word.
  3. Ignore differences that are not content: punctuation-only changes, hyphens, spacing and dash characters (so "Korin-then", "Korin then" and "Korinthen" match), the same fraction written two ways (`¾` and `3/4`), and a bare number alone on the first or last line that only one read has (a page number). A quantity at the start of a line is never ignored.
  3b. **Reading order (built).** The two reads are first cut into blocks: runs of words intact in both reads (same words, same order), found longest first and wherever they sit (the same idea as greedy string tiling used to compare reordered texts). The heaviest chain of blocks that keep their order in both reads is "in place"; the other blocks were moved. A moved block counts as agreeing and is counted in `Comparison.reordered`; the page gets one medium issue `reading_order_differs`. The words not in any block are then aligned in order, but only inside the gap between the same two in-place blocks, so a word is only matched in its own context. What is not forgiven, so that an error cannot hide as a move: a lone number (swapped quantities, or a number moved away from its noun, are still flagged); a block of tiny words ("in the", which matches anywhere and would steal words from real blocks); a block that ends on a number (it belongs to the unit after it, so "flour 1" cannot steal the 1 of the next ingredient); and a block longer than 12 words, which is flagged `order` because the order of the steps of a method matters.
  4. Compute a **page agreement** score (word similarity between the two reads). Below about 0.85 the whole page is flagged unreliable (a guess to tune on more pages), because on illegible pages each model invents different text.
  5. A `[?...?]` marker in the primary read is also a flag. Markers in the second read are only counted.
  6. If the second reader fails, the primary reading is kept with a warning and no comparison. Using the same model twice is refused.
  No third model or re-read for now: flagged values are shown as not certain, and the reviewer decides.
  Observed on `Recipe.pdf` (page agreement): clean pages 0.98 and 0.99 with one flag each (one was the second model being wrong, which is still the right thing to flag); the 150 px copy 0.91 and 0.76 (unreliable); the 90 px copy 0.12 and 0.18 (unreliable).

### 7.2 Structure (text to schema) (built, `structure.py`)
- One call (the primary model) turns the transcript, with its line ids, into the schema below, using Gemini's JSON schema output. The prompt forbids adding anything, requires a verbatim quote and line ids for every item, and says how to treat vague amounts, optional ingredients, implied tools and unstated servings.
- Names and steps stay in the recipe's language; each ingredient and tool also gets a short English label for rules.
- The answer is cached, retried and falls back to another model like the readings. If it is not valid JSON of the right shape the model is asked once more with the error, then the command stops.
- The schema lives in `models.py` (pydantic). The transcript object (`extract/transcript.py`) joins the pages with line ids, joins line-end hyphens when a quote is looked up, and carries the uncertain lines from the two-model comparison.

### 7.3 Validate (plain code, built, `validate.py`)
The checks never fix or drop anything. Each problem is an `Issue` with a code and a severity.
1. **Grounding:** each item's quote must be in the transcript, on the lines it cites (at least 80% of its words, in order, ignoring case, punctuation and hyphens; a quote that kept a hyphenated line break matches too). Not found anywhere: `unsupported_quote` (high), possibly invented. Found on other lines: `wrong_lines` (low).
2. **Amount check:** a quantity needs words behind it (`invented_quantity`), and the number parsed from those words (½, 1/2, 1,5, "ein halbes Pfund") must equal the quantity (`quantity_mismatch`). The unit must be known (`unknown_unit`) and match the words (`unit_mismatch`). An ingredient with no amount is fine (`no_quantity`, low): amounts are never guessed.
3. **Coverage:** every number on a transcript line must appear in a structured field extracted from that line (amount, step text, duration, condition, servings), not only in a quote. This catches dropped quantities and durations (`uncovered_number`, high; medium on a line before the first or after the last extracted line, such as a header). A page number alone on a line is skipped.
4. **Integrity:** steps reference ingredients and tools that exist, ids are unique, every ingredient is used in a step, the same ingredient is not listed twice, steps are numbered in order.
5. **Servings:** missing is `servings_unknown`; a guess is `servings_inferred` with its reason shown.
6. **Uncertainty:** an item on a line where the two readings disagreed is `uncertain_reading`; severity follows the comparison's.
7. **Totals:** only if the recipe states them (none of the recipes seen so far do).

Unit conversion (`units.py`): Pfund is 500 g, Loth about 16.7 g (approximate), Schoppen about 250 ml (approximate: a quarter to half a litre by region), spoons and cups are rough volumes (approximate), counted units (Prise, Stück, Zehe, Dose, Glas, Packung, Flasche) do not convert, and a container with its size in front ("9-ounce can") counts as the container.

Citation repair (`repair.py`, runs before the checks): the quote is the evidence and the line ids are only a pointer. When an item's quote is in the transcript but not on the lines it cites (the model cited one line of a quote that runs on to the next), the lines are replaced by the shortest run of consecutive lines that holds the quote, nearest to the original, and a low `lines_corrected` issue records what was cited. A quote found nowhere is left alone and stays `unsupported_quote`. The content is never changed, only the pointer.

The schema fields carry descriptions, which the model receives as part of the schema (a comment in the code would not reach it). They fix the id format (`i1`, `t1`), say that `lines` lists every line the quote touches, and explain `stated` and `implied`. Before this the model chose its own ids (`loaf_pan`, `baking_powder`).

### 7.4 What structuring showed on `Recipe.pdf`
One run of `gemini-3.1-flash-lite` on the transcript, after one round of prompt changes. A single recipe, so a rough signal.
- First run: 18 ingredients, 7 steps, 1 high issue. Reading the result against the transcript found real misses the checks had not caught at first: two steps with different times ("nach dem 1. Tag oder 2. Tag", "nach 8 Tagen") were merged into one without them, and the coverage check was blind to it because it counted numbers in the model's quote. Counting only the structured fields fixed that.
- Prompt changes (optional markers like "auch kann man", vague words into `quantity_text`, keep times and conditions, do not merge steps with different conditions) turned the optional apples, carrots and juniper berries into `optional`, recorded "einige" and "soviel", and split the merged step. Second run: 17 ingredients, 9 steps, no high issues, three medium (one uncertain reading, servings unknown, the header year).
- **Overfitting risk:** the prompt was adjusted on this one recipe. Its wording is general, but the real test is a recipe it has not seen (build order item 13).
- Known gaps: only one tool (the pot) and no implied ones; a mixture the recipe names ("Essigwasser", made of two listed ingredients) is not an ingredient and steps that use it list no ingredient; servings are not guessed (the model chose null). A step's `duration_text` often holds a condition instead ("nach 8 Tagen" went into `condition`).

## 8. Schema sketch (`src/cookbook/models.py`)

What was built (pydantic classes in `models.py`; every item has a `quote` and the `lines` it came from):

```
Ingredient    id, name, name_en, quantity, quantity_text, unit, prep_note, optional, quote, lines
Tool          id, name, name_en, implied, quote, lines
Step          n, text, ingredient_ids, tool_ids, duration_text, condition, quote, lines
Servings      value, stated, basis, quote, lines
Recipe        title, language, servings, ingredients, tools, steps
Issue         code, severity (high | medium | low), message, refers_to, lines     (the checks produce these)
```

The first sketch had a per-value `origin` and `confidence`. That was simplified: whether something was read or guessed is `stated` (servings) and `implied` (tools); an amount is either read (`quantity_text` holds its words) or `None`, never invented; and uncertainty is an `Issue` found by the checks, not a number the model reports.

Rules: an unquantified ingredient has `quantity = null`, never a made-up number. A guess is marked (`stated = false` with a `basis`).

## 9. Small-model risks

- **More hallucination and misreads** than a large model. The validators matter more, not less.
- **Weaker JSON discipline:** expect malformed output sometimes.
- **Inconsistent runs:** the newer Claude models reject non-default sampling settings, and hosted endpoints may too (unverified for others), so lean on caching and the two-model comparison for reproducibility.
- **Confident invention:** on unreadable input a model can write plausible text, including numbers, with no warning (section 5.3). The page agreement check and the grounding checks exist for this.
- **Language:** check that the model handles German handwriting at all before building on it.

## 10. Build order

1. [x] Choose a provider: Gemini free tier (HF pricing check no longer needed)
2. [x] `llm.py`: provider interface, Gemini client, cache, token and time logging
3. [x] `transcribe.py`, and compare models on both pages, including degraded and illegible copies (section 5.3)
4. [x] Send smaller JPEG images (a fixed 200 dpi render was the cause of slow calls)
5. [x] Retry with backoff and a fallback model in `llm.py`
6. [x] Two-model read: compare, flag differing words and numbers, page agreement score (section 7.1)
7. [x] A transcript object: all pages with line ids and the uncertain lines (`extract/transcript.py`). Line-end hyphens are joined when quotes are looked up; the transcript itself keeps the written lines
8. [x] `models.py`: schema and issue type
9. [x] `units.py`: parse quantities (fractions, decimals with comma, number words, ranges, vague amounts) and convert units, with tests
10. [x] `validate.py`: the checks, with tests using a made-up recipe and planted errors (a dropped quantity, a changed number, an invented ingredient, ...)
11. [x] `structure.py`: the model call, retry on invalid JSON, then the checks
12. [ ] A small eval: a few lines transcribed by hand (kept out of git if copied from the recipe), scored on number accuracy and unsupported-value rate
13. [ ] A second recipe, in a different format or language, and image and plain-text input in ingest, to test the prompt on something it was not tuned on
14. [ ] Derived ingredients (a named mixture like "Essigwasser") and implied tools, which the rules for vegan and scaling will need
15. [x] An order-tolerant comparison of the two readings (section 7.1, item 3b). Not a plain word count, which would let swapped quantities through. Intact blocks that moved are forgiven and reported; a lone number, tiny words and long blocks are not.
16. [ ] Which reading's order to use. The transcript follows the first reading even when it is the interleaved one; the structuring still worked on the pastry, but it is luck of the model, not a guarantee. Options: prefer the reading with fewer blocks mixing two columns, or detect the columns in the image.

## 11. Open questions

- Is a second reader from a different model family good enough? The Claude client exists (`--second-provider claude`) but has not been run on the real pages yet. The first thing to measure is its agreement with Gemini on the clean pages, the degraded copies and the five scans, and what its disagreements look like. Gemma returned a 503 and other providers are untested for handwriting.
- Is 0.85 the right page agreement threshold? It needs more pages and a harder, more realistic set than synthetic degradation.
- How strict is the coverage check? It covers numbers only. Words are not checked: an ingredient the model never lists is only caught if its line has a number.
- Should the structuring be read twice by two models, like the transcript? We chose one call plus the checks; revisit if the second recipe shows misses the checks cannot see.
- Where does the hand-transcribed eval text live, so no page content is committed?

## Sources

- [Inference Providers pricing (official)](https://huggingface.co/docs/inference-providers/pricing)
- [Usage Pricing post on removal of free credits (unconfirmed)](https://www.usagepricing.com/blueprint/activity/hugging-face-2026-10-15-free-inference-credits-removed)
- [Free LLM API resources: Hugging Face](https://www.mintlify.com/cheahjs/free-llm-api-resources/providers/free/huggingface)
- [Best open-source OCR models (Roboflow)](https://blog.roboflow.com/best-open-source-ocr-models/)
- [Best open-source vision language models of 2026 (Labellerr)](https://www.labellerr.com/blog/top-open-source-vision-language-models/amp/)
- [CHURRO paper on historical text (arXiv)](https://arxiv.org/pdf/2509.19768v1)
