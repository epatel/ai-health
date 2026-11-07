"""Shared adapter machinery: parsing, code-line counting, error counting."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterator
from typing import ClassVar

from tree_sitter import Node
from tree_sitter_language_pack import get_parser

from aihealth.metrics import Role
from aihealth.model import Class, Function, Module


def iter_nodes(node: Node) -> Iterator[Node]:
    """Pre-order traversal without recursion (deep trees are common)."""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def text(node: Node | None) -> str:
    return node.text.decode("utf-8", "replace") if node is not None else ""


def code_lines(root: Node, skip: Callable[[Node], bool]) -> set[int]:
    """1-based line numbers holding at least one code token.

    A leaf spanning several lines (multi-line string) marks all of them.
    Subtrees for which ``skip`` is true (comments, docstrings) are ignored.
    """
    lines: set[int] = set()
    stack = [root]
    while stack:
        n = stack.pop()
        if skip(n):
            continue
        if n.child_count == 0:
            if n.end_byte > n.start_byte:
                lines.update(range(n.start_point[0] + 1, n.end_point[0] + 2))
        else:
            stack.extend(n.children)
    return lines


def split_top(text: str, sep: str, open_ch: str, close_ch: str) -> list[str]:
    """Split on ``sep`` outside brackets: `str, dict[str, int]` -> 2 parts."""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


def type_leaves(text: str, open_ch: str, close_ch: str) -> list[str]:
    """Innermost type names: a generic is judged by its arguments, so
    `list[Order]` -> [Order], `dict[str, int]` -> [str, int], bare `list` ->
    [list]; unions split, `None` / trailing `?` dropped."""
    leaves: list[str] = []
    for part in split_top(text, "|", open_ch, close_ch):
        part = part.rstrip("?")
        if part in ("", "None"):
            continue
        if open_ch in part and part.endswith(close_ch):
            name, inner = part.split(open_ch, 1)
            args = split_top(inner[:-1], ",", open_ch, close_ch)
            leaves += [x for a in args for x in type_leaves(a, open_ch, close_ch)] or [name]
        else:
            leaves.append(part)
    return leaves


def count_errors(root: Node) -> int:
    if not root.has_error:
        return 0
    return sum(1 for n in iter_nodes(root) if n.type == "ERROR" or n.is_missing)


def count_in_span(lines: set[int], start: int, end: int) -> int:
    return sum(1 for ln in lines if start <= ln <= end)


def _assign_own_loc(funcs: list[Function], lines: set[int]) -> None:
    """A function's loc counts its own code lines only: lines inside nested
    scored functions (local defs, Dart test closures) belong to those."""
    sorted_lines = sorted(lines)
    ordered = sorted(funcs, key=lambda f: (f.start_line, -f.end_line))
    for i, fn in enumerate(ordered):
        lo = bisect_left(sorted_lines, fn.start_line)
        hi = bisect_right(sorted_lines, fn.end_line)
        own = set(sorted_lines[lo:hi])
        for inner in ordered[i + 1 :]:
            if inner.start_line > fn.end_line:
                break
            if inner.end_line <= fn.end_line and (inner.start_line, inner.end_line) != (
                fn.start_line,
                fn.end_line,
            ):
                own.difference_update(range(inner.start_line, inner.end_line + 1))
        fn.loc = len(own)


def _make_qualnames_unique(module: Module) -> None:
    """Suffix repeats (`test("x")` twice, conditional redefinitions) with
    `#2`, `#3`, ... in source order, so delta can pair functions by name."""
    seen: dict[str, int] = {}
    for fn in module.all_functions():
        n = seen.get(fn.qualname, 0) + 1
        seen[fn.qualname] = n
        if n > 1:
            fn.qualname = f"{fn.qualname}#{n}"


class LanguageAdapter:
    """Turns source into a :class:`Module` and tells the metric walker what
    each node means. Subclasses implement ``_extract`` and fill ``roles``;
    override the hooks below for roles that depend on context."""

    language: ClassVar[str]
    extensions: ClassVar[tuple[str, ...]]
    comment_types: ClassVar[frozenset[str]] = frozenset({"comment"})
    roles: ClassVar[dict[str, Role]] = {}
    block_types: ClassVar[frozenset[str]] = frozenset({"block"})
    # nodes between two boolean expressions that don't break the sequence
    bool_transparent: ClassVar[frozenset[str]] = frozenset({"parenthesized_expression"})

    # --- metric hooks -------------------------------------------------------

    def role(self, node: Node) -> Role | None:
        return self.roles.get(node.type)

    def bool_token(self, node: Node) -> str | None:
        """'and' / 'or' if this node is a boolean operator token."""
        return None

    def is_recursive_call(self, node: Node, fn_name: str) -> bool:
        return False

    def statements(self, body: Node) -> list[Node]:
        """Statements directly inside a function/loop body (empty for
        expression bodies)."""
        block = body if body.type in self.block_types else None
        if block is None:
            block = next((c for c in body.named_children if c.type in self.block_types), None)
        if block is None:
            return []
        return [c for c in block.named_children if c.type not in self.comment_types]

    def unwrap_loop(self, stmt: Node) -> Node | None:
        """The body of ``stmt`` if it is a loop, else None."""
        if self.role(stmt) is not Role.LOOP:
            return None
        return stmt.child_by_field_name("body")

    def widget_depth(self, body: Node) -> int:
        return 0

    # --- module/class smell hooks ----------------------------------------------

    primitive_types: ClassVar[frozenset[str]] = frozenset()
    # flag types: neither primitive nor domain types for Primitive Obsession
    neutral_types: ClassVar[frozenset[str]] = frozenset({"bool"})
    # composite nodes treated as one token (and normalised) for duplication
    literal_types: ClassVar[frozenset[str]] = frozenset()

    def base_types(self, type_text: str) -> list[str] | None:
        """Base type names of a declared type, ignoring nullability and
        generics; None when the type is unknown (untyped / from a field)."""
        return None

    def is_primitive(self, type_text: str | None) -> bool | None:
        """True/False for a declared parameter type; None if unknown."""
        if type_text is None:
            return None
        bases = self.base_types(type_text)
        bases = [b for b in bases or [] if b not in self.neutral_types]
        if not bases:
            return None
        return all(b in self.primitive_types for b in bases)

    def member_refs(self, cls: Class) -> dict[str, set[str]]:
        """Per method qualname: names of the class's own members (fields and
        methods) it references. Used for LCOM4 cohesion."""
        return {}

    # --- parsing --------------------------------------------------------------

    def __init__(self) -> None:
        self._parser = get_parser(self.language)

    def parse(self, source: bytes, path: str = "<memory>") -> Module:
        root = self._parser.parse(source).root_node
        module = Module(path=path, language=self.language, source=source, root=root)
        module.error_count = count_errors(root)
        lines = code_lines(root, self._skip_for_loc)
        module.loc = len(lines)
        self._extract(module)
        _make_qualnames_unique(module)
        _assign_own_loc(module.all_functions(), lines)
        for cls in module.classes:
            cls.loc = count_in_span(lines, cls.start_line, cls.end_line)
        return module

    def _skip_for_loc(self, node: Node) -> bool:
        return node.type in self.comment_types

    def _extract(self, module: Module) -> None:
        raise NotImplementedError
