# Ingest: what came in, and how each page should be read

Ingest takes a file and decides, for every page, how its text should be obtained. It never calls a model: it only looks at the file and decides, so the next stage knows what to do. The code is in [src/cookbook/ingest/pdf.py](src/cookbook/ingest/pdf.py); `python -m cookbook.ingest file` prints the decision for each page.

## What it decides

**1. What kind of file this is.** `detect_kind` reads the first 2,048 bytes and ignores the name:

| First bytes | Kind | How it is read |
|---|---|---|
| `%PDF` | PDF | per page, see below |
| PNG, JPEG, GIF, TIFF or WebP signature | image | PyMuPDF opens an image as a one-page document: a big picture with no text, which the rules below send to a vision model |
| anything else without NUL bytes (or with a UTF-16 byte-order mark) | plain text | one page, used exactly as written, never sent to a model |
| NUL bytes and no UTF-16 mark | refused | `ValueError`: not a PDF, an image or a text file |

A text file is not run through the PDF library, because PyMuPDF re-wraps long lines to a page width and the line ids would no longer match the file. It is decoded as UTF-16 (with a mark), UTF-8 (a UTF-8 mark is dropped) or Windows-1252, and its lines are numbered over every line of the file, blank ones included, so `p1:L4` is line 4 of the file.

**2. For each PDF page, a strategy.** From a handful of cheap facts about the page ("signals"):

| Strategy | When | Next stage |
|---|---|---|
| `TEXT_DIGITAL` | a real typed text layer, no sign of OCR | use the text layer, no model call |
| `TEXT_OCR` | an OCR layer over a scan, and the text looks like words | use it, but it is machine-read, so less certain |
| `VISION` | no usable text: a pure scan, an OCR layer that is garbage, or a small image on an empty page | render the page, send the image to a vision model |
| `EMPTY` | no text and no meaningful image | skip |

The signals: how many characters the text layer has; how much of the page the largest image covers; whether any text is drawn invisibly (render mode 3, the usual way an OCR layer hides over its scan); whether the document metadata names an OCR or scanner tool (Tesseract, ABBYY, Adobe Scan, CamScanner, ...); whether a page font looks like an OCR font ("GlyphLessFont"); and a text-quality score.

The decision (`decide`, a pure function, so it is easy to test):
- Under 20 characters is "no text layer". With one image covering at least 80% of the page it is a scan (`VISION`); with a smaller image (over 5%) it may carry content (`VISION`); otherwise `EMPTY`.
- With text, OCR is suspected when there is invisible text, or when a full-page image comes with at least one more clue (an OCR tool in the metadata, an OCR font). **A big image alone is not proof**: typed pages often have a background picture. An OCR layer of quality 0.60 or better is `TEXT_OCR`; below that it is distrusted and the page goes to `VISION`.
- No OCR sign but a text layer of quality under 0.60 (garbled or broken encoding) goes to `VISION`. Otherwise `TEXT_DIGITAL`.

**The text-quality score** (0 to 1, no dictionary, so it works for any language) looks at the shape of the words: the share of letters, digits and normal punctuation; the share of tokens that look like a word or a number; a penalty for runs of one-letter tokens (a classic OCR failure, "l | ; t i e"); and a penalty for letters and digits glued together ("Zwi3beln"). The 0.60 threshold was set on synthetic OCR layers and not on a large real set.

**3. Extra tasks.** A `TEXT_DIGITAL` page can hold an image that carries text of its own (a handwritten note on a typed page). Each such image, between 2% and 80% of the page, becomes an extra task: it is cut out and read by a vision model, and the result is merged back among the text blocks by position (`merge_page_text`, with a `[from image, page N]` line before it). Images under 2% are icons and rules. A page-sized image behind real text is a background. `VISION` pages are read whole, so cutting out regions would read the same pixels twice; `TEXT_OCR` pages' images are the scan their layer came from. For most pages the list is empty.

## Rendering for the model

The image sent to a model is a JPEG (quality 90), never a PNG, and its scale is the smallest of: 200 dpi, the scale that makes the longest side 2,400 px, and the resolution of the photo underneath. This is not a detail. The first version rendered at a fixed 200 dpi, and for `Recipe.pdf` (a phone's "save as PDF": the page is as many points as the photo has pixels) that stretched each page almost three times into a 41 MB, 4728 x 6809 px PNG that added pixels and no detail, and a call took 15 to 75 seconds. A JPEG of about 1 MB gives the same transcript in 3 to 6 seconds.

## What the Sauerbraten shows

Both pages of `Recipe.pdf` are `VISION`: zero characters of text and one image covering 100% of the page. The drawings on the page (jars labelled "Rosinen", "Korinthen", "Rotwein") are part of that image, so they are read by the model and returned as `[drawing]` lines. A sentence that runs across the page break is not cut in ingest: pages stay separate here, and the extract stage joins their lines into one transcript with ids that run across the break (`p1:L23`, then `p2:L1`).

## Considered and rejected

- **Trusting any text layer.** A scanned PDF often has an OCR layer, and a bad one reads as plausible text. Trusting it would put a misread quantity into the recipe with no warning. The quality score and the OCR clues exist so a bad layer goes to a vision model.
- **Treating a big image as proof of OCR.** Typed pages with background pictures would be misrouted.
- **Rendering at a fixed 200 dpi.** See above: slow and no more accurate.
- **Running the PDF library on a text file.** The lines would be re-wrapped and the line ids would not match the file.
- **Cutting regions out of a `VISION` page.** It would read the same pixels twice.

## Known limits

- **Thresholds are guesses** (20 characters, 80% and 2% of the page, quality 0.60), checked on synthetic OCR layers, the scans and the typed pages we had, not on a large set.
- **A partial OCR layer** (covering only part of the page) is not detected.
- **A broken text layer that still looks like words is not caught.** A test PDF made with LibreOffice stored the accented letters in another font, so the text layer lost them ("m dio-alto", "ingl s" for "médio-alto", "inglês") and had stray lines holding only "é" or "á". Its quality score was 0.87, so the page was read as `TEXT_DIGITAL` and the transcript carries the broken words. A check for lone accented letters would catch it.
- **Multi-column typed PDFs.** The text extraction sorts by position, which on a two-column page can put a left item and a right item on one line. We saw it on a generated test PDF and have not tested a real typed two-column PDF.
- **Handwriting is only read by a model.** There is no OCR cross-check for it, because no dedicated OCR engine was tested on cursive German.

## Tests

`tests/test_ingest_pdf.py` builds its PDFs on the fly (a typed page, a scan, an OCR layer good and bad, a blank page, a typed page with an image) and checks the strategy, the extra tasks, the merge by position, and the rendering size. `tests/test_text_input.py` covers the kind detection, text files (encodings, blank lines, ids) and images.
