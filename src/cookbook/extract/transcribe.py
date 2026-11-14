"""Stage 2a: get the text of every page, reading scans with a vision model.

Digital pages use their text layer and never call the model. Pages the ingest
stage labels VISION are rendered to a JPEG and transcribed. Images on a typed page
(ingest's extra tasks) are transcribed and merged back by position.

With a second reader, every image is read by two different models at the same
time. The first model's reading is the transcript; `compare` flags where the
second reading differs.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from cookbook.extract.compare import MIN_AGREEMENT, Comparison, compare_reads
from cookbook.ingest.pdf import (
    Strategy, analyze_pdf, merge_page_text, render_page, render_task,
)
from cookbook.llm import LLMResult, VisionLLM

PROMPT = """You are transcribing a photographed page from an old handwritten German cookbook.

Rules:
- Copy the text exactly as written, one output line per written line.
- If the text is in columns, write one column completely, from top to bottom, before the next column. Never put text from two columns on the same output line, and keep the lines of an item that wraps inside its column together and in order. Text that runs across the full width below or above the columns goes before or after them, in reading order.
- Keep the old spelling, punctuation and line-end hyphens. Do not correct, modernise or translate anything.
- Copy numbers and fractions exactly as shown (for example 1/2, 4). Never guess a number: if you cannot read it, write [?].
- If a word is hard to read, write your best reading inside [? ?], for example [?Pfeffer?]. If you cannot read it at all, write [?].
- Text written inside a drawing (a label on a jar or a box) is included, on its own line starting with "[drawing] ".
- Do not describe pictures, add comments or summarise.
Output only the transcription."""


@dataclass
class PageTranscript:
    page: int
    strategy: Strategy
    source: str  # "text_layer", "vision" or "none"
    text: str  # the primary reading
    calls: list[LLMResult] = field(default_factory=list)  # primary model calls for this page
    second_text: str | None = None  # the second reading of a vision page, if there was one
    second_calls: list[LLMResult] = field(default_factory=list)
    comparisons: list[Comparison] = field(default_factory=list)  # one per image that two models read
    warnings: list[str] = field(default_factory=list)  # for example a second read that failed

    @property
    def unreliable(self) -> bool:
        return any(c.unreliable for c in self.comparisons)


def _read(image: bytes, llm: VisionLLM, second: VisionLLM | None):
    """Read one image with the primary model and, if given, the second one, in parallel.

    Returns (primary result, second result or None, warnings). A failure of the
    primary model raises. A failure of the second only costs the comparison. If a
    reader had to fall back to another model that is said in the warnings, and if
    both ended up on the same model the comparison is dropped, because two readings
    from one model are not independent.
    """
    if second is None:
        primary = llm.transcribe(image, PROMPT)
        return primary, None, _fallback_notes(primary, None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, other = pool.submit(llm.transcribe, image, PROMPT), pool.submit(second.transcribe, image, PROMPT)
        primary = first.result()
        try:
            secondary = other.result()
        except Exception as e:  # noqa: BLE001 - any provider error: keep the primary reading
            warnings = _fallback_notes(primary, None)
            return primary, None, warnings + [f"second read failed ({type(e).__name__}): {str(e)[:200]}"]
    warnings = _fallback_notes(primary, secondary)
    if primary.model == secondary.model:
        warnings.append(f"both readings came from {primary.model}, so they are not independent: comparison skipped")
        return primary, None, warnings
    return primary, secondary, warnings


def _fallback_notes(primary: LLMResult, secondary: LLMResult | None) -> list[str]:
    return [f"{who} reader fell back from {res.fell_back_from} to {res.model}"
            for who, res in (("primary", primary), ("second", secondary)) if res and res.fell_back_from]


def transcribe_pdf(path: str | Path, llm: VisionLLM, pages: set[int] | None = None,
                   second: VisionLLM | None = None,
                   min_agreement: float = MIN_AGREEMENT) -> list[PageTranscript]:
    """Return the text of each page (optionally only the given 1-based pages).

    With `second`, images are read by both models and each pair is compared.
    """
    if second is not None and (getattr(second, "name", None), getattr(second, "model", None)) == (
            getattr(llm, "name", None), getattr(llm, "model", None)):
        raise ValueError("the second reader must be a different model, or it would only repeat the first")

    out = []
    for r in analyze_pdf(path):
        if pages and r.page not in pages:
            continue
        if r.strategy is Strategy.VISION:
            first, other, warnings = _read(render_page(path, r.page), llm, second)
            t = PageTranscript(r.page, r.strategy, "vision", first.text.strip(), [first])
            if other:
                t.second_text, t.second_calls = other.text.strip(), [other]
                t.comparisons.append(compare_reads(r.page, first.text, other.text, "page",
                                                   min_agreement=min_agreement))
            t.warnings.extend(warnings)
            out.append(t)
        elif r.strategy is Strategy.EMPTY:
            out.append(PageTranscript(r.page, r.strategy, "none", ""))
        else:  # TEXT_DIGITAL or TEXT_OCR: the text layer is the text, only images are read
            t = PageTranscript(r.page, r.strategy, "text_layer", "")
            image_texts = {}
            for i, task in enumerate(r.tasks):
                first, other, warnings = _read(render_task(path, r.page, task), llm, second)
                t.calls.append(first)
                image_texts[i] = first.text
                if other:
                    t.second_calls.append(other)
                    t.comparisons.append(compare_reads(r.page, first.text, other.text, f"image {i}",
                                                       id_prefix=f"p{r.page}:image{i}",
                                                       min_agreement=min_agreement))
                t.warnings.extend(f"image {i}: {w}" for w in warnings)
            t.text = merge_page_text(r, image_texts).strip()
            out.append(t)
    return out


def number_lines(page: int, text: str) -> list[str]:
    """Prefix each non-empty line with an id like `p1:L3`, for later citation."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return [f"p{page}:L{i}\t{ln}" for i, ln in enumerate(lines, start=1)]
