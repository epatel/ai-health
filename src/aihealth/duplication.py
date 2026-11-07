"""Token-based clone detection within and across files.

Tokens come from the language adapter (`adapter.tokens`): by default the
leaves of the syntax tree, with literals collapsed to their type (so `pad(8)`
and `pad(16)` match) and identifiers kept (renamed code is not reported,
which keeps false positives in repetitive Flutter widget code down). Only
tokens on lines owned by a scored function count, and clones never cross a
function boundary.

Matching uses winnowing (Schleimer, Wilkerson, Aiken 2003): rolling hashes
of k-token windows, of which only the minimum in every run of `w` windows
is indexed. Any shared run of >= k + w - 1 tokens is guaranteed to share
an indexed fingerprint; matches are then extended token by token to their
full length. Memory stays at roughly 2/(w+1) of all windows.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from aihealth.lang.base import LanguageAdapter
    from aihealth.model import Function, Module

_WINNOW = 8  # w: windows per winnowing run
_MAX_ANCHORS = 24
_BASE = 1_000_003
_MASK = (1 << 61) - 1


class Anchor(NamedTuple):
    """A place in the index: token position ``pos`` in stream ``stream``."""

    stream: int
    pos: int


class Span(NamedTuple):
    """Token positions [start, end) in one stream."""

    start: int
    end: int


@dataclass
class _Stream:
    path: str
    ids: list[int]  # interned normalised tokens
    lines: list[int]  # 1-based line of each token
    owner: list[int]  # index into funcs of the innermost function
    funcs: list[str]  # qualnames


@dataclass
class CloneSide:
    path: str
    qualname: str
    start_line: int
    end_line: int


@dataclass
class Clone:
    a: CloneSide
    b: CloneSide
    tokens: int

    @property
    def lines(self) -> int:
        return min(self.a.end_line - self.a.start_line, self.b.end_line - self.b.start_line) + 1


@dataclass
class DuplicationIndex:
    """Collects modules, then finds clones among all of them."""

    min_tokens: int = 50
    min_lines: int = 6
    _vocab: dict[str, int] = field(default_factory=dict)
    _streams: list[_Stream] = field(default_factory=list)

    def add(self, module: Module, adapter: LanguageAdapter) -> None:
        funcs = module.all_functions()
        if not funcs:
            return
        owner_of_line = _owner_of_line(funcs)
        stream = _Stream(module.path, [], [], [], [f.qualname for f in funcs])
        for key, line in adapter.tokens(module.root):
            owner = owner_of_line.get(line)
            if owner is not None:
                stream.ids.append(self._vocab.setdefault(key, len(self._vocab)))
                stream.lines.append(line)
                stream.owner.append(owner)
        if len(stream.ids) >= self.min_tokens:
            self._streams.append(stream)

    def clones(self) -> list[Clone]:
        k = max(1, self.min_tokens - _WINNOW + 1)
        found: dict[tuple, Clone] = {}
        for anchors in self._fingerprint_index(k).values():
            for x, y in _anchor_pairs(anchors):
                result = self._extend(x, y, k)
                if result is not None:
                    found.setdefault(*result)
        return sorted(found.values(), key=lambda c: (-c.tokens, c.a.path, c.a.start_line))

    def _fingerprint_index(self, k: int) -> dict[int, list[Anchor]]:
        """Winnowed fingerprint -> the places it occurs."""
        index: dict[int, list[Anchor]] = defaultdict(list)
        for s_idx, stream in enumerate(self._streams):
            for pos, h in _winnow(stream, k):
                index[h].append(Anchor(s_idx, pos))
        return index

    def _extend(self, x: Anchor, y: Anchor, k: int) -> tuple[tuple, Clone] | None:
        """Grow a shared k-token window at x and y to the full matching run
        within one function on each side; None if it's not a reportable clone."""
        (sa, pa), (sb, pb) = x, y
        A, B = self._streams[sa], self._streams[sb]
        window = _window(A, pa, k)
        if window is None or window != _window(B, pb, k):
            return None  # hash collision, or the window spans two functions
        a, b = _Side(A, A.owner[pa]), _Side(B, B.owner[pb])
        back = _run(a, b, (pa - 1, pb - 1), step=-1)
        ahead = _run(a, b, (pa + k, pb + k), step=1)
        span_a, span_b = Span(pa - back, pa + k + ahead), Span(pb - back, pb + k + ahead)
        if sa == sb and _overlaps(span_a, span_b):
            return None  # overlapping self-match (repetitive code)
        length = span_a.end - span_a.start
        if length < self.min_tokens:
            return None
        clone = Clone(a.clone_side(span_a), b.clone_side(span_b), length)
        if clone.lines < self.min_lines:
            return None
        return (sa, span_a.start, sb, span_b.start, length), clone


