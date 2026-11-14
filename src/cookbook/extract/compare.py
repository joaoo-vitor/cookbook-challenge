"""Compare two independent reads of the same page and flag where they differ.

Two different models read the page. The primary read is the transcript. Where
the second read disagrees, the word or number is flagged as not certain. If the
reads mostly disagree, the whole page is flagged as unreliable: on an illegible
page each model invents different text, so low agreement exposes it.

Plain code, no model calls. Differences that are not content (hyphens, spacing,
punctuation, a fraction written two ways, a page number present in only one read)
are ignored.

Reading order. On a page with columns one model may read row by row and the other
column by column: the same words, in a different order. So the two reads are first
cut into *blocks*: runs of words that are intact (same words, same order) in both reads,
found longest first and wherever they sit. The blocks that keep their relative order in
both reads are in place; the others were *moved*. A moved block is not a disagreement: it
counts as agreeing, and the page is reported as `reordered`. The words left over are then
aligned in order, and what still differs is flagged. Not forgiven, so that an error cannot
hide as a move: a lone number (swapped quantities are still caught, because a number only
moves together with its unit and noun), a block of only tiny words, and a long block (the
order of the steps of a method matters), which is flagged as `order`.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from cookbook.units import normalize_fractions

MIN_AGREEMENT = 0.85  # below this the page is flagged unreliable (a guess, tune on more pages)
MAX_MOVE_WORDS = 12  # an intact block longer than this is not forgiven as a move: it is flagged

_DASHES = re.compile(r"[‐‑–—]")
_EDGE_PUNCT = re.compile(r"^[.,;:!?()\"“”„‚'«»]+|[.,;:!?()\"“”„‚'«»]+$")
_MARKER = re.compile(r"\[\?(.*?)\?\]")
_NUMBERISH = re.compile(r"\d|[½¼¾⅓⅔⅛]")


@dataclass
class Flag:
    line: str  # id of the primary line, like "p1:L3"
    primary: str  # what the primary read has there ("" if it has nothing)
    secondary: str  # what the second read has there ("" if it has nothing)
    kind: str  # "number", "word", "marker" or "order"
    severity: str  # "high" for numbers, "medium" otherwise


@dataclass
class Comparison:
    label: str  # "page" or "image 0", ...
    agreement: float  # 0..1 word similarity of the two reads, ignoring non-content differences
    unreliable: bool
    flags: list[Flag] = field(default_factory=list)
    secondary_markers: int = 0  # unreadable-word markers written by the second read
    reordered: int = 0  # blocks of words both reads have, but at different places (columns read in another order)


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


def _movable(words: list[str]) -> bool:
    """May an intact block that sits at another place count as a move?

    A single word must be a real word (5 letters or more, no digit): a lone number is never a
    move, because swapped quantities have exactly that shape. A longer block must hold a
    number or a word longer than three letters: "in the" matches anywhere and proves nothing.
    """
    if len(words) == 1:
        return not _NUMBERISH.search(words[0]) and len(words[0]) >= 5
    return any(_NUMBERISH.search(w) or len(w) > 3 for w in words)


def _tile(a: list[str], b: list[str]) -> list[tuple[int, int, int]]:
    """Cut the two reads into blocks that are intact in both, longest first, wherever they sit.

    Returns (start in a, start in b, length) for each block. A block is a contiguous run of
    words in both reads, so a number stays with its unit and noun. Only blocks that
    `_movable` accepts count: a run of tiny words matches anywhere and would steal words from
    the real blocks. Among equally long candidates the one at the most similar relative
    position wins, so repeated phrases ("1 cup") pair with the copy that is not displaced.
    """
    a_free, b_free = [True] * len(a), [True] * len(b)
    tiles: list[tuple[int, int, int]] = []
    while True:
        best_length, ends = 0, []  # the longest run found so far, and where it ends in a and b
        previous = [0] * (len(b) + 1)
        for i in range(1, len(a) + 1):
            current = [0] * (len(b) + 1)
            if a_free[i - 1]:
                for j in range(1, len(b) + 1):
                    if b_free[j - 1] and a[i - 1] == b[j - 1]:
                        current[j] = previous[j - 1] + 1
                        if current[j] > best_length:
                            best_length, ends = current[j], [(i, j)]
                        elif current[j] == best_length:
                            ends.append((i, j))
            previous = current
        if best_length == 0:
            return tiles
        i_end, j_end = min(ends, key=lambda e: abs((e[0] - best_length) / max(len(a), 1)
                                                   - (e[1] - best_length) / max(len(b), 1)))
        i0, j0 = i_end - best_length, j_end - best_length
        # A block never ends on a number: it belongs to the unit that follows. "flour 1" in
        # "2 cups flour 1 tablespoon" and "2 cups flour 1 cup milk" would steal the 1 of the next item.
        length = best_length
        while length and _NUMBERISH.search(a[i0 + length - 1]):
            length -= 1
        if length and _movable(a[i0:i0 + length]):
            tiles.append((i0, j0, length))
        # used up either way, so that a run that is not accepted is not found again (the trailing
        # numbers stay free for their own block)
        for k in range(length or best_length):
            a_free[i0 + k] = b_free[j0 + k] = False


def _in_order(tiles: list[tuple[int, int, int]]) -> set[int]:
    """Which blocks keep their relative order in both reads (the heaviest such chain).

    The rest were moved. Weighted longest increasing subsequence: with a few blocks the
    simple quadratic version is enough.
    """
    order = sorted(range(len(tiles)), key=lambda k: tiles[k][0])
    best: dict[int, tuple[int, int | None]] = {}  # tile -> (weight of the chain ending here, previous tile)
    for pos, k in enumerate(order):
        weight, prev = tiles[k][2], None
        for q in order[:pos]:
            if tiles[q][1] + tiles[q][2] <= tiles[k][1] and best[q][0] + tiles[k][2] > weight:
                weight, prev = best[q][0] + tiles[k][2], q
        best[k] = (weight, prev)
    chain: set[int] = set()
    end = max(best, key=lambda k: best[k][0], default=None)
    while end is not None:
        chain.add(end)
        end = best[end][1]
    return chain


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

    def line_id(i: int) -> str:
        if not p_tok:
            return f"{prefix}:L1"
        return f"{prefix}:L{p_tok[min(i, len(p_tok) - 1)][1] + 1}"

    matched = 0.0  # words that agree, counted on both sides
    skipped = 0  # words left out of the score altogether (a page number in only one read)
    moved = 0  # blocks that both reads have, at different places
    positioned: list[tuple[int, Flag]] = []  # (position in the primary read, flag)
    a_done, b_done = [False] * len(a), [False] * len(s_tok)

    # 1. Cut both reads into intact blocks, then see which blocks kept their order.
    tiles = _tile(a, s_tok)
    in_place = _in_order(tiles)
    for k, (i0, j0, n) in enumerate(tiles):
        a_done[i0:i0 + n], b_done[j0:j0 + n] = [True] * n, [True] * n
        if k in in_place:
            matched += n
        elif n > MAX_MOVE_WORDS:  # too long to be an ingredient line: the order may matter
            moved += 1
            positioned.append((i0, Flag(line_id(i0), " ".join(a[i0:i0 + n]), " ".join(s_tok[j0:j0 + n]),
                                        "order", "medium")))
        else:
            matched += n
            moved += 1

    # 2. Align the words left over, in order, but only inside the gap between the same two
    #    in-place blocks, so a word is only matched in its own context. A lone "4" that sits
    #    before "Zwiebeln" in one read and after it in the other is therefore a difference.
    kept = sorted(tiles[k] for k in in_place)
    gaps = zip([(0, 0)] + [(i0 + n, j0 + n) for i0, j0, n in kept],
               [(i0, j0) for i0, j0, n in kept] + [(len(a), len(s_tok))])
    for (a_lo, b_lo), (a_hi, b_hi) in gaps:
        a_left = [i for i in range(a_lo, a_hi) if not a_done[i]]
        b_left = [j for j in range(b_lo, b_hi) if not b_done[j]]
        la, lb = [a[i] for i in a_left], [s_tok[j] for j in b_left]
        for op, x1, x2, y1, y2 in difflib.SequenceMatcher(None, la, lb, autojunk=False).get_opcodes():
            if op == "equal":
                matched += x2 - x1
                continue
            a_words, b_words = la[x1:x2], lb[y1:y2]
            if _squash(a_words) == _squash(b_words):  # only hyphens, spacing or punctuation differ
                matched += (len(a_words) + len(b_words)) / 2
                continue
            a_ids, b_ids = a_left[x1:x2], b_left[y1:y2]
            if _is_page_number(a_words, b_words, [p_tok[i] for i in a_ids], [s_pairs[j] for j in b_ids],
                               p_tok, s_pairs):
                skipped += len(a_words) + len(b_words)
                continue
            kind = "number" if _NUMBERISH.search(" ".join(a_words + b_words)) else "word"
            where = a_ids[0] if a_ids else (a_left[x1] if x1 < len(a_left) else a_hi)
            positioned.append((where, Flag(line_id(where), " ".join(a_words), " ".join(b_words), kind,
                                           "high" if kind == "number" else "medium")))
    flags = [f for _, f in sorted(positioned, key=lambda pf: pf[0])]

    # unreadable-word markers written by the primary read are warnings in their own right
    for li, line in enumerate(p_lines):
        for m in _MARKER.finditer(line):
            flags.append(Flag(f"{prefix}:L{li + 1}", m.group(1) or "?", "", "marker", "medium"))

    total = len(a) + len(s_tok) - skipped
    agreement = 1.0 if total <= 0 else (min(1.0, 2 * matched / total) if (a and s_tok) else 0.0)
    return Comparison(label, round(agreement, 3), agreement < min_agreement, flags,
                      sum(len(_MARKER.findall(ln)) for ln in s_lines), moved)


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
