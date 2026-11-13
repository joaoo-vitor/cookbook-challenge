# Ingest stage: build guide

This document explains how to rebuild the PDF ingest stage from scratch, step by step. Follow the steps in order. After each step there is a check you can run.

## 1. What the stage does

Input: a path to a PDF. Output: one `PageResult` per page.

For each page the code:
1. extracts the text layer,
2. measures **signals** (cheap facts about the page),
3. maps the signals to a **strategy** that says how the page should be read,
4. builds a list of **extra tasks** (images on a text page that may contain text).

It never calls an LLM. It only decides what to do. Later stages do the reading.

### Strategies

| Strategy | Meaning | Next stage should |
|---|---|---|
| `TEXT_DIGITAL` | real typed text, no OCR signs | use the text |
| `TEXT_OCR` | OCR layer over a scan, quality is acceptable | use the text, mark it as machine-read |
| `VISION` | no text, or a text layer of poor quality | render the page to PNG and send it to a vision model |
| `EMPTY` | no text, no meaningful image | skip |

### Extra tasks

The task list holds only the **extras beyond the page strategy**: one entry per image on a `TEXT_DIGITAL` page that may hold text of its own (for example a handwritten note on a typed page). For almost every page the list is empty.

## 2. Environment

- Python 3.10 or newer. Windows works.
- One runtime dependency: **PyMuPDF** (`import pymupdf`). One dev dependency: **pytest**.

Create the project files:

`pyproject.toml`
```toml
[project]
name = "cookbook"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["pymupdf>=1.24"]

[project.optional-dependencies]
dev = ["pytest"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

`.gitignore` (the real recipe must never be committed)
```
.env
Recipe.pdf
data/
.cache/
__pycache__/
*.egg-info/
.venv/
.pytest_cache/
```

Set up and activate a virtual environment (PowerShell), then install:
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Layout to create:
```
src/cookbook/__init__.py            (empty file)
src/cookbook/ingest/__init__.py     (exports, step 8)
src/cookbook/ingest/pdf.py          (all logic, steps 3-7)
src/cookbook/ingest/__main__.py     (CLI, step 9)
tests/test_ingest_pdf.py            (step 10)
```

Check: `python -c "import pymupdf; print(pymupdf.__version__)"` prints a version.

## 3. Constants and data types (top of `pdf.py`)

Start the file with `from __future__ import annotations`. Imports: `re`, `dataclass`/`field`/`asdict` from `dataclasses`, `Enum`, `Path`, and `import pymupdf` (use `pymupdf`, not the deprecated `fitz`).

All thresholds live together at the top:

| Constant | Value | Meaning |
|---|---|---|
| `MIN_CHARS` | 20 | fewer extracted characters than this means "no text layer" |
| `BIG_IMAGE_COVERAGE` | 0.80 | one image covering at least 80% of the page means a scan |
| `MIN_QUALITY` | 0.60 | below this the text layer is not trusted |
| `MIN_IMAGE_COVERAGE` | 0.02 | images under 2% of the page are ignored |

Lists (compare in lowercase):
- `OCR_TOOLS = ("ocr", "tesseract", "abbyy", "finereader", "adobe scan", "ocrmypdf", "paperport", "omnipage", "readiris", "camscanner", "genius scan")`
- `OCR_FONT_HINTS = ("glyphless", "ocr", "tesseract")`

Types:
- `Strategy(str, Enum)` with members `TEXT_DIGITAL="text_digital"`, `TEXT_OCR="text_ocr"`, `VISION="vision"`, `EMPTY="empty"`.
- `BBox = tuple[float, float, float, float]` (x0, y0, x1, y1, in PDF points).
- `Signals` dataclass: `n_chars: int`, `image_coverage: float`, `has_big_image: bool`, `has_invisible_text: bool`, `ocr_producer: bool`, `ocr_font: bool`, `text_quality: float | None`.
- `TextBlock` dataclass: `bbox: BBox`, `text: str`.
- `ExtractionTask` dataclass: `bbox: BBox`, `reasons: list[str]`.
- `PageResult` dataclass: `page: int` (1-based), `strategy: Strategy`, `reasons: list[str]`, `signals: Signals`, `text: str = ""`, `tasks: list[ExtractionTask]` (default empty), `blocks: list[TextBlock]` (default empty). Add a `to_dict()` that calls `asdict(self)` and replaces `d["strategy"]` with `self.strategy.value`, so the result can be dumped as JSON.

## 4. Signals (functions in `pdf.py`)

**`image_regions(page) -> list[(bbox, coverage)]`**
- `page_area = page.rect.width * page.rect.height`; return `[]` if it is 0.
- For each `info` in `page.get_image_info()`: `rect = pymupdf.Rect(info["bbox"]) & page.rect` (clip to the page). Skip if `rect.is_empty`.
- `bbox = tuple(round(v, 1) for v in rect)`. Skip duplicates (same image drawn twice) with a `seen` set.
- `coverage = min(rect.width * rect.height / page_area, 1.0)`.

**`image_coverage(page)`**: the largest coverage from `image_regions`, or `0.0` if none.

**`has_invisible_text(page)`**: `any(span.get("type") == 3 for span in page.get_texttrace())`. In PyMuPDF's text trace, type 3 means "ignored", which is PDF render mode 3 (invisible). OCR tools use it for their hidden layer.

**`uses_ocr_font(page)`**: join `f"{f[3]} {f[4]}"` for each row of `page.get_fonts()`, lowercase, and check whether any `OCR_FONT_HINTS` entry appears in it.

**`producer_is_ocr(doc)`**: join `doc.metadata["producer"]` and `["creator"]` (use `or ""` for missing values), lowercase, and check for any `OCR_TOOLS` entry.

**`text_quality(text) -> float | None`**: a language-independent score of "does this look like real words?" with no dictionary.
```python
chars = [c for c in text if not c.isspace()]
if len(chars) < MIN_CHARS:
    return None
