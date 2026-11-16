# Cookbook Pipeline: Architecture Proposal

Goal: scanned or handwritten recipe → structured, source-traceable recipe → rule-based manipulations (servings, vegan) → cookable output. The pipeline should know when it is unsure.

## Design principles

1. **LLM reads, code decides.** The LLM does perception and language (transcribe, structure, propose substitutions). Anything that can be checked or computed (units, scaling, validation, diffs, rendering) is plain code.
2. **Every value carries provenance.** Each field has `source` (page, line ids, verbatim quote), `origin` (`read` | `inferred` | `assumed`) and `confidence`.
3. **Never fill a gap silently.** An unquantified ingredient has `quantity = null` and `origin = read` ("nach Geschmack"). A guessed value goes into a separate field marked `assumed`.
4. **Verify the LLM mechanically.** Every extracted value must point to a quote that exists in the transcript, or it is rejected or flagged.
5. **Manipulations are pure functions.** `Recipe -> (Recipe, ChangeLog)`. They are deterministic, combinable and explainable.

## Pipeline (LangGraph)

```
 file ──► [1 INGEST] ──► [2a TRANSCRIBE] ──► [2b STRUCTURE] ──► [2c VALIDATE]
          detect+route     vision LLM          LLM + schema        code
                                                                    │ fail → retry once with error feedback
                                                                    ▼
 output ◄── [4 RENDER] ◄── [3 MANIPULATE: rules engine] ◄── Recipe (base, flagged)
            md / json       scale · vegan · …
```

LangGraph earns its place through the conditional retry edge, a checkpointed state (resume and cache), and an optional human-review interrupt before manipulation.

## Stage 1: Ingest

| Input | Detection | Route |
|---|---|---|
| PDF | magic bytes, then per-page text-layer check (`chars > N` and sane char ratio) | digital page → text extraction. Scanned page → render to PNG (~200 dpi) → vision |
| Image | magic bytes / Pillow | straight to vision (auto-rotate, downscale) |
| TXT / MD | decode, detect encoding | straight to structuring |

- Detection is **per page**, because mixed PDFs exist. Output is `Document{pages:[Page{id, kind, text|image}]}`.
- **No text layer:** vision LLM (OCR first is rejected: Tesseract has weak Kurrent/Sütterlin support, and a vision model reads the layout too).
- **Page break:** transcribe all pages in one call when the document is small. For long documents, use overlapping windows and stitch. The transcript is one continuous line-numbered stream (`p1:L12`), so a sentence split across the page break is not cut in two.
- **Drawings:** instruct the model to ignore decoration but note any drawing that carries information (a pot, a quantity mark). Never treat it as evidence for an ingredient.

## Stage 2: Extract

**2a Transcribe (vision, scans only).** Faithful, line-numbered transcription with no interpretation. Illegible words are written as `[?word?]`. The model keeps the old spelling (e.g. "Loth", "Schoppen"), and a normalization pass happens later.

**2b Structure.** Transcript → Pydantic schema via structured output:
- `Ingredient{id, name, name_normalized, quantity, unit, prep_note, optional, source, origin, confidence}`
- `Tool{id, name, origin(stated|implied), source}`
- `Step{n, text, ingredient_ids, tool_ids, duration, condition, source}`
- `Servings{value, origin(stated|inferred), basis}`

**2c Validate (code).**
- The quote is a (fuzzy) substring of the transcript. Otherwise the value is marked unsupported and dropped or flagged. This is the main hallucination guard.
- A quantity number must appear in the quote.
- Units are mapped to a canonical set through a table, including historic units (Pfund, Loth, Schoppen, Maß, Löffel). Unknown units are flagged, never guessed.
- Dedupe by normalized name. Same name with different quantities is kept as two lines and flagged "appears twice" (marinade vs. sauce).
- Referential integrity: step ids exist, every ingredient is used in some step, every step ingredient exists in the list.
- Servings: if the recipe doesn't state it, inference (from quantities and the era's household size) is marked `inferred` with the reasoning shown.
- Optional vs. required: `optional` only if the text says so ("nach Belieben", "wer will"). The default is required.
- Self-consistency on risky fields (quantities, servings): 2 runs at temperature 0 with a different prompt phrasing. A disagreement lowers confidence and raises a review flag.

