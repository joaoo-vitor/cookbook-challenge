"""The transcript of a whole document: every written line with an id, in reading order.

This is what the structuring step reads and what the checks compare against. It also
carries how sure we are of each line: where the two readings of a page disagreed,
the line is marked uncertain, and a page whose readings mostly disagree marks all
its lines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .transcribe import PageTranscript, number_lines


@dataclass(frozen=True)
class Note:
    severity: str  # "high" or "medium"
    reason: str


@dataclass
class Transcript:
    lines: dict[str, str] = field(default_factory=dict)  # "p1:L3" -> text, in reading order
    uncertain: dict[str, list[Note]] = field(default_factory=dict)  # line id -> why we are unsure
    unreliable_pages: list[int] = field(default_factory=list)
    reordered_pages: dict[int, int] = field(default_factory=dict)  # page -> blocks the two readings put in another order

    def as_prompt(self) -> str:
        """The lines with their ids, for the model that structures the recipe."""
        return "\n".join(f"{line_id}\t{text}" for line_id, text in self.lines.items())

    def text_of(self, ids: list[str]) -> str:
        """The text of the given lines, with line-end hyphens joined ("Bra-" + "ten" = "Braten")."""
        return re.sub(r"(?<=\w)-\n(?=\w)", "", "\n".join(self.lines[i] for i in ids if i in self.lines))

    def full_text(self) -> str:
        return self.text_of(list(self.lines))


def build_transcript(pages: list[PageTranscript]) -> Transcript:
    """Join the pages' transcripts and attach the uncertainty found by comparing two readings."""
    out = Transcript()
    for page in pages:
        for numbered in number_lines(page.page, page.text, count_blank=page.source == "text_file"):
            line_id, _, text = numbered.partition("\t")
            out.lines[line_id] = text
        page_lines = [i for i in out.lines if i.startswith(f"p{page.page}:L")]
        for cmp in page.comparisons:
            if cmp.unreliable:
                out.unreliable_pages.append(page.page)
                note = Note("high", f"page {page.page} is unreliable: the two readings agree on only "
                                    f"{cmp.agreement:.0%} of the words")
                for line_id in page_lines:
                    out.uncertain.setdefault(line_id, []).append(note)
            if cmp.label != "page":
                continue  # differences inside an image have no line ids on the page: only the page check applies
            if cmp.reordered:
                out.reordered_pages[page.page] = cmp.reordered
            for flag in cmp.flags:
                out.uncertain.setdefault(flag.line, []).append(Note(
                    flag.severity, f"{flag.kind}: one reading has {flag.primary!r}, the other {flag.secondary!r}"))
    return out
