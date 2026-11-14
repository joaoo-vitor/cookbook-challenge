"""Compare two independent reads of the same page and flag where they differ.

Two different models read the page. The primary read is the transcript. Where
the second read disagrees, the word or number is flagged as not certain. If the
reads mostly disagree, the whole page is flagged as unreliable: on an illegible
page each model invents different text, so low agreement exposes it.

Plain code, no model calls. Differences that are not content (hyphens, spacing,
punctuation, a page number present in only one read) are ignored.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from cookbook.units import normalize_fractions

MIN_AGREEMENT = 0.85  # below this the page is flagged unreliable (a guess, tune on more pages)

_DASHES = re.compile(r"[‐‑–—]")
_EDGE_PUNCT = re.compile(r"^[.,;:!?()\"“”„‚'«»]+|[.,;:!?()\"“”„‚'«»]+$")
_MARKER = re.compile(r"\[\?(.*?)\?\]")
_NUMBERISH = re.compile(r"\d|[½¼¾⅓⅔⅛]")


@dataclass
class Flag:
    line: str  # id of the primary line, like "p1:L3"
    primary: str  # what the primary read has there ("" if it has nothing)
    secondary: str  # what the second read has there ("" if it has nothing)
    kind: str  # "number", "word" or "marker"
    severity: str  # "high" for numbers, "medium" otherwise


@dataclass
class Comparison:
    label: str  # "page" or "image 0", ...
    agreement: float  # 0..1 word similarity of the two reads, ignoring non-content differences
    unreliable: bool
    flags: list[Flag] = field(default_factory=list)
    secondary_markers: int = 0  # unreadable-word markers written by the second read


def _tokens(lines: list[str]) -> list[tuple[str, int]]:
    """(word, line index) for each word, with line-end hyphens joined.

    "Bra-" at the end of a line and "tenstücke" at the start of the next become
    "Bratenstücke", attributed to the first line. Drawing-label markers and
    standalone dashes are not content.
    """
    out: list[tuple[str, int]] = []
    pending: tuple[str, int] | None = None  # (word without its hyphen, line index)
    for li, line in enumerate(lines):
        words = _DASHES.sub("-", normalize_fractions(line.replace("[drawing]", " "))).split()
        words = [w for w in words if w.strip("-")]  # drop standalone dashes
        if not words:
            continue
        if pending:
            words[0] = pending[0] + words[0]
            out.append((words[0], pending[1]))
            words, pending = words[1:], None
        if words and re.search(r"\w-$", words[-1]):
            pending = (words[-1][:-1], li)
            words = words[:-1]
        out.extend((w, li) for w in words)
    if pending:  # hyphen at the very end of the text
        out.append((pending[0], pending[1]))
    return out


def _clean(word: str) -> str:
    word = _MARKER.sub(lambda m: m.group(1), word).replace("[?", "").replace("?]", "")
    return _EDGE_PUNCT.sub("", word)


def _squash(words: list[str]) -> str:
    """Letters and digits only, so 'Korin then', 'Korin-then' and 'Korinthen' match."""
    return re.sub(r"[\W_]", "", "".join(words))


def compare_reads(page: int, primary: str, secondary: str, label: str = "page",
                  id_prefix: str | None = None, min_agreement: float = MIN_AGREEMENT) -> Comparison:
    """Compare the primary and second read (plain text, one written line per line)."""
    prefix = id_prefix or f"p{page}"
    p_lines = [ln for ln in primary.splitlines() if ln.strip()]  # same numbering as number_lines
    s_lines = [ln for ln in secondary.splitlines() if ln.strip()]
    p_tok = [(_clean(w), li) for w, li in _tokens(p_lines)]
    p_tok = [(w, li) for w, li in p_tok if w]
    s_pairs = [(_clean(w), li) for w, li in _tokens(s_lines)]
    s_pairs = [(w, li) for w, li in s_pairs if w]
    s_tok = [w for w, _ in s_pairs]
    a = [w for w, _ in p_tok]
    flags: list[Flag] = []
    pending: list[tuple[Flag, list[str], list[str]]] = []  # (flag, primary words, second words)

    def line_id(i: int) -> str:
        if not p_tok:
            return f"{prefix}:L1"
        return f"{prefix}:L{p_tok[min(i, len(p_tok) - 1)][1] + 1}"

    matched = 0.0  # words that agree, counted on both sides
    skipped = 0  # words left out of the score altogether (a page number in only one read)
    for op, a1, a2, b1, b2 in difflib.SequenceMatcher(None, a, s_tok, autojunk=False).get_opcodes():
        if op == "equal":
            matched += a2 - a1
            continue
        a_words, b_words = a[a1:a2], s_tok[b1:b2]
        if _squash(a_words) == _squash(b_words):  # only hyphens, spacing or punctuation differ
            matched += (len(a_words) + len(b_words)) / 2
            continue
        if _is_page_number(a_words, b_words, p_tok[a1:a2], s_pairs[b1:b2], p_tok, s_pairs):
            skipped += len(a_words) + len(b_words)
            continue
        kind = "number" if _NUMBERISH.search(" ".join(a_words + b_words)) else "word"
        pending.append((Flag(line_id(a1), " ".join(a_words), " ".join(b_words), kind,
                             "high" if kind == "number" else "medium"), a_words, b_words))

    # A single word that is missing in one place and present in another (a drawing label
    # listed in a different order) is the same content moved, not a disagreement. Numbers
    # are never treated this way: a moved quantity changes the recipe.
    lone_missing = [i for i, (f, aw, bw) in enumerate(pending) if f.kind == "word" and aw and not bw and len(aw) == 1]
    lone_extra = [i for i, (f, aw, bw) in enumerate(pending) if f.kind == "word" and bw and not aw and len(bw) == 1]
    moved: set[int] = set()
    for i in lone_missing:
        for j in lone_extra:
            if j not in moved and _squash(pending[i][1]) == _squash(pending[j][2]):
                moved |= {i, j}
                matched += 1
                break
    flags.extend(f for k, (f, _, _) in enumerate(pending) if k not in moved)

    # unreadable-word markers written by the primary read are warnings in their own right
    for li, line in enumerate(p_lines):
        for m in _MARKER.finditer(line):
            flags.append(Flag(f"{prefix}:L{li + 1}", m.group(1) or "?", "", "marker", "medium"))

    total = len(a) + len(s_tok) - skipped
    agreement = 1.0 if total <= 0 else (min(1.0, 2 * matched / total) if (a and s_tok) else 0.0)
    return Comparison(label, round(agreement, 3), agreement < min_agreement, flags,
                      sum(len(_MARKER.findall(ln)) for ln in s_lines))


def _is_page_number(a_words, b_words, a_part, b_part, a_all, b_all) -> bool:
    """A bare number, alone on the first or last line, that only one of the reads has.

    That is a page number. A quantity that starts or ends a line with other words
    is not, so it is still flagged.
    """
    if a_words and b_words:
        return False
    part, every = (a_part, a_all) if a_words else (b_part, b_all)
    if len(part) != 1 or not part[0][0].isdigit():
        return False
    line = part[0][1]
    return sum(1 for _, li in every if li == line) == 1 and line in (every[0][1], every[-1][1])
