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
from typing import TYPE_CHECKING

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


def _walk(root: Node, adapter: LanguageAdapter, fn_name: str | None) -> _Totals:
    t = _Totals()
    stack: list[tuple[Node, int, int, int]] = [(root, 0, 0, _NORMAL)]

    def deepen(node: Node, depth: int) -> int:
        d = depth + 1
        if d > t.max_nesting:
            t.max_nesting, t.nesting_line = d, node.start_point[0] + 1
        return d

    def push_children(node: Node, level: int, depth: int) -> None:
        stack.extend((c, level, depth, _NORMAL) for c in reversed(node.children))

    while stack:
        node, level, depth, mode = stack.pop()

        if mode == _ELSE:  # else branch: +1, contents nested under the if
            t.cognitive += 1
            push_children(node, level + 1, depth)
            continue

        role = adapter.role(node)
        if role is Role.STOP and node is not root:
            continue

        if role is Role.IF:
            t.cyclomatic += 1
            t.cognitive += 1 if mode == _ELSE_IF else 1 + level
            d = deepen(node, depth)
            for i in reversed(range(node.child_count)):
                child = node.children[i]
                if node.field_name_for_child(i) == "alternative":
                    if adapter.role(child) is Role.IF:
                        stack.append((child, level, depth, _ELSE_IF))
                    else:
                        stack.append((child, level, d, _ELSE))
                else:
                    stack.append((child, level + 1, d, _NORMAL))
            continue

        if role in (Role.LOOP, Role.SWITCH, Role.CATCH):
            if role is not Role.SWITCH:
                t.cyclomatic += 1
            t.cognitive += 1 + level
            push_children(node, level + 1, deepen(node, depth))
            continue

        if role is Role.TERNARY:
            t.cyclomatic += 1
            t.cognitive += 1 + level
            push_children(node, level + 1, depth)
            continue

        if role is Role.LAMBDA:
            push_children(node, level + 1, depth)
            continue

        if role in (Role.CASE, Role.COMPREHENSION):
            t.cyclomatic += 1
        elif role is Role.JUMP_LABEL:
            t.cognitive += 1
        elif role is Role.BOOL_EXPR and _is_bool_root(node, adapter):
            ops = _bool_operators(node, adapter)
            if ops:
                t.cyclomatic += len(ops)
                t.cognitive += 1 + sum(1 for a, b in pairwise(ops) if a != b)
                t.bool_exprs.append(
                    BoolExpr(node.start_point[0] + 1, len(ops), _is_condition(node, adapter))
                )
        elif fn_name and adapter.is_recursive_call(node, fn_name):
            t.cognitive += 1

        push_children(node, level, depth)

    return t


def _is_bool_root(node: Node, adapter: LanguageAdapter) -> bool:
    parent = node.parent
    while parent is not None and parent.type in adapter.bool_transparent:
        parent = parent.parent
    return parent is None or adapter.role(parent) is not Role.BOOL_EXPR


_CONDITION_OWNERS = (Role.IF, Role.LOOP, Role.TERNARY, Role.COMPREHENSION)


def _is_condition(node: Node, adapter: LanguageAdapter) -> bool:
    """True when the boolean expression is the condition of a branch or loop
    (parentheses and `not` between them are looked through)."""
    parent = node.parent
    while parent is not None and parent.type in adapter.bool_transparent:
        parent = parent.parent
    return parent is not None and adapter.role(parent) in _CONDITION_OWNERS


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