ok = sum(c.isalnum() or c in ".,;:!?()-–'\"%/°&" for c in chars) / len(chars)
tokens = text.split()
words = [re.sub(r"^\W+|\W+$", "", t) for t in tokens]
words = [w for w in words if w]
if not words:
    return 0.0
wordlike = re.compile(r"^(?:[^\W\d_]+(?:['’-][^\W\d_]+)*|[\d.,/½¼¾⅓⅔]+)$")
good = sum(bool(wordlike.match(w)) for w in words) / len(words)
singles = sum(len(w) == 1 for w in words) / len(words)
mixed = sum(bool(re.search(r"[^\W\d_]", w) and re.search(r"\d", w)) for w in words) / len(words)
score = 0.4 * ok + 0.6 * good - 0.5 * max(0.0, singles - 0.10) - 0.8 * mixed
return round(max(0.0, min(1.0, score)), 3)
```
Meaning: `ok` = share of normal characters, `good` = share of tokens that are a word or a number, `singles` = share of one-character tokens (an OCR failure sign), `mixed` = share of tokens mixing letters and digits (like "Zwi3beln").

**`collect_signals(doc, page, text) -> Signals`**: `cov = image_coverage(page)`; build `Signals` with `n_chars=len(text.strip())`, `image_coverage=round(cov, 3)`, `has_big_image=cov >= BIG_IMAGE_COVERAGE`, and the other fields from the functions above.

## 5. The decision (`decide(s: Signals) -> (Strategy, list[str])`)

It is a **pure function**: no I/O, so it is easy to test. Apply the rules in this order. Every return includes a human-readable reason.

1. `has_text = s.n_chars >= MIN_CHARS`.
2. **No text:**
   - big image: `VISION` (a scan).
   - else if `s.image_coverage > 0.05`: `VISION`.
   - else: `EMPTY`.
3. **Text present.** Collect OCR clues: invisible text, big image, OCR producer, OCR font.
   `ocr_suspected = s.has_invisible_text or (s.has_big_image and len(clues) >= 2)`.
   **A big image alone is not proof of OCR**, since typed pages also have images. Invisible text is the strongest single clue.
4. `low_quality = s.text_quality is not None and s.text_quality < MIN_QUALITY`.
5. If `ocr_suspected`:
   - low quality: `VISION`. The layer is kept on the page for cross-checking.
   - otherwise: `TEXT_OCR`.
6. Not suspected:
   - low quality (garbled or broken encoding): `VISION`.
   - otherwise: `TEXT_DIGITAL`.

Decision table:

| Text? | OCR suspected? | Quality | Result |
|---|---|---|---|
| no | n/a (big image) | n/a | `VISION` |
| no | n/a (no image) | n/a | `EMPTY` |
| yes | yes | ok | `TEXT_OCR` |
| yes | yes | low | `VISION` |
| yes | no | ok | `TEXT_DIGITAL` |
| yes | no | low | `VISION` |

## 6. Extra tasks, merging and rendering

**`build_tasks(strategy, regions) -> list[ExtractionTask]`**
- If `strategy is not Strategy.TEXT_DIGITAL`: return `[]`. (`VISION` pages are read whole. `TEXT_OCR` pages' images are the scan itself. `EMPTY` has nothing.)
- For each `(bbox, cov)`: skip if `cov >= BIG_IMAGE_COVERAGE` (a background behind real text) or `cov < MIN_IMAGE_COVERAGE` (too small). Otherwise add `ExtractionTask(bbox, [reason])`, where the reason says the image may hold text: transcribe it, answer `NONE` if none.

**`merge_page_text(page: PageResult, image_texts: dict[int, str]) -> str`**
- `image_texts` maps a task index (into `page.tasks`) to the vision model's answer.
- Drop answers that are empty or equal to `"NONE"` (case-insensitive, after `strip()`). Keep the rest as `(task.bbox, f"[from image, page {page.page}]\n{text}")`.
- If nothing is kept, return `page.text` unchanged.
- Otherwise add every `TextBlock` as `(bbox, text)`, sort by `(bbox[1], bbox[0])` (top to bottom, then left to right) and join with `"\n\n"`.
- Known limit: this is wrong for multi-column layouts.

**`render_page(path, page_no, dpi=200) -> bytes`**: open the PDF and return `doc[page_no - 1].get_pixmap(dpi=dpi).tobytes("png")`. Used for `VISION` pages.

**`render_task(path, page_no, task, dpi=200) -> bytes`**: the same, with `clip=pymupdf.Rect(task.bbox)`. Rendering what is displayed (rather than extracting the embedded image) keeps rotation and cropping.

## 7. Public entry point

```python
def analyze_pdf(path) -> list[PageResult]:
    path = Path(path)
    with pymupdf.open(path) as doc:
        if doc.needs_pass:
            raise ValueError(f"{path} is password protected")
        results = []
        for i, page in enumerate(doc, start=1):
            text = page.get_text(sort=True)
            signals = collect_signals(doc, page, text)
            strategy, reasons = decide(signals)
            tasks = build_tasks(strategy, image_regions(page))
            blocks = [
                TextBlock(tuple(round(v, 1) for v in b[:4]), b[4].strip())
                for b in page.get_text("blocks", sort=True)
                if b[6] == 0 and b[4].strip()   # block type 0 = text
            ]
            results.append(PageResult(i, strategy, reasons, signals, text, tasks, blocks))
        return results
