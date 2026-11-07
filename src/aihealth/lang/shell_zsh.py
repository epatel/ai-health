"""zsh parts of the shell adapter: which files are zsh, source preparation,
and a guarded parser that keeps the better of the zsh and bash trees.

The `zsh` grammar (tree-sitter-language-pack) is a bash fork with the same
node names plus zsh constructs, but it is young: it loses whole files that
the bash grammar reads, and the reverse (see cards/parser-choice.md), and
on some input it never returns while its memory grows to gigabytes (`)` in
the replacement of `${a/b/)}`, `${${a}/b/${c}(d)}`, and others not
narrowed down). So zsh source is parsed in a forked child with a CPU limit
first, and the bash tree is used when that child doesn't finish.
"""

from __future__ import annotations

import os
import re
import signal
import warnings
from bisect import bisect_left
from typing import NamedTuple

from tree_sitter import Node, Parser, Tree
from tree_sitter_language_pack import get_parser

try:
    import resource
except ImportError:  # Windows: no fork either, see _finishes
    resource = None

ZSH_EXTENSIONS = (".zsh", ".zsh-theme")
_SHEBANG_PATH = rb"#![ \t]*\S*?(?:/|env[ \t]+(?:-\S+[ \t]+)*)"
# completion (`_docker`) and autoload functions start with these, no shebang
_ZSH_MARKERS = rb"|#compdef\b|#autoload\b"
_SHELL_HEAD = re.compile(_SHEBANG_PATH + rb"(?:ba|da|k|z)?sh\b" + _ZSH_MARKERS)
_ZSH_HEAD = re.compile(_SHEBANG_PATH + rb"zsh\b" + _ZSH_MARKERS)

# `for k v in ...`: the grammar takes one loop variable (at most 8 more, so
# a line of many `for` words isn't backtracked over once per `for`)
_FOR_NAMES = re.compile(rb"\bfor[ \t]+\w+((?:[ \t]+\w+){1,8})(?=[ \t]+in\b)")
# bounded, so a long line of `${(` or `${a[` isn't rescanned for each one
_FLAGS = re.compile(rb"\([^)\n]{0,64}\)")
_PARAM = re.compile(rb"[\w#@*!?-]*(?:\[[^]\n]{0,256}\])?")
_BRACE_EVENTS = re.compile(rb"\\.|[{}\n]", re.DOTALL)
_COMMAND = re.compile(rb"\$\(")
_PARENS = bytes.maketrans(b"()", b"__")
_CPU_SECONDS = 1
_WALL_SECONDS = 3  # also ends a child that blocks without using CPU


def is_shell_head(head: bytes) -> bool:
    """A sh/bash/dash/ksh/zsh shebang, or a zsh `#compdef` / `#autoload`."""
    return _SHELL_HEAD.match(head) is not None


def is_zsh_head(head: bytes) -> bool:
    """A zsh shebang, `#compdef` or `#autoload`."""
    return _ZSH_HEAD.match(head) is not None


def prepare_zsh(source: bytes) -> bytes:
    """Same length and lines: extra `for` variables blanked, and parens in
    `${name/pattern/replacement}` turned into `_` (the endless-loop case
    above; the pattern's text doesn't matter for metrics). Linear time."""
    out = bytearray(_FOR_NAMES.sub(_blank_group, source))
    if b"${" in source:
        for part in _Expansions(source).substitution_parts():
            out[part.start : part.end] = out[part.start : part.end].translate(_PARENS)
    return bytes(out)


def _blank_group(match: re.Match[bytes]) -> bytes:
    start, end = match.span(1)
    whole = match.group(0)
    offset = match.start()
    return whole[: start - offset] + b" " * (end - start) + whole[end - offset :]


class _Span(NamedTuple):
    start: int
    end: int


def _match_braces(src: bytes) -> dict[int, int]:
    """For each `${` (by position), the index after its `}` on the same
    line. One pass: thousands of `${` on one line stay linear."""
    closes: dict[int, int] = {}
    stack: list[int] = []
    for m in _BRACE_EVENTS.finditer(src):  # a backslash pair is skipped
        token = m.group()
        if token == b"\n":
            stack.clear()
        elif token == b"{":
            stack.append(m.start() - 1)
        elif token == b"}" and stack:
            closes[stack.pop()] = m.end()
    return {start: end for start, end in closes.items() if src[start : start + 1] == b"$"}


