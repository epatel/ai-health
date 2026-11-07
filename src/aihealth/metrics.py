"""Language-neutral function metrics.

Walks a function body using the adapter's node roles (see ``Role``) and
computes:

- cyclomatic complexity: 1 + decision points + boolean operators
- cognitive complexity (SonarSource spec): structural increments with
  nesting penalty, +1 per sequence of like boolean operators, +1 for
  labeled jumps and recursion; closures raise nesting without increment
- max nesting depth of control structures (if/loop/switch/catch); closures
  and ternaries do not deepen it, else-if stays at the depth of its if
- bumps (Bumpy Road): top-level statements whose own nesting depth >= 2,
  looking through a body that is a single loop
- boolean expressions with their operator counts (complex conditionals)
- widget-tree depth (adapter-provided, Dart only)

Nested named functions (adapter ``STOP`` role) are scored separately and
are not entered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from itertools import pairwise
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from tree_sitter import Node

    from aihealth.lang.base import LanguageAdapter
    from aihealth.model import Function

BUMP_MIN_DEPTH = 2


class Role(Enum):
    IF = auto()  # if / elif / collection-if; else-if detected via `alternative`
    LOOP = auto()
    SWITCH = auto()
    CASE = auto()  # non-default case arm
    CATCH = auto()  # exception handler body
    TERNARY = auto()
    LAMBDA = auto()  # closure: nesting +1 for cognitive, nothing else
    STOP = auto()  # nested named function/class: scored on its own
    COMPREHENSION = auto()  # Python comprehension for/if: cyclomatic only
    JUMP_LABEL = auto()  # break/continue to a label
    BOOL_EXPR = auto()  # boolean and/or expression node


@dataclass
class Bump:
    start_line: int
    end_line: int
    depth: int


@dataclass
class BoolExpr:
    line: int
    operators: int
    # used as an if/elif/while/ternary/guard condition (not a value such as
    # `return a or b`, a default chain, or an assert)
    is_condition: bool = False


@dataclass
class FunctionMetrics:
    qualname: str
    start_line: int
    loc: int
    cyclomatic: int = 1
    cognitive: int = 0
    max_nesting: int = 0
    nesting_line: int | None = None  # line of the deepest construct
    bumps: list[Bump] = field(default_factory=list)
    bool_exprs: list[BoolExpr] = field(default_factory=list)
    args: int = 0
    named_args: int = 0
    widget_depth: int = 0

    @property
    def max_condition_ops(self) -> int:
        return max((b.operators for b in self.bool_exprs), default=0)


# --- traversal --------------------------------------------------------------

_NORMAL, _ELSE_IF, _ELSE = 0, 1, 2


@dataclass
class _Totals:
    cyclomatic: int = 1
    cognitive: int = 0
    max_nesting: int = 0
    nesting_line: int | None = None
    bool_exprs: list[BoolExpr] = field(default_factory=list)


class _Frame(NamedTuple):
    node: Node
    level: int  # cognitive nesting level (closures raise it too)
    depth: int  # control-structure nesting depth (max_nesting)
    mode: int = _NORMAL  # _ELSE_IF / _ELSE when reached as an if's alternative


class _Walker:
    """One pre-order pass over a function body. Each node's role picks a
    handler (see ``_HANDLERS``); a handler updates the totals and pushes the
    children with the nesting they get."""

    def __init__(self, root: Node, adapter: LanguageAdapter, fn_name: str | None) -> None:
        self.root = root
        self.adapter = adapter
        self.fn_name = fn_name
        self.t = _Totals()
        self.stack = [_Frame(root, 0, 0)]

    def run(self) -> _Totals:
        while self.stack:
            f = self.stack.pop()
            if f.mode == _ELSE:
                self._else(f)
                continue
            role = self.adapter.role(f.node)
            if role is Role.STOP and f.node is not self.root:
                continue  # nested named function: scored on its own
            _HANDLERS.get(role, _Walker._plain)(self, f)
        return self.t

    # --- helpers -------------------------------------------------------------

    def _deepen(self, node: Node, depth: int) -> int:
        d = depth + 1
        if d > self.t.max_nesting:
            self.t.max_nesting, self.t.nesting_line = d, node.start_point[0] + 1
        return d

    def _push_children(self, node: Node, level: int, depth: int) -> None:
        self.stack.extend(_Frame(c, level, depth) for c in reversed(node.children))

    def _nested_structure(self, f: _Frame, *, branches: bool) -> None:
        """+1 + nesting level; children one level deeper in both senses."""
        self.t.cyclomatic += branches
        self.t.cognitive += 1 + f.level
        self._push_children(f.node, f.level + 1, self._deepen(f.node, f.depth))

    # --- handlers, one per role ---------------------------------------------

    def _if(self, f: _Frame) -> None:
        self.t.cyclomatic += 1
        self.t.cognitive += 1 if f.mode == _ELSE_IF else 1 + f.level
        d = self._deepen(f.node, f.depth)
        for i in reversed(range(f.node.child_count)):
            self.stack.append(self._if_child(f, i, d))

    def _if_child(self, f: _Frame, i: int, d: int) -> _Frame:
        """else-if keeps the if's level and depth; else and the if's own
        contents sit one level inside it."""
        child = f.node.children[i]
        if not self.adapter.is_alternative(f.node, i):
            return _Frame(child, f.level + 1, d)
        inner = self.adapter.else_if(child)
        if inner is None and self.adapter.role(child) is Role.IF:
            inner = child
        if inner is not None:
            return _Frame(inner, f.level, f.depth, _ELSE_IF)
        return _Frame(child, f.level, d, _ELSE)

    def _else(self, f: _Frame) -> None:
        self.t.cognitive += 1
        self._push_children(f.node, f.level + 1, f.depth)

    def _loop_or_catch(self, f: _Frame) -> None:
        self._nested_structure(f, branches=True)

    def _switch(self, f: _Frame) -> None:
        self._nested_structure(f, branches=False)  # its case arms add the branches

    def _ternary(self, f: _Frame) -> None:
        self.t.cyclomatic += 1
        self.t.cognitive += 1 + f.level
        self._push_children(f.node, f.level + 1, f.depth)  # no depth: an expression

    def _lambda(self, f: _Frame) -> None:
        self._push_children(f.node, f.level + 1, f.depth)

    def _branch(self, f: _Frame) -> None:
        """Case arm or comprehension for/if: a path, no nesting."""
        self.t.cyclomatic += 1
        self._push_children(f.node, f.level, f.depth)

    def _jump_label(self, f: _Frame) -> None:
        self.t.cognitive += 1
        self._push_children(f.node, f.level, f.depth)

    def _bool_expr(self, f: _Frame) -> None:
        if _is_bool_root(f.node, self.adapter):
            self._record_bool_expr(f.node)
        self._push_children(f.node, f.level, f.depth)

    def _record_bool_expr(self, node: Node) -> None:
        ops = _bool_operators(node, self.adapter)
        if not ops:
            return
        self.t.cyclomatic += len(ops)
        self.t.cognitive += 1 + sum(1 for a, b in pairwise(ops) if a != b)
        is_condition = _is_condition(node, self.adapter)
        self.t.bool_exprs.append(BoolExpr(node.start_point[0] + 1, len(ops), is_condition))

    def _plain(self, f: _Frame) -> None:
        if self.fn_name and self.adapter.is_recursive_call(f.node, self.fn_name):
            self.t.cognitive += 1
        self._push_children(f.node, f.level, f.depth)


_HANDLERS = {
    Role.IF: _Walker._if,
    Role.LOOP: _Walker._loop_or_catch,
    Role.CATCH: _Walker._loop_or_catch,
    Role.SWITCH: _Walker._switch,
    Role.TERNARY: _Walker._ternary,
    Role.LAMBDA: _Walker._lambda,
    Role.CASE: _Walker._branch,
    Role.COMPREHENSION: _Walker._branch,
    Role.JUMP_LABEL: _Walker._jump_label,
    Role.BOOL_EXPR: _Walker._bool_expr,
}


def _walk(root: Node, adapter: LanguageAdapter, fn_name: str | None) -> _Totals:
    return _Walker(root, adapter, fn_name).run()


def _is_bool_root(node: Node, adapter: LanguageAdapter) -> bool:
    parent = node.parent
    while parent is not None and parent.type in adapter.bool_transparent:
        parent = parent.parent
    return parent is None or adapter.role(parent) is not Role.BOOL_EXPR


_CONDITION_OWNERS = (Role.IF, Role.LOOP, Role.TERNARY, Role.COMPREHENSION)


def _is_condition(node: Node, adapter: LanguageAdapter) -> bool:
    """True when the boolean expression is the condition of a branch or loop
    (parentheses and `not` between them are looked through). Where the
    grammar names the condition (`condition` field) only that child counts:
    in bash, statements of an if's body are also its direct children."""
    child, parent = node, node.parent
    while parent is not None and parent.type in adapter.bool_transparent:
        child, parent = parent, parent.parent
    if parent is None or adapter.role(parent) not in _CONDITION_OWNERS:
        return False
    named = parent.child_by_field_name("condition")
    return named is None or any(
        c.id == child.id for c in parent.children_by_field_name("condition")
    )


