"""PDF ingest: decide, per page, how the page should be read.

For every page we collect *signals* (cheap, deterministic facts about the page),
then map them to a *strategy*:

    TEXT_DIGITAL   born-digital text layer, trust it
    TEXT_OCR       an OCR layer over a scan that passes the quality check:
                   usable, but flagged as machine-guessed
    VISION         no usable text (pure scan, or OCR layer of poor quality):
                   render the page and send the image to a vision model
    EMPTY          nothing to read (no text, no meaningful image)

The page strategy says how to read the page itself. Each page also carries a
list of *extra tasks*: images on a text page that may hold text of their own
(a handwritten note on a typed page). The list is empty for most pages, e.g. a
typed page, a plain scan or a blank page.

Nothing here calls an LLM. It only decides *what* to do with each page, and
provides the helpers (render a region, merge results back by position).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

import pymupdf

# --- thresholds (tune on real documents; kept in one place on purpose) -------
MIN_CHARS = 20  # fewer extracted chars than this = "no text layer"
BIG_IMAGE_COVERAGE = 0.80  # one image covering >= 80% of the page = a scan
MIN_QUALITY = 0.60  # below this the text layer is not trusted
MIN_IMAGE_COVERAGE = 0.02  # images smaller than 2% of the page are ignored (icons, rules)

# Rendering for a vision model: enough pixels to read, never more than the source has.
MAX_DPI = 200  # upper bound for pages with no photo underneath (typed pages)
MAX_SIDE_PX = 2400  # longest side of an image sent to a model
JPEG_QUALITY = 90

OCR_TOOLS = (
    "ocr", "tesseract", "abbyy", "finereader", "adobe scan", "ocrmypdf",
    "paperport", "omnipage", "readiris", "camscanner", "genius scan",
)
# Fonts that OCR engines embed for their hidden text layer.
OCR_FONT_HINTS = ("glyphless", "ocr", "tesseract")


class Strategy(str, Enum):
    TEXT_DIGITAL = "text_digital"
    TEXT_OCR = "text_ocr"
    VISION = "vision"
    EMPTY = "empty"


@dataclass
class Signals:
    n_chars: int
    image_coverage: float  # largest single image / page area, 0..1
    has_big_image: bool
    has_invisible_text: bool  # text render mode 3 (typical of OCR layers)
    ocr_producer: bool  # document metadata names an OCR/scanner tool
    ocr_font: bool  # page uses a font typical of OCR text layers
    text_quality: float | None  # 0..1, None when there is no text to judge


BBox = tuple[float, float, float, float]  # x0, y0, x1, y1 in page points


@dataclass
class TextBlock:
    bbox: BBox
    text: str


@dataclass
class ExtractionTask:
    """An image on a text page that should be sent to a vision model."""

    bbox: BBox
    reasons: list[str]


@dataclass
class PageResult:
    page: int  # 1-based
    strategy: Strategy  # page-level decision
    reasons: list[str]
    signals: Signals
    # Text is kept for TEXT_* strategies. For VISION it is whatever the (poor
    # or missing) layer contained; do not trust it, it is kept for cross-checks.
    text: str = field(repr=False, default="")
    tasks: list[ExtractionTask] = field(default_factory=list)  # extras beyond `strategy`
    blocks: list[TextBlock] = field(repr=False, default_factory=list)  # text with positions
    kind: str = "pdf"  # what the input file was: "pdf", "image" or "text"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["strategy"] = self.strategy.value
        return d


# --- signals -----------------------------------------------------------------
def image_regions(page: pymupdf.Page) -> list[tuple[BBox, float]]:
    """Every distinct image on the page as (bbox, share of the page area)."""
    page_area = page.rect.width * page.rect.height
    if page_area <= 0:
        return []
    out, seen = [], set()
    for info in page.get_image_info():
        rect = pymupdf.Rect(info["bbox"]) & page.rect  # clip to the page
        if rect.is_empty:
            continue
        bbox = tuple(round(v, 1) for v in rect)
        if bbox in seen:  # same image drawn twice
            continue
        seen.add(bbox)
        out.append((bbox, min(rect.width * rect.height / page_area, 1.0)))
    return out


def image_coverage(page: pymupdf.Page) -> float:
    """Area of the largest image on the page, as a fraction of the page."""
    return max((cov for _, cov in image_regions(page)), default=0.0)


def has_invisible_text(page: pymupdf.Page) -> bool:
    """True if any text is drawn with render mode 3 (invisible)."""
    # In PyMuPDF's text trace, span "type" 3 means "ignored" = invisible.
    return any(span.get("type") == 3 for span in page.get_texttrace())


def uses_ocr_font(page: pymupdf.Page) -> bool:
    # get_fonts() rows: (xref, ext, type, basefont, name, encoding)
    names = " ".join(f"{f[3]} {f[4]}" for f in page.get_fonts()).lower()
    return any(hint in names for hint in OCR_FONT_HINTS)


def producer_is_ocr(doc: pymupdf.Document) -> bool:
    meta = " ".join(str(doc.metadata.get(k) or "") for k in ("producer", "creator")).lower()
    return any(tool in meta for tool in OCR_TOOLS)


def text_quality(text: str) -> float | None:
    """Heuristic 0..1 score for 'does this look like real words?'.

    No dictionary (language-agnostic, any recipe language). We look at the
    shape of the tokens instead; OCR garbage has a recognisable shape:
    symbols, digits glued into words ("Zwi3beln"), strings of 1-char tokens.
    """
    chars = [c for c in text if not c.isspace()]
    if len(chars) < MIN_CHARS:
        return None

    # 1. Share of characters that are letters, digits or normal punctuation.
    ok = sum(c.isalnum() or c in ".,;:!?()-–'\"%/°&" for c in chars) / len(chars)

    tokens = text.split()
    words = [re.sub(r"^\W+|\W+$", "", t) for t in tokens]
    words = [w for w in words if w]
    if not words:
        return 0.0

    # 2. Share of tokens that look like a word or a number: letters only
    #    (hyphen/apostrophe allowed) or digits only (with , . / for 1/2, 1,5).
    wordlike = re.compile(r"^(?:[^\W\d_]+(?:['’-][^\W\d_]+)*|[\d.,/½¼¾⅓⅔]+)$")
    good = sum(bool(wordlike.match(w)) for w in words) / len(words)

    # 3. Penalise runs of single-character tokens ("l | ; t i e"), a classic
    #    OCR failure. Real 1-char tokens (a, 1, &) are rare.
    singles = sum(len(w) == 1 for w in words) / len(words)

    # 4. Letters and digits glued in one token ("Zwi3beln", "l0g").
    mixed = sum(bool(re.search(r"[^\W\d_]", w) and re.search(r"\d", w)) for w in words) / len(words)

    score = 0.4 * ok + 0.6 * good - 0.5 * max(0.0, singles - 0.10) - 0.8 * mixed
    return round(max(0.0, min(1.0, score)), 3)


def collect_signals(doc: pymupdf.Document, page: pymupdf.Page, text: str) -> Signals:
    cov = image_coverage(page)
    return Signals(
        n_chars=len(text.strip()),
        image_coverage=round(cov, 3),
        has_big_image=cov >= BIG_IMAGE_COVERAGE,
        has_invisible_text=has_invisible_text(page),
        ocr_producer=producer_is_ocr(doc),
        ocr_font=uses_ocr_font(page),
        text_quality=text_quality(text),
    )


# --- decision ----------------------------------------------------------------
def decide(s: Signals) -> tuple[Strategy, list[str]]:
    """Pure function: signals in, (strategy, reasons) out. Easy to unit test."""
    reasons: list[str] = []
    has_text = s.n_chars >= MIN_CHARS

    if not has_text:
        if s.has_big_image:
            reasons.append(f"no text layer ({s.n_chars} chars) and one image covers "
                           f"{s.image_coverage:.0%} of the page: a scan")
            return Strategy.VISION, reasons
        if s.image_coverage > 0.05:
            reasons.append("no text layer, but a smaller image is present: "
                           "may carry content, read with vision")
            return Strategy.VISION, reasons
        reasons.append("no text and no meaningful image")
        return Strategy.EMPTY, reasons

    # Count independent OCR clues. A big image alone is NOT proof (digital pages
    # have images too); invisible text is the strongest single clue.
    clues = []
    if s.has_invisible_text:
        clues.append("invisible text (render mode 3)")
    if s.has_big_image:
        clues.append(f"full-page image ({s.image_coverage:.0%})")
    if s.ocr_producer:
        clues.append("OCR/scanner tool in metadata")
    if s.ocr_font:
        clues.append("OCR-style font")
    ocr_suspected = s.has_invisible_text or (s.has_big_image and len(clues) >= 2)

    low_quality = s.text_quality is not None and s.text_quality < MIN_QUALITY

    if ocr_suspected:
        reasons.append("OCR layer suspected: " + ", ".join(clues))
        if low_quality:
            reasons.append(f"text quality {s.text_quality:.2f} < {MIN_QUALITY}: "
                           "layer not trusted, use vision (keep layer for cross-check)")
            return Strategy.VISION, reasons
        reasons.append(f"text quality {s.text_quality:.2f} is acceptable: use the layer, "
                       "but it is machine-read, so treat it as less certain")
        return Strategy.TEXT_OCR, reasons

    if low_quality:
        reasons.append(f"text layer present but quality {s.text_quality:.2f} < {MIN_QUALITY} "
                       "(garbled or broken encoding): use vision")
        return Strategy.VISION, reasons

    reasons.append(f"text layer with {s.n_chars} chars, quality {s.text_quality:.2f}, "
                   "no OCR indicators: born-digital")
    return Strategy.TEXT_DIGITAL, reasons


def build_tasks(strategy: Strategy, regions: list[tuple[BBox, float]]) -> list[ExtractionTask]:
    """Extra work beyond the page strategy: images worth sending to vision.

    - Only TEXT_DIGITAL pages get tasks.
    - VISION pages are read whole, so cutting out regions would read the same
      pixels twice.
    - TEXT_OCR pages: their images are the scan the layer was made from.
    - EMPTY pages have nothing.
    """
    if strategy is not Strategy.TEXT_DIGITAL:
        return []

    tasks = []
    for bbox, cov in regions:
        if cov >= BIG_IMAGE_COVERAGE:  # page-sized image behind real text: background
            continue
        if cov < MIN_IMAGE_COVERAGE:  # too small to carry readable text
            continue
        tasks.append(ExtractionTask(
            bbox,
            [f"image covers {cov:.0%} of a page that has a text layer: may hold text "
             "(transcribe it, answer NONE if there is none)"]))
    return tasks


IMAGE_MARKER = re.compile(r"\[from image, page \d+\]")  # the line merge_page_text puts before an image's text


def merge_page_text(page: PageResult, image_texts: dict[int, str]) -> str:
    """Put the text read from images back into the page text, by position.

    `image_texts` maps a task index (into `page.tasks`) to what the vision model
    returned for that image. Empty strings and "NONE" are dropped. Text blocks
    and image results are ordered top-to-bottom, then left-to-right. That is
    wrong for multi-column layouts (a known limit).
    """
    items: list[tuple[BBox, str]] = []
    for idx, raw in image_texts.items():
        task = page.tasks[idx]
        text = raw.strip()
        if text and text.upper() != "NONE":
            items.append((task.bbox, f"[from image, page {page.page}]\n{text}"))
    if not items:
        return page.text
    items += [(b.bbox, b.text) for b in page.blocks]
    items.sort(key=lambda it: (it[0][1], it[0][0]))  # y0, then x0
    return "\n\n".join(t for _, t in items)


def _render_scale(page: pymupdf.Page, area: pymupdf.Rect) -> float:
    """Pixels per PDF point to render `area` at.

    A fixed dpi is wrong for photo PDFs: a phone's "save as PDF" makes the page
    as many points as the photo has pixels, so 200 dpi would stretch it almost
    3x and add pixels but no detail (and a 40 MB upload). So the scale is the
    smallest of:
      - MAX_DPI (typed pages have no photo to be faithful to),
      - the scale that makes the longest side MAX_SIDE_PX,
      - the resolution of the photo that covers the area, if there is one.
    """
    scale = min(MAX_DPI / 72, MAX_SIDE_PX / max(area.width, area.height))
    for info in page.get_image_info():
        box = pymupdf.Rect(info["bbox"])
        covered = box & area
        if not covered.is_empty and covered.width * covered.height >= 0.8 * area.width * area.height:
            scale = min(scale, info["width"] / box.width)  # photo pixels per point
            break
    return scale


def _encode(pix: pymupdf.Pixmap, fmt: str) -> bytes:
    if fmt == "jpeg":  # about 5x smaller than PNG for a photo
        return pix.tobytes("jpeg", jpg_quality=JPEG_QUALITY)
    if fmt == "png":
        return pix.tobytes("png")
    raise ValueError(f"unknown image format {fmt!r}")


def render_page(path: str | Path, page_no: int, fmt: str = "jpeg") -> bytes:
    """Image bytes (JPEG or PNG) of a whole page, for pages whose strategy is VISION.

    Rendering what is *displayed* (rather than pulling out the embedded image)
    keeps rotation and cropping as the reader sees them.
    """
    with pymupdf.open(path) as doc:
        page = doc[page_no - 1]
        scale = _render_scale(page, page.rect)
        return _encode(page.get_pixmap(matrix=pymupdf.Matrix(scale, scale)), fmt)


def render_task(path: str | Path, page_no: int, task: ExtractionTask, fmt: str = "jpeg") -> bytes:
    """Image bytes (JPEG or PNG) of one image region (an extra task on a text page)."""
    with pymupdf.open(path) as doc:
        page = doc[page_no - 1]
        clip = pymupdf.Rect(task.bbox)
        scale = _render_scale(page, clip)
        return _encode(page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip), fmt)


# --- public API --------------------------------------------------------------
def analyze_pdf(path: str | Path) -> list[PageResult]:
    """Analyse every page of a PDF and return one PageResult per page."""
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
                if b[6] == 0 and b[4].strip()  # type 0 = text block
            ]
            results.append(PageResult(i, strategy, reasons, signals, text, tasks, blocks))
        return results


# --- any input: PDF, image or plain text ---------------------------------------
_IMAGE_SIGNATURES = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"II*\x00", b"MM\x00*")
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")


def detect_kind(path: str | Path) -> str:
    """What came in, judged by the first bytes of the file and not by its name: "pdf", "image" or "text".

    A file that is none of these (it holds binary data) is an error.
    """
    with open(path, "rb") as f:
        head = f.read(2048)
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(_IMAGE_SIGNATURES) or (head[:4] == b"RIFF" and head[8:12] == b"WEBP"):
        return "image"
    if b"\x00" in head and not head.startswith(_UTF16_BOMS):
        raise ValueError(f"{path} is not a PDF, an image or a text file")
    return "text"


def _read_text(path: str | Path) -> str:
    """Decode a text file: UTF-8 (with or without a BOM), UTF-16 with a BOM, else Windows-1252."""
    raw = Path(path).read_bytes()
    if raw.startswith(_UTF16_BOMS):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def analyze_text(path: str | Path) -> list[PageResult]:
    """A plain text file is one page of text. It is used as it is: no reading, no wrapping.

    The lines of the file stay the lines of the text, so a line id points at the same
    line as in the file.
    """
    text = _read_text(path)
    quality = text_quality(text)
    signals = Signals(n_chars=len(text.strip()), image_coverage=0.0, has_big_image=False,
                      has_invisible_text=False, ocr_producer=False, ocr_font=False, text_quality=quality)
    if not text.strip():
        return [PageResult(1, Strategy.EMPTY, ["the text file is empty"], signals, "", kind="text")]
    reasons = ["plain text file: its lines are used as they are"]
    if quality is not None and quality < MIN_QUALITY:
        reasons.append(f"text quality {quality:.2f} is low: the file may not be plain text in this encoding")
    return [PageResult(1, Strategy.TEXT_DIGITAL, reasons, signals, text, kind="text")]


def analyze_document(path: str | Path) -> list[PageResult]:
    """Decide how to read each page of a PDF, an image or a plain text file."""
    kind = detect_kind(path)
    if kind == "text":
        return analyze_text(path)
    pages = analyze_pdf(path)  # PyMuPDF opens an image as a document of one page
    for page in pages:
        page.kind = kind
    return pages