```
`page.get_text("blocks")` returns tuples `(x0, y0, x1, y1, text, block_no, block_type)`.

## 8. Exports (`src/cookbook/ingest/__init__.py`)

Re-export `ExtractionTask`, `PageResult`, `Signals`, `Strategy`, `TextBlock`, `analyze_pdf`, `decide`, `merge_page_text`, `render_page`, `render_task`, `text_quality` from `.pdf`, and list them in `__all__`.

## 9. CLI (`src/cookbook/ingest/__main__.py`)

Run with `python -m cookbook.ingest file.pdf [--json] [--show-text]`.
- Parse the arguments with `argparse`.
- On any exception from `analyze_pdf`, print `error: ...` to stderr and return exit code 1.
- `--json`: print `json.dumps([p.to_dict() for p in pages], ensure_ascii=False, indent=2)`.
- Default output, per page: a line `page N: STRATEGY`, a `signals:` line (chars, image coverage, invisible text, OCR producer, OCR font, quality, with `n/a` for `None`), one `- reason` line per reason, and one `extra task [i]: image (bbox) -> vision` line per task.
- `--show-text` also prints the extracted text, indented.

## 10. Tests (`tests/test_ingest_pdf.py`)

The tests **generate their PDFs in memory** with PyMuPDF and save them into pytest's `tmp_path`. No real document is committed.

Helpers (test setup, often called fixtures):
- `_text_page(doc, text)`: new page, `page.insert_text((50, 100), text, fontsize=11)`.
- `_scan_png()`: draw text on a temporary doc, `get_pixmap(dpi=100).tobytes("png")`. This stands in for a photo.
- `_scan_page(doc, hidden_text=None)`: new page with `page.insert_image(page.rect, stream=png)`. If `hidden_text` is given, add `page.insert_text(..., render_mode=3)`, which is an invisible OCR-style layer.
- `_mixed_page(doc, image_rect)`: text at y=100, an image in the middle, text at y=700. **Pass `keep_proportion=False` to `insert_image`**, otherwise PyMuPDF shrinks the image to keep its aspect ratio and the bounding box will not match the rectangle you passed.
- `_analyze(doc, tmp_path)`: save and call `analyze_pdf`.

Use a good sentence (`GOOD`) for clean text and a string full of symbols, lone letters and digit-letter mixes (`GARBAGE`) for bad OCR.

Tests to write (28 cases in total, counting parametrized ones):
1. Typed page gives `TEXT_DIGITAL` and the text is extracted.
2. Scan gives `VISION` (0 characters, big image).
3. Scan with a good invisible layer gives `TEXT_OCR`.
4. Scan with a garbled invisible layer gives `VISION` and keeps the layer text.
5. Typed page with a small image is still `TEXT_DIGITAL`.
6. Blank page gives `EMPTY`.
7. A two-page document (typed, then scan) gets `[TEXT_DIGITAL, VISION]`, with page numbers `[1, 2]`.
8. Metadata with `producer="OCRmyPDF 16 / Tesseract"` sets `ocr_producer`.
9. Typed page, scan and blank page all have `tasks == []`.
10. Mixed page: strategy is still `TEXT_DIGITAL` and there is one task whose bbox is `(50.0, 300.0, 350.0, 450.0)`.
11. A tiny image (about 0.1% of the page) gives no task.
12. A page-sized image behind visible text gives no task and stays `TEXT_DIGITAL`.
13. A `TEXT_OCR` page has no tasks.
14. `merge_page_text` with `{0: "Rosinen 100 g"}` puts that text between the top and bottom paragraphs and includes `[from image, page 1]`.
15. `merge_page_text` ignores answers `"NONE"`, `"none"`, `"  "`, `""` and returns `page.text` (parametrized, 4 cases).
16. `render_task` returns PNG bytes (starts with `b"\x89PNG\r\n\x1a\n"`) of size 300x150 at 72 dpi.
17. `render_page` returns a PNG of 595x842 at 72 dpi (A4).
18. `text_quality(GOOD) > 0.8`, `text_quality(GARBAGE) < 0.5`, `text_quality("kurz") is None`.
19. A table of `Signals` combinations for `decide` (parametrized, 7 cases) including: "big image alone stays `TEXT_DIGITAL`", "big image plus OCR producer gives `TEXT_OCR`", "invisible text plus low quality gives `VISION`", "low quality alone gives `VISION`".

## 11. Acceptance checks

```powershell
pytest
```
Expected: `28 passed`.

```powershell
python -m cookbook.ingest Recipe.pdf
```
Expected for the real recipe (two scanned pages, no text layer, one image covering 100% of each page):
```
page 1: VISION
  signals: chars=0 image=100% invisible_text=False ocr_producer=False ocr_font=False quality=n/a
  - no text layer (0 chars) and one image covers 100% of the page: a scan
page 2: VISION
  ...same...
```

## 12. Pitfalls we hit

- **Image aspect ratio in tests:** `insert_image` keeps proportions by default, so the drawn image was smaller than the rectangle and two tests failed. Fix: `keep_proportion=False`.
- **Package not found:** `python -m cookbook.ingest` fails with `ModuleNotFoundError` until you run `pip install -e ".[dev]"`. (Tests work anyway because `pyproject.toml` sets `pythonpath = ["src"]`.)
- **Windows shell:** `PYTHONPATH=src python ...` only works in Git Bash. In PowerShell use `$env:PYTHONPATH = "src"`, or install the project instead.
- **Thresholds are guesses:** the quality threshold (0.60) is validated only on synthetic OCR layers. Try it on a real OCR'd PDF before trusting it.
- **Do not count a big image as OCR proof:** typed pages with background pictures would be misrouted.

## 13. Not done yet

- Images and plain-text input (the assignment requires them).
- The vision model call that transcribes `VISION` pages and image tasks.
- Detecting a partial OCR layer, where the layer covers only part of the page.
- Multi-column reading order in `merge_page_text`.
