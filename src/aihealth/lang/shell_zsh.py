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
import warnings
from dataclasses import dataclass

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

# `for k v in ...`: the grammar takes one loop variable
_FOR_NAMES = re.compile(rb"\bfor[ \t]+\w+((?:[ \t]+\w+)+)(?=[ \t]+in\b)")
_FLAGS = re.compile(rb"\([^)\n]*\)")
_PARAM = re.compile(rb"[\w#@*!?-]*(?:\[[^]\n]*\])?")
_PARENS = bytes.maketrans(b"()", b"__")
_CPU_SECONDS = 1


def is_shell_head(head: bytes) -> bool:
    """A sh/bash/dash/ksh/zsh shebang, or a zsh `#compdef` / `#autoload`."""
    return _SHELL_HEAD.match(head) is not None


def is_zsh_head(head: bytes) -> bool:
    """A zsh shebang, `#compdef` or `#autoload`."""
    return _ZSH_HEAD.match(head) is not None


def prepare_zsh(source: bytes) -> bytes:
    """Same length and lines: extra `for` variables blanked, and parens in
    `${name/pattern/replacement}` turned into `_` (the endless-loop case
    above; the pattern's text doesn't matter for metrics)."""
    out = bytearray(_FOR_NAMES.sub(_blank_group, source))
    start = source.find(b"${")
    while start >= 0:
        span = _Expansion(source, start).substitution()
        if span is not None:
            out[span] = out[span].translate(_PARENS)
        start = source.find(b"${", start + 2)
    return bytes(out)


def _blank_group(match: re.Match[bytes]) -> bytes:
    start, end = match.span(1)
    whole = match.group(0)
    offset = match.start()
    return whole[: start - offset] + b" " * (end - start) + whole[end - offset :]


@dataclass(frozen=True)
class _Expansion:
    """The `${...}` starting at ``start`` in ``src``."""

    src: bytes
    start: int

    def end(self) -> int:
        """Index after its closing `}` on the same line, or -1."""
        src = self.src
        depth = 0
        i = self.start
        while i < len(src):
            ch = src[i : i + 1]
            if ch == b"\n":
                return -1
            if ch == b"}" and depth == 1:
                return i + 1
            depth += (ch == b"{") - (ch == b"}")
            i += 2 if ch == b"\\" else 1
        return -1

    def operator(self) -> int:
        """Index after the flags and the parameter: a name with an optional
        subscript, or a nested `${...}`."""
        i = self.start + 2
        flags = _FLAGS.match(self.src, i)
        i = flags.end() if flags else i
        if self.src.startswith(b"${", i):
            return max(_Expansion(self.src, i).end(), i)
        return _PARAM.match(self.src, i).end()

    def substitution(self) -> slice | None:
        """The `/pattern/replacement}` part, unless it runs a command."""
        end = self.end()
        op = self.operator()
        if end < 0 or self.src[op : op + 1] != b"/":
            return None
        return None if b"$(" in self.src[op:end] else slice(op, end)


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
    to one CPU second (about 2 ms for a normal file). No fork: assume not."""
    if resource is None or not hasattr(os, "fork"):
        return False
    with warnings.catch_warnings():  # "multi-threaded, fork() may deadlock"
        warnings.simplefilter("ignore", DeprecationWarning)
        pid = os.fork()
    if pid == 0:  # the child only parses, then leaves without cleanup
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (_CPU_SECONDS, _CPU_SECONDS))
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