def _bool_operators(root: Node, adapter: LanguageAdapter) -> list[str]:
    """Boolean operator kinds ('and'/'or') in source order, not entering closures."""
    ops: list[tuple[int, str]] = []
    stack = [root]
    while stack:
        n = stack.pop()
        if n is not root and adapter.role(n) in (Role.LAMBDA, Role.STOP):
            continue
        kind = adapter.bool_token(n)
        if kind:
            ops.append((n.start_byte, kind))
        stack.extend(n.children)
    return [k for _, k in sorted(ops)]


# --- bumps ------------------------------------------------------------------


def _bump_statements(body: Node, adapter: LanguageAdapter) -> list[Node]:
    """Top-level statements; if the body is a single loop, its statements."""
    stmts = adapter.statements(body)
    for _ in range(4):  # look through a few levels of single-loop wrapping
        if len(stmts) != 1:
            break
        inner = adapter.unwrap_loop(stmts[0])
        if inner is None:
            break
        stmts = adapter.statements(inner)
    return stmts


def _bumps(body: Node, adapter: LanguageAdapter) -> list[Bump]:
    out = []
    for stmt in _bump_statements(body, adapter):
        depth = _walk(stmt, adapter, None).max_nesting
        if depth >= BUMP_MIN_DEPTH:
            out.append(Bump(stmt.start_point[0] + 1, stmt.end_point[0] + 1, depth))
    return out


# --- public API -------------------------------------------------------------


def function_metrics(fn: Function, adapter: LanguageAdapter) -> FunctionMetrics:
    t = _walk(fn.body, adapter, fn.name)
    return FunctionMetrics(
        qualname=fn.qualname,
        start_line=fn.start_line,
        loc=fn.loc,
        cyclomatic=t.cyclomatic,
        cognitive=t.cognitive,
        max_nesting=t.max_nesting,
        nesting_line=t.nesting_line,
        bumps=_bumps(fn.body, adapter),
        bool_exprs=t.bool_exprs,
        args=len(fn.params),
        named_args=sum(1 for p in fn.params if p.named),
        widget_depth=adapter.widget_depth(fn.body),
    )
