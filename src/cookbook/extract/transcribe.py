"""Stage 2a: get the text of every page, reading scans with a vision model.

Digital pages use their text layer and never call the model. Pages the ingest
stage labels VISION are rendered to a JPEG and transcribed. Images on a typed page
(ingest's extra tasks) are transcribed and merged back by position.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from cookbook.ingest.pdf import (
    Strategy, analyze_pdf, merge_page_text, render_page, render_task,
)
from cookbook.llm import LLMResult, VisionLLM

PROMPT = """You are transcribing a photographed page from an old handwritten German cookbook.

Rules:
- Copy the text exactly as written, one output line per written line.
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
    text: str
    calls: list[LLMResult] = field(default_factory=list)  # model calls made for this page


def transcribe_pdf(path: str | Path, llm: VisionLLM,
                   pages: set[int] | None = None) -> list[PageTranscript]:
    """Return the text of each page (optionally only the given 1-based pages)."""
    out = []
    for r in analyze_pdf(path):
        if pages and r.page not in pages:
            continue
        if r.strategy is Strategy.VISION:
            res = llm.transcribe(render_page(path, r.page), PROMPT)
            out.append(PageTranscript(r.page, r.strategy, "vision", res.text.strip(), [res]))
        elif r.strategy is Strategy.EMPTY:
            out.append(PageTranscript(r.page, r.strategy, "none", ""))
        else:  # TEXT_DIGITAL or TEXT_OCR
            calls = [llm.transcribe(render_task(path, r.page, t), PROMPT) for t in r.tasks]
            text = merge_page_text(r, {i: c.text for i, c in enumerate(calls)})
            out.append(PageTranscript(r.page, r.strategy, "text_layer", text.strip(), calls))
    return out


def number_lines(page: int, text: str) -> list[str]:
    """Prefix each non-empty line with an id like `p1:L3`, for later citation."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return [f"p{page}:L{i}\t{ln}" for i, ln in enumerate(lines, start=1)]