class _Expansions:
    """The `${...}` expansions of a source and where commands (`$(`) start."""

    def __init__(self, src: bytes) -> None:
        self.src = src
        self.closes = _match_braces(src)
        self.commands = [m.start() for m in _COMMAND.finditer(src)]

    def substitution_parts(self) -> list[_Span]:
        """The `/pattern/replacement}` parts, merged where they nest."""
        parts = (self._substitution(_Span(*item)) for item in self.closes.items())
        merged: list[_Span] = []
        for part in sorted(p for p in parts if p is not None):
            if merged and part.start <= merged[-1].end:
                merged[-1] = _Span(merged[-1].start, max(merged[-1].end, part.end))
            else:
                merged.append(part)
        return merged

    def _substitution(self, expansion: _Span) -> _Span | None:
        """The part from the `/` after the parameter, unless it runs a command."""
        op = self._operator(expansion)
        if self.src[op : op + 1] != b"/":
            return None
        part = _Span(op, expansion.end)
        return None if self._runs_command(part) else part

    def _operator(self, expansion: _Span) -> int:
        """Index after the flags and the parameter: a name with an optional
        subscript, or a nested `${...}`."""
        i = expansion.start + 2
        flags = _FLAGS.match(self.src, i)
        i = flags.end() if flags else i
        if i in self.closes:
            return self.closes[i]
        return _PARAM.match(self.src, i).end()

    def _runs_command(self, part: _Span) -> bool:
        k = bisect_left(self.commands, part.start)
        return k < len(self.commands) and self.commands[k] + 2 <= part.end


def lost_lines(root: Node) -> int:
    """Lines the parser could not place: tokens directly in ERROR nodes and
    missing nodes (statements recovered inside an ERROR don't count)."""
    lost: set[int] = set()
    stack = [(root, False)]
    while stack:
        node, in_error = stack.pop()
        if _is_lost(node, in_error):
            lost.update(range(node.start_point[0], node.end_point[0] + 1))
        elif node.has_error:
            inherit = _inherits_error(node, in_error)
            stack.extend((child, inherit) for child in node.children)
    return len(lost)


def _is_lost(node: Node, in_error: bool) -> bool:
    if node.is_missing:
        return True
    return node.child_count == 0 and _inherits_error(node, in_error)


def _inherits_error(node: Node, in_error: bool) -> bool:
    """Are the children of ``node`` directly in an ERROR node?"""
    if node.type == "ERROR":
        return True
    return in_error and not node.is_named


def _finishes(parser: Parser, source: bytes) -> bool:
    """Does ``parser`` return on ``source``? Tried in a forked child limited
    to one CPU second and three seconds of wall-clock time (about 2 ms for a
    normal file). No fork: assume not."""
    if resource is None or not hasattr(os, "fork"):
        return False
    with warnings.catch_warnings():  # "multi-threaded, fork() may deadlock"
        warnings.simplefilter("ignore", DeprecationWarning)
        pid = os.fork()
    if pid == 0:  # the child only parses, then leaves without cleanup
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (_CPU_SECONDS, _CPU_SECONDS))
            signal.signal(signal.SIGALRM, signal.SIG_DFL)  # the default kills it
            signal.alarm(_WALL_SECONDS)
            parser.parse(source)
        finally:
            os._exit(0)
    return os.waitpid(pid, 0)[1] == 0


class ZshParser:
    """Stands in for the zsh grammar's parser: the zsh tree when it parses
    cleanly, else whichever of the zsh and bash trees loses fewer lines
    (the bash one alone when zsh doesn't finish)."""

    def __init__(self) -> None:
        self._zsh = get_parser("zsh")
        self._bash = get_parser("bash")

    def parse(self, source: bytes) -> Tree:
        zsh = self._zsh.parse(source) if _finishes(self._zsh, source) else None
        zsh_lost = lost_lines(zsh.root_node) if zsh is not None else None
        if zsh is not None and zsh_lost == 0:
            return zsh
        bash = self._bash.parse(source)
        if zsh is None or lost_lines(bash.root_node) < zsh_lost:
            return bash
        return zsh
