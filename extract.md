# Extract stage: working plan

Status: planning. Nothing here is built yet. Items marked **(unverified)** came from web searches or my own recollection and must be checked before relying on them.

## 1. Goal

Turn the pages that ingest produces into a structured recipe: ingredients, tools, steps, servings. Every value must be traceable to the source, and anything guessed must be marked as a guess.

Input: `analyze_pdf` results, `render_page` (JPEG for `VISION` pages), `render_task` (JPEG for an image region), `merge_page_text`. See [ingest.md](ingest.md).

## 2. Decisions so far

| Topic | Decision |
|---|---|
| Call design | Two calls: **transcribe** (vision), then **structure** (text to schema) |
| Confidence | The model marks unreadable words as `[?word?]`. Numbers get a **second read**, and a disagreement lowers confidence |
| Failed checks | Keep the value and flag it. Never drop silently |
| Check style | Plain deterministic code (grounding, number match, coverage, integrity) |
| Model | **Gemini first** (free API tier), then one or two other candidates, all behind a provider interface. Hugging Face and Claude Haiku 5.5 stay as options (see sections 4 and 5) |
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

Suggested start: try **hosted HF first** for speed of development, and keep **local** as a fallback if credits run out. Decide after the live pricing check.

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

## 6. Provider interface (`src/cookbook/llm.py`)

All model calls go through one small module so the backend can change:

```python
class VisionLLM(Protocol):
    def transcribe(self, image: bytes, prompt: str) -> LLMResult: ...
    def structure(self, text: str, schema: dict, prompt: str) -> dict: ...
```

- A `HuggingFaceClient` implements it now. Another provider can be added later without touching extract code.
- Responses are cached by `hash(input, prompt, model)` in `.cache/` (gitignored), so reruns are reproducible and free.
- The token goes in `.env` as `HF_TOKEN`, with a `.env.example` containing no real value.
- Log token counts and latency per call for the README's cost section.

## 7. Pipeline

### 7.1 Transcribe (vision, only for `VISION` pages and image tasks)
- Prompt: transcribe exactly, one line per output line, keep old spelling, write `[?word?]` for anything unreadable, do not interpret, do not fix.
- Output: one transcript with line ids (`p1:L1`, ...), pages joined so a sentence crossing a page break stays whole. Hyphenated line ends (like "Bra-tenstücke") are joined by plain code, and the join is recorded.
- Digital pages (`TEXT_*`) skip this and use the text layer. Image tasks use `merge_page_text`.
- Second read: re-read only lines containing numbers or fractions, and compare.

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
- **Inconsistent runs:** sampling settings may not be controllable on hosted endpoints (unverified), so lean on caching and the second read for reproducibility.
- **Language:** check that the model handles German handwriting at all before building on it.

## 10. Build order

1. [ ] Read the live HF pricing page and try one request with a vision model (settles hosted vs local)
2. [ ] `models.py`: schema and flag types
3. [ ] `units.py`: parse quantities (fractions, decimals with comma) and map units, with tests
4. [ ] `validate.py`: the seven checks, with tests using hand-written transcripts that contain planted errors (a dropped quantity, a changed number, an invented ingredient)
5. [ ] `llm.py`: provider interface, HF client, cache, logging
6. [ ] `transcribe.py`, then compare 2 or 3 models on page 1
7. [ ] `structure.py`
8. [ ] A small eval: a few lines transcribed by hand (kept out of git if copied from the recipe), scored on number accuracy and unsupported-value rate
9. [ ] A second recipe, in a different format or language

Steps 2 to 4 need no model and no network, so they can start now.

## 11. Open questions

- Hosted or local first? (Depends on item 1 in the build order.)
- Which models do we compare, and on how many lines?
- How strict is the coverage check? Numbers only, or also units and ingredient words?
- Where does the hand-transcribed eval text live, so no page content is committed?

## Sources

- [Inference Providers pricing (official)](https://huggingface.co/docs/inference-providers/pricing)
- [Usage Pricing post on removal of free credits (unconfirmed)](https://www.usagepricing.com/blueprint/activity/hugging-face-2026-10-15-free-inference-credits-removed)
- [Free LLM API resources: Hugging Face](https://www.mintlify.com/cheahjs/free-llm-api-resources/providers/free/huggingface)
- [Best open-source OCR models (Roboflow)](https://blog.roboflow.com/best-open-source-ocr-models/)
- [Best open-source vision language models of 2026 (Labellerr)](https://www.labellerr.com/blog/top-open-source-vision-language-models/amp/)
- [CHURRO paper on historical text (arXiv)](https://arxiv.org/pdf/2509.19768v1)
