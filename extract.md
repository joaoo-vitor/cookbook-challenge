# Extract stage: working plan

Status: transcription with Gemini and the two-model check are built and tested (see [README.md](README.md)). Retry with fallback is built too. A structured transcript, structuring and validation are still planned. Items marked **(unverified)** came from web searches or my own recollection and must be checked before relying on them.

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

## 6. Provider interface (`src/cookbook/llm.py`)

All model calls go through one small module so the backend can change:

```python
class VisionLLM(Protocol):
    def transcribe(self, image: bytes, prompt: str) -> LLMResult: ...
    # planned: def structure(self, text: str, schema: dict, prompt: str) -> dict: ...
```

- `GeminiClient` implements it now. Another provider can be added later without touching extract code.
- Responses are cached by `hash(provider, model, prompt, image)` in `.cache/llm/` (gitignored), so reruns are reproducible and free.
- The key goes in `.env` as `GEMINI_API_KEY`, with a `.env.example` containing no real value.
- Each result carries input, output and thinking tokens and the time taken, for the README's cost section.
- The image type is read from the first bytes (PNG or JPEG), not assumed.
- Retry and fallback (built): temporary errors (408, 429, 5xx, timeouts) are retried up to 3 times per model with waits of 1, 2, 4 s (plus up to 25% jitter, at most 20 s). A model that cannot work (404) is skipped at once, and a fatal error (400, 401, 403) is raised. Then the next model in the fallback list is tried (`gemini-3.5-flash` by default). A fallback answer is cached under the model that gave it, and the primary model is tried again on the next call. Each request has a 90 s timeout, because we saw calls hang for 5 minutes. Each result records how many calls it took, which model it fell back from, and the errors.

## 7. Pipeline

### 7.1 Transcribe (vision, only for `VISION` pages and image tasks)
- Prompt: transcribe exactly, one line per output line, keep old spelling, write `[?word?]` for anything unreadable, do not interpret, do not fix. (Built.)
- Output: one transcript with line ids (`p1:L1`, ...), pages joined so a sentence crossing a page break stays whole. Hyphenated line ends (like "Bra-tenstücke") are joined by plain code, and the join is recorded. (Line ids exist in the CLI output; the joined, structured transcript is to do.)
- Digital pages (`TEXT_*`) skip this and use the text layer. Image tasks use `merge_page_text`. (Built.)
- **Two reads (built, `compare.py`).** Each page is read by two different models (primary `gemini-3.1-flash-lite`, second `gemini-3.5-flash-lite`), in parallel. The primary reading is the transcript. Then plain code compares the two:
  1. Normalise both (join line-end hyphens, unify dashes, ignore the `[?` `?]` brackets), then align the words.
  2. Every span where they differ becomes a **flag** on the primary's words: "not certain". A difference in a number or fraction is a higher severity than a difference in a word.
  3. Ignore differences that are not content: punctuation-only changes, hyphens, spacing and dash characters (so "Korin-then", "Korin then" and "Korinthen" match), a single non-numeric word that only moved (a drawing label listed in a different order), and a bare number alone on the first or last line that only one read has (a page number). A quantity at the start of a line is never ignored.
  4. Compute a **page agreement** score (word similarity between the two reads). Below about 0.85 the whole page is flagged unreliable (a guess to tune on more pages), because on illegible pages each model invents different text.
  5. A `[?...?]` marker in the primary read is also a flag. Markers in the second read are only counted.
  6. If the second reader fails, the primary reading is kept with a warning and no comparison. Using the same model twice is refused.
  No third model or re-read for now: flagged values are shown as not certain, and the reviewer decides.
  Observed on `Recipe.pdf` (page agreement): clean pages 0.98 and 0.99 with one flag each (one was the second model being wrong, which is still the right thing to flag); the 150 px copy 0.91 and 0.76 (unreliable); the 90 px copy 0.12 and 0.18 (unreliable).

### 7.2 Structure (text to schema)
- One call turns the transcript into the schema below.
- Small models are worse at strict JSON, so: ask for a flat schema, parse defensively, retry once with the parse error, and rely on the validators, not on the model being right.

### 7.3 Validate (plain code)
1. **Grounding:** each value's quote is a fuzzy substring of the transcript.
2. **Number check:** the number parsed from the quote equals the extracted quantity (½ gives 0.5, "1/2", "1,5").
3. **Coverage:** every number and unit in the transcript appears in some extracted field. This catches dropped quantities.
4. **Integrity:** steps reference existing ingredients, every ingredient is used in a step, duplicates are merged or flagged.
5. **Units:** map historic and spoken units through a table (Pfund, Loth, Schoppen, Maß, Esslöffel). Unknown units are flagged, never guessed.
6. **Servings:** stated, or `inferred` with the reasoning shown.
7. **Totals:** only if the recipe states them.

## 8. Schema sketch (`src/cookbook/models.py`)

```
Source        page, line_ids, quote
Value         value, origin (read | inferred | assumed), confidence, source
Ingredient    id, name, quantity (null if none), unit, prep_note, optional, + source/origin
Tool          id, name, origin (stated | implied), source
Step          n, text, ingredient_ids, tool_ids, duration, condition, source
Servings      value, origin (stated | inferred), basis
Recipe        servings, ingredients, tools, steps, flags
Flag          code, severity, message, refers_to
```

Rules: an unquantified ingredient has `quantity = null`, never a made-up number. Guesses go in a separate field with `origin = assumed`.

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
7. [ ] A structured transcript: lines with ids, the source of each line, the model, the flags; join line-end hyphens
8. [ ] `models.py`: schema and flag types
9. [ ] `units.py`: parse quantities (fractions, decimals with comma) and map units, with tests
10. [ ] `validate.py`: the seven checks, with tests using hand-written transcripts that contain planted errors (a dropped quantity, a changed number, an invented ingredient)
11. [ ] `structure.py`
12. [ ] A small eval: a few lines transcribed by hand (kept out of git if copied from the recipe), scored on number accuracy and unsupported-value rate
13. [ ] A second recipe, in a different format or language, and image and plain-text input in ingest

Items 8 to 10 need no model and no network.

## 11. Open questions

- Is a second reader from a different model family available and good enough? Both current readers are Gemini, so shared mistakes would go unflagged. Gemma returned a 503 and other providers are untested for handwriting.
- Is 0.85 the right page agreement threshold? It needs more pages and a harder, more realistic set than synthetic degradation.
- How strict is the coverage check? Numbers only, or also units and ingredient words?
- Where does the hand-transcribed eval text live, so no page content is committed?

## Sources

- [Inference Providers pricing (official)](https://huggingface.co/docs/inference-providers/pricing)
- [Usage Pricing post on removal of free credits (unconfirmed)](https://www.usagepricing.com/blueprint/activity/hugging-face-2026-10-15-free-inference-credits-removed)
- [Free LLM API resources: Hugging Face](https://www.mintlify.com/cheahjs/free-llm-api-resources/providers/free/huggingface)
- [Best open-source OCR models (Roboflow)](https://blog.roboflow.com/best-open-source-ocr-models/)
- [Best open-source vision language models of 2026 (Labellerr)](https://www.labellerr.com/blog/top-open-source-vision-language-models/amp/)
- [CHURRO paper on historical text (arXiv)](https://arxiv.org/pdf/2509.19768v1)
