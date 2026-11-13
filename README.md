# Cookbook challenge

A pipeline that turns a scanned or typed recipe into structured data you can scale and make vegan.
Built for the finlyt engineering case study. Stages: **ingest, extract, manipulate, output**.

## Status

| Stage | What it does | Status | Guide |
|---|---|---|---|
| Ingest | Decides, per PDF page, how to read it (text layer, OCR layer, or vision model) | Built (PDF only) | [ingest.md](ingest.md) |
| Extract | Transcribes pages with a vision model; structuring and validation come next | Transcription built, rest planned | [extract.md](extract.md) |
| Manipulate | Change servings, make vegan | Not started | |
| Output | Step-by-step recipe, changes against the original | Not started | |

Not built yet: images and plain-text input, structured extraction, validators, the rules engine, the output renderers, `examples/` and `AI_LOG.md`.

## Setup

You need Python 3.10 or newer. From the project folder:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

On macOS or Linux, activate with `source .venv/bin/activate` instead. Activate the environment again in every new terminal.

If PowerShell refuses to run the activation script, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

### API key (only needed for the extract stage)

The extract stage calls the Gemini API. Copy the example file and put your key in it:

```powershell
Copy-Item .env.example .env
```

Then edit `.env` and set `GEMINI_API_KEY=`. Optionally set `GEMINI_MODEL=` as well. `.env` is gitignored, so never commit it.

### The recipe PDF

`Recipe.pdf` (the scanned Sauerbraten) is **not** in the repository, because it is a page from a printed book. Put your copy in the project folder next to this README. The name is already in `.gitignore`. Other recipes work the same way: any PDF you pass on the command line is read from where it is.

## Running each stage

### Ingest

Shows how each page of a PDF would be read.

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

Reads every page and writes a transcript. Typed pages use their text layer and never call the model. `VISION` pages and images on typed pages are rendered (a JPEG at the photo's own resolution, at most 2400 px on the longest side) and sent to Gemini.

First, see which models your key can use. Model names change often, and some are closed to new users:

```powershell
python -m cookbook.extract --list-models
```

Then transcribe:

```powershell
python -m cookbook.extract Recipe.pdf --pages 1,2 --model gemini-3.1-flash-lite
```

Options:
- `--model NAME` picks the model. Without it the code uses `GEMINI_MODEL` from `.env`, then a built-in alias that may not exist for your key.
- `--pages 1,2` limits the run to those pages (1-based).
- `--out DIR` sets the output folder. The default is `data/experiments/`, which is gitignored.

For each page it writes two files into the output folder:
- `<pdf name>_<model>_p<N>.txt`: the transcript, one line per written line, each prefixed with an id like `p1:L3`.
- `<pdf name>_<model>_p<N>.usage.json`: input, output and thinking tokens, seconds, and whether the answer came from the cache.

Things to know:
- **Sending data:** this uploads images of your pages to Google. Free-tier prompts may be used for training in some regions, so do not send documents you must keep private.
- **Cache:** answers are cached in `.cache/llm/`, keyed on provider, model, prompt and image. Running the same page again costs nothing. Delete the folder to force new calls.
- **Errors:** a 503 "high demand" error is usually temporary, so run the command again. There is no automatic retry or fallback model yet.

You can also call it from Python:

```python
from cookbook.llm import GeminiClient
from cookbook.extract import transcribe_pdf

pages = transcribe_pdf("Recipe.pdf", GeminiClient(model="gemini-3.1-flash-lite"))
print(pages[0].text)
```

## Tests

```powershell
pytest
```

All tests run offline: the PDFs are generated on the fly and the model is replaced by a fake, so no API key is needed. Run one file with `pytest tests/test_ingest_pdf.py`.

## Comparing models

The scripts in [evals/](evals/README.md) build degraded copies of the scan and score each model's transcript against a reference. See that file for the steps.

## Debugging in VS Code

Open the folder in VS Code and pick the `.venv` interpreter (Ctrl+Shift+P, "Python: Select Interpreter"). The Run and Debug panel then offers `Ingest: Recipe.pdf`, `Ingest: choose a PDF`, `Pytest: current file` and `Python: current file`.

## Project layout

```
src/cookbook/
  ingest/        decide how to read each page (pdf.py, CLI in __main__.py)
  extract/       transcribe pages with a vision model (transcribe.py, CLI in __main__.py)
  llm.py         the only module that talks to a model provider (Gemini client, cache)
tests/           offline tests
evals/           scripts for comparing models on degraded scans
ingest.md        how the ingest stage works, step by step
extract.md       plan and decisions for the extract stage
.env.example     template for the API key
```
