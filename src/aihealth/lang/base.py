"""Shared adapter machinery: parsing, code-line counting, error counting."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import ClassVar

from tree_sitter import Node
from tree_sitter_language_pack import get_parser

from aihealth.metrics import Role
from aihealth.model import Module


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


def count_errors(root: Node) -> int:
    if not root.has_error:
        return 0
    return sum(1 for n in iter_nodes(root) if n.type == "ERROR" or n.is_missing)


def count_in_span(lines: set[int], start: int, end: int) -> int:
    return sum(1 for ln in lines if start <= ln <= end)


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
        for fn in module.all_functions():
            fn.loc = count_in_span(lines, fn.start_line, fn.end_line)
        for cls in module.classes:
            cls.loc = count_in_span(lines, cls.start_line, cls.end_line)
        return module

    def _skip_for_loc(self, node: Node) -> bool:
        return node.type in self.comment_types

    def _extract(self, module: Module) -> None:
        raise NotImplementedError
