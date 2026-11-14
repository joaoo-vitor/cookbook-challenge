"""The transcript of a whole document: every written line with an id, in reading order.

This is what the structuring step reads and what the checks compare against. It also
carries how sure we are of each line: where the two readings of a page disagreed,
the line is marked uncertain, and a page whose readings mostly disagree marks all
its lines.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .transcribe import PageTranscript, number_lines


@dataclass(frozen=True)
class Note:
    severity: str  # "high" or "medium"
    reason: str
    # what each reading had, when the note comes from a difference between the two readings
    # (a page-level note has none). A page can use these to underline the doubtful word.
    kind: str | None = None
    primary: str | None = None
    secondary: str | None = None


@dataclass
class Transcript:
    lines: dict[str, str] = field(default_factory=dict)  # "p1:L3" -> text, in reading order
    uncertain: dict[str, list[Note]] = field(default_factory=dict)  # line id -> why we are unsure
    unreliable_pages: list[int] = field(default_factory=list)
    reordered_pages: dict[int, int] = field(default_factory=dict)  # page -> blocks the two readings put in another order
    human_checked: set[str] = field(default_factory=set)  # lines a person read and confirmed or corrected

    def to_json(self) -> dict:
        """Plain data for a page or a file. `from_json` is the inverse."""
        return {"lines": self.lines,
                "uncertain": {i: [asdict(n) for n in notes] for i, notes in self.uncertain.items()},
                "unreliable_pages": self.unreliable_pages,
                "reordered_pages": {str(p): n for p, n in self.reordered_pages.items()},
                "human_checked": sorted(self.human_checked)}

    @classmethod
    def from_json(cls, data: dict) -> "Transcript":
        return cls(lines=dict(data.get("lines", {})),
                   uncertain={i: [Note(**n) for n in notes] for i, notes in data.get("uncertain", {}).items()},
                   unreliable_pages=list(data.get("unreliable_pages", [])),
                   reordered_pages={int(p): n for p, n in data.get("reordered_pages", {}).items()},
                   human_checked=set(data.get("human_checked", [])))

    def with_edits(self, edited: dict[str, str]) -> "Transcript":
        """A copy with the person's version of each line. A line they changed or confirmed is no longer in doubt.

        Only lines that exist can be edited: line ids are what everything else cites, so the
        set of ids never changes here.
        """
        unknown = [i for i in edited if i not in self.lines]
        if unknown:
            raise ValueError(f"unknown line id(s): {', '.join(unknown[:5])}")
        out = Transcript.from_json(self.to_json())
        for line_id, text in edited.items():
            if text != self.lines[line_id] or line_id in self.uncertain:
                out.human_checked.add(line_id)
            out.lines[line_id] = text
            out.uncertain.pop(line_id, None)
        return out

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
                    flag.severity, f"{flag.kind}: one reading has {flag.primary!r}, the other {flag.secondary!r}",
                    flag.kind, flag.primary, flag.secondary))
    return out