def _owner_of_line(funcs: list[Function]) -> dict[int, int]:
    """Innermost function per line: shorter spans are assigned last and win
    (a stable sort, so equal spans keep source order)."""
    owner: dict[int, int] = {}
    by_span = sorted(
        range(len(funcs)), key=lambda i: funcs[i].end_line - funcs[i].start_line, reverse=True
    )
    for idx in by_span:
        owner.update(dict.fromkeys(range(funcs[idx].start_line, funcs[idx].end_line + 1), idx))
    return owner


def _anchor_pairs(anchors: list[Anchor]):
    """Pairs of places sharing a fingerprint. A fingerprint shared by dozens
    of places is boilerplate; pairing all of them is quadratic and adds
    nothing actionable, so only the first _MAX_ANCHORS are paired."""
    if len(anchors) < 2:
        return ()
    return combinations(anchors[:_MAX_ANCHORS], 2)


def _window(stream: _Stream, pos: int, k: int) -> list[int] | None:
    """The k token ids at pos, or None if they belong to two functions."""
    if stream.owner[pos] != stream.owner[pos + k - 1]:
        return None
    return stream.ids[pos : pos + k]


def _overlaps(a: Span, b: Span) -> bool:
    return not (a.end <= b.start or b.end <= a.start)


@dataclass(frozen=True)
class _Side:
    """One side of a clone: a token stream restricted to one function."""

    stream: _Stream
    owner: int

    def token(self, i: int) -> int | None:
        """Token id at i, or None if i is outside the stream or the function."""
        in_range = 0 <= i < len(self.stream.ids)
        return self.stream.ids[i] if in_range and self.stream.owner[i] == self.owner else None

    def clone_side(self, span: Span) -> CloneSide:
        s = self.stream
        return CloneSide(s.path, s.funcs[self.owner], s.lines[span.start], s.lines[span.end - 1])


def _run(a: _Side, b: _Side, at: tuple[int, int], step: int) -> int:
    """How many positions, starting at ``at`` and moving by ``step``, carry
    the same token on both sides."""
    (i, j), n = at, 0
    while a.token(i) is not None and a.token(i) == b.token(j):
        i, j, n = i + step, j + step, n + 1
    return n


def _winnow(stream: _Stream, k: int):
    """Yield (position, hash) fingerprints selected by winnowing."""
    ids = stream.ids
    n = len(ids)
    if n < k:
        return
    power = pow(_BASE, k - 1, _MASK)
    h = 0
    for t in ids[:k]:
        h = (h * _BASE + t + 1) % _MASK
    hashes = [h]
    for i in range(k, n):
        h = ((h - (ids[i - k] + 1) * power) * _BASE + ids[i] + 1) % _MASK
        hashes.append(h)
    last = -1
    for start in range(max(1, len(hashes) - _WINNOW + 1)):
        window = hashes[start : start + _WINNOW]
        # rightmost minimum, as in the paper
        m = min(range(len(window)), key=lambda i: (window[i], -i))
        pos = start + m
        if pos != last:
            yield pos, hashes[pos]
            last = pos
