"""Language-neutral code model produced by the language adapters.

Metrics and smells (M3/M4) work only on these types plus the tree-sitter
nodes they carry, so they never need to know which language they are in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node


@dataclass
class Param:
    name: str
    type_text: str | None = None  # declared type as written, None if untyped
    named: bool = False  # Dart `{...}` named / Python keyword-only (after `*`) or **kwargs
    optional: bool = False  # has a default / Dart `[...]` / nullable named
    variadic: bool = False  # Python *args / **kwargs

    @property
    def typed(self) -> bool:
        return self.type_text is not None


@dataclass
class Function:
    name: str
    qualname: str  # "Class.method", "outer.inner", "func"; unique per module
    kind: str  # function | method | constructor | getter | setter | operator | test
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive
    params: list[Param]
    body: Node  # tree-sitter node of the body; metrics walk this
    loc: int = 0  # body code lines (no signature/blanks/comments/docstrings/nested fns)
    class_name: str | None = None


@dataclass
class Class:
    name: str
    kind: str  # class | mixin | extension | enum
    start_line: int
    end_line: int
    methods: list[Function] = field(default_factory=list)
    loc: int = 0
    node: Node | None = field(default=None, repr=False, compare=False)


@dataclass
class Module:
    path: str
    language: str
    source: bytes
    root: Node
    functions: list[Function] = field(default_factory=list)  # top-level + nested
    classes: list[Class] = field(default_factory=list)
    loc: int = 0
    error_count: int = 0  # tree-sitter ERROR/MISSING nodes (partial parse)

    def all_functions(self) -> list[Function]:
        """Every scorable function, ordered by position."""
        funcs = list(self.functions)
        for cls in self.classes:
            funcs.extend(cls.methods)
        return sorted(funcs, key=lambda f: (f.start_line, f.qualname))