## Stage 3: Manipulate (rules engine, pure code)

```python
class Rule(Protocol):
    def apply(self, recipe: Recipe) -> tuple[Recipe, list[Change]]: ...
```

Each `Change` has `{target, before, after, rule, reason}`.

**Scale servings.** Factor = target / base servings.
- Each ingredient has a `scaling_class`: `linear` (meat, liquids), `sublinear` (salt, strong spices: factor^0.8, flagged as a suggestion), `fixed` (a bay leaf, one onion rounded to whole units), `to_taste` (unchanged).
- Times are not scaled linearly. They get a flag ("check doneness") and equipment is flagged when the factor is large (pot size).
- If base servings are `inferred`, every scaled value inherits that flag.

**Make vegan.**
1. Deterministic lookup of non-vegan terms and substitutions (curated DE/EN table, with ratios and a time/method note).
2. For unknown ingredients, the LLM classifies `is_vegan` with a reason and proposes a substitute. Its output is schema-checked against the table's units.
3. **Carry through to steps:** the LLM rewrites each affected step under constraints. The code then verifies that no non-vegan term remains and that all numbers and durations are unchanged unless declared in a `Change`. A failure retries once, then flags the step.
4. The main ingredient is the meat: replace it with a substitute (seitan or similar) and add a loud note that time and technique differ. Don't pretend equivalence.

**Combination and order.** Rules run in a canonical order (vegan, then scale). Substitution ratios are linear, so the rules commute. A property test asserts `scale(vegan(r)) == vegan(scale(r))` on quantities.

## Stage 4: Output

- `recipe.md`: ingredient list, steps, and "what changed" (original → new, with reason).
- `recipe.json`: full structure with provenance.
- Markers: ✓ read from source, ~ inferred, ? assumed or unsure. Each is shown inline, with a "Needs review" section at the top listing flags.
- `run_manifest.json`: model, prompt hash, tokens, cost, latency, and validator results.

## AI challenges: planned answers

| Challenge | Approach |
|---|---|
| Hallucination | Quote-grounding validator, `null` over guess, an origin tag on everything, an ingredient-used-in-step check |
| Non-determinism | Temperature 0, structured output, cache keyed by `hash(input, prompt, model)`, self-consistency on risky fields. Tests mock the LLM with recorded fixtures |
| No ground truth | Golden set of ~5 recipes (handwritten, digital, English, no-quantities, tricky units). Metrics: ingredient F1, exact quantity match, unsupported-quote rate |
| LLM vs. code | LLM: transcription, structuring, unknown-ingredient classification, step rewrite. Code: everything else. In production, move the substitution table to curated data and the validators to hard gates |
| Cost / latency | Small model for structuring, stronger vision model only for scans. Measure per run and report it in the README. Expect cents and a few seconds |
| Trust | Provenance and confidence in the output, plus a review flag list |

## Repo layout

```
cookbook/
  ingest/      detect.py  pdf.py  render.py
  extract/     transcribe.py  structure.py  validate.py  units.py  schema.py
  manipulate/  base.py  scale.py  vegan.py  data/substitutions.yaml
  output/      markdown.py  json_out.py  diff.py
  llm/         client.py (cache, retries, cost tracking)
  graph.py     cli.py
tests/         test_units  test_scale  test_vegan_rules  test_commute  test_validate
evals/         golden/  run_evals.py
examples/      sauerbraten_{raw,6,vegan,vegan_6}.{md,json}
AI_LOG.md  README.md  .env.example  .gitignore   # Recipe.pdf ignored
```

## Build order (6 to 10 hours)

1. Schema, units and scale rule with tests (pure code). ~1.5h
2. LLM client with cache, then transcribe and structure on the Sauerbraten. ~2h
3. Validators, then retry. ~1.5h
4. Vegan rule and step rewrite checks. ~1.5h
5. Renderers and the LangGraph wiring. ~1h
6. Second recipe, a mini eval, README and AI_LOG. ~1.5h

## Open decisions

- **Provider:** I suggest Anthropic: Haiku for structuring and Sonnet for handwriting vision. Check cost on the real pages.
- **Handwriting:** if the scan is Kurrent, expect misreads. Surface the `[?word?]` markers rather than hiding them.
- **Once `Recipe.pdf` is available**, I'll tailor the README's challenge section to the actual words the model misreads.
