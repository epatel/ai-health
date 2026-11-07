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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aihealth.lang.base import LanguageAdapter
    from aihealth.model import Module

_WINNOW = 8  # w: windows per winnowing run
_MAX_ANCHORS = 24
_BASE = 1_000_003
_MASK = (1 << 61) - 1


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
        # innermost owner per line: shorter spans win
        owner_of_line: dict[int, int] = {}
        for idx in sorted(
            range(len(funcs)), key=lambda i: funcs[i].end_line - funcs[i].start_line, reverse=True
        ):
            f = funcs[idx]
            for ln in range(f.start_line, f.end_line + 1):
                owner_of_line[ln] = idx
        stream = _Stream(module.path, [], [], [], [f.qualname for f in funcs])
        for key, line in adapter.tokens(module.root):
            owner = owner_of_line.get(line)
            if owner is None:
                continue
            tid = self._vocab.setdefault(key, len(self._vocab))
            stream.ids.append(tid)
            stream.lines.append(line)
            stream.owner.append(owner)
        if len(stream.ids) >= self.min_tokens:
            self._streams.append(stream)

    def clones(self) -> list[Clone]:
        k = max(1, self.min_tokens - _WINNOW + 1)
        index: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for s_idx, stream in enumerate(self._streams):
            for pos, h in _winnow(stream, k):
                index[h].append((s_idx, pos))

        found: dict[tuple, Clone] = {}
        for anchors in index.values():
            if len(anchors) < 2:
                continue
            # a fingerprint shared by dozens of places is boilerplate; pairing
            # all of them is quadratic and adds nothing actionable
            anchors = anchors[:_MAX_ANCHORS]
            for i in range(len(anchors)):
                for j in range(i + 1, len(anchors)):
                    clone = self._extend(anchors[i], anchors[j], k)
                    if clone is not None:
                        key, c = clone
                        found.setdefault(key, c)
        return sorted(found.values(), key=lambda c: (-c.tokens, c.a.path, c.a.start_line))

    def _extend(self, x: tuple[int, int], y: tuple[int, int], k: int) -> tuple[tuple, Clone] | None:
        (sa, pa), (sb, pb) = x, y
        A, B = self._streams[sa], self._streams[sb]
        if A.ids[pa : pa + k] != B.ids[pb : pb + k]:
            return None  # hash collision
        if A.owner[pa] != A.owner[pa + k - 1] or B.owner[pb] != B.owner[pb + k - 1]:
            return None
        oa, ob = A.owner[pa], B.owner[pb]
        start_a, start_b = pa, pb
        while (
            start_a > 0
            and start_b > 0
            and A.ids[start_a - 1] == B.ids[start_b - 1]
            and A.owner[start_a - 1] == oa
            and B.owner[start_b - 1] == ob
        ):
            start_a -= 1
            start_b -= 1
        end_a, end_b = pa + k, pb + k
        while (
            end_a < len(A.ids)
            and end_b < len(B.ids)
            and A.ids[end_a] == B.ids[end_b]
            and A.owner[end_a] == oa
            and B.owner[end_b] == ob
        ):
            end_a += 1
            end_b += 1
        length = end_a - start_a
        if sa == sb and not (end_a <= start_b or end_b <= start_a):
            return None  # overlapping self-match (repetitive code)
        if length < self.min_tokens:
            return None
        side_a = CloneSide(A.path, A.funcs[oa], A.lines[start_a], A.lines[end_a - 1])
        side_b = CloneSide(B.path, B.funcs[ob], B.lines[start_b], B.lines[end_b - 1])
        clone = Clone(side_a, side_b, length)
        if clone.lines < self.min_lines:
            return None
        return (sa, start_a, sb, start_b, length), clone


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
