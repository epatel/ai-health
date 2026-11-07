"""Rust adapter (tree-sitter `rust` grammar).

Grammar notes (see cards/parser-choice.md):
- an else-if is `else_clause > if_expression`, so `else_if()` unwraps it;
- `let PAT = EXPR else { ... }` is a `let_declaration` with an
  `alternative` block: scored like Swift `guard` (one IF, no else);
- statements are mostly `expression_statement` wrappers around if/loops;
- methods live in `impl_item` / `trait_item` blocks, not in the struct.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import ClassVar

from tree_sitter import Node

from aihealth.lang.base import (
    ExtractionScope,
    LanguageAdapter,
    split_top,
    text,
    type_leaves,
    walk_units,
)
from aihealth.metrics import Role
from aihealth.model import Class, Function, Module, Param

# items whose body holds functions, with the Class kind they become
_CONTAINERS = {"impl_item": "impl", "trait_item": "trait"}

# nested items that are scored on their own (or not at all), never inline
_ITEMS = frozenset(
    {"function_item", "impl_item", "trait_item", "mod_item", "struct_item", "enum_item"}
)


def _child(node: Node | None, *types: str) -> Node | None:
    """First named child of one of ``types``."""
    if node is None:
        return None
    return next((c for c in node.named_children if c.type in types), None)


def _line(node: Node) -> int:
    return node.start_point[0] + 1


def _end_line(node: Node) -> int:
    return node.end_point[0] + 1


def _base_name(type_node: Node | None) -> str:
    """`Cache` for `Cache<S>`, `Display` for `fmt::Display`."""
    if type_node is None:
        return "<impl>"
    name = _strip_ref(text(type_node)).split("<", 1)[0]  # `&mut Foo<T>` -> `Foo`
    return name.rsplit("::", 1)[-1].strip() or "<impl>"


_CFG_TEST = re.compile(r"#\[\s*cfg\s*\(\s*(all\s*\(\s*)?test\b")


def _is_test_module(node: Node) -> bool:
    """A `mod` item under `#[cfg(test)]` (or `#[cfg(all(test, ...))]`)."""
    if node.type != "mod_item":
        return False
    prev = node.prev_named_sibling
    while prev is not None and prev.type in ("attribute_item", "line_comment", "block_comment"):
        if prev.type == "attribute_item" and _CFG_TEST.match(text(prev)):
            return True
        prev = prev.prev_named_sibling
    return False


def _container_name(node: Node) -> str:
    if node.type == "trait_item":
        return text(node.child_by_field_name("name"))
    return _base_name(node.child_by_field_name("type"))


# --- parameters ---------------------------------------------------------------------


def _params(fn: Node) -> list[Param]:
    """Parameters callers pass: `self` is implicit and not counted."""
    plist = fn.child_by_field_name("parameters")
    if plist is None:
        return []
    out = []
    for p in plist.named_children:
        if p.type == "parameter":
            type_node = p.child_by_field_name("type")
            out.append(
                Param(
                    name=text(p.child_by_field_name("pattern")),
                    type_text=text(type_node) if type_node is not None else None,
                )
            )
        elif p.type == "variadic_parameter":
            out.append(Param(name="...", variadic=True))
    return out


# --- roles that depend on context -------------------------------------------------------


def _binary_role(node: Node) -> Role | None:
    op = node.child_by_field_name("operator")
    return Role.BOOL_EXPR if op is not None and op.type in ("&&", "||") else None


def _let_role(node: Node) -> Role | None:
    """`let Some(x) = y else { return }` branches like Swift `guard`."""
    return Role.IF if node.child_by_field_name("alternative") is not None else None


def _arm_role(node: Node) -> Role | None:
    pattern = node.child_by_field_name("pattern")
    is_wildcard = pattern is not None and text(pattern).strip() == "_"
    return None if is_wildcard else Role.CASE


def _jump_role(node: Node) -> Role | None:
    """`break 'outer` / `continue 'outer`; unlabeled jumps add nothing."""
    return Role.JUMP_LABEL if _child(node, "label") is not None else None


_CONTEXT_ROLES = {
    "binary_expression": _binary_role,
    "let_declaration": _let_role,
    "match_arm": _arm_role,
    "break_expression": _jump_role,
    "continue_expression": _jump_role,
}


# --- types ----------------------------------------------------------------------------

_CALLABLE_PREFIXES = ("impl ", "fn(", "Fn(", "FnMut(", "FnOnce(", "dyn Fn")


def _strip_ref(t: str) -> str:
    """`&'a mut T` -> `T`."""
    t = t.strip()
    while t.startswith("&"):
        t = t[1:].lstrip()
        if t.startswith("'"):
            t = t.partition(" ")[2]
        t = t.removeprefix("mut ").lstrip()
    return t.removeprefix("dyn ").strip() if not t.startswith("dyn Fn") else t


def _type_leaves(t: str) -> list[str] | None:
    """Innermost type names: `Vec<Order>` -> [Order], `&[u8]` -> [u8],
    `HashMap<String, u32>` -> [String, u32]; closures, `impl Trait` and
    tuples are unknown."""
    t = _strip_ref(t)
    if t.startswith(_CALLABLE_PREFIXES) or t.startswith("("):
        return None
    if t.startswith("[") and t.endswith("]"):
        return _type_leaves(split_top(t[1:-1], ";", "[", "]")[0])
    leaves = [x.rsplit("::", 1)[-1] for x in type_leaves(t, "<", ">")]
    return [x for x in leaves if not x.startswith("'")]


class RustAdapter(LanguageAdapter):
    language = "rust"
    extensions = (".rs",)
    comment_types = frozenset({"line_comment", "block_comment"})
    roles: ClassVar[dict[str, Role]] = {
        "if_expression": Role.IF,
        "for_expression": Role.LOOP,
        "while_expression": Role.LOOP,
        "loop_expression": Role.LOOP,
        "match_expression": Role.SWITCH,
        "closure_expression": Role.LAMBDA,
        "let_chain": Role.BOOL_EXPR,
        **dict.fromkeys(_ITEMS, Role.STOP),
    }
    bool_transparent = frozenset({"parenthesized_expression", "unary_expression"})
    primitive_types = frozenset(
        {
            *(f"{s}{n}" for s in "iu" for n in (8, 16, 32, 64, 128, "size")),
            "f32",
            "f64",
            "char",
            "str",
            "String",
            "Vec",
            "HashMap",
            "HashSet",
            "BTreeMap",
            "BTreeSet",
            "VecDeque",
        }
    )
    literal_types = frozenset(
        {
            "string_literal",
            "raw_string_literal",
            "char_literal",
            "integer_literal",
            "float_literal",
            "boolean_literal",
        }
    )

    def __init__(self, score_test_modules: bool = False) -> None:
        super().__init__()
        # Unit tests live in the file they test (`#[cfg(test)] mod tests`).
        # By default they are left out (units, LoC, duplication), so a file is
        # judged on its production code as in languages with separate test
        # files (see code-health-model). Integration tests in tests/ are scored.
        self.score_test_modules = score_test_modules

    def _skipped(self, node: Node) -> bool:
        return not self.score_test_modules and _is_test_module(node)

    def _skip_for_loc(self, node: Node) -> bool:
        return node.type in self.comment_types or self._skipped(node)

    def tokens(self, root: Node) -> Iterator[tuple[str, int]]:
        """Duplication tokens without those of skipped test modules."""
        skipped = {
            line
            for n in root.named_children
            if self._skipped(n)
            for line in range(_line(n), _end_line(n) + 1)
        }
        return ((key, line) for key, line in super().tokens(root) if line not in skipped)

    # --- metric hooks --------------------------------------------------------------------

    def role(self, node: Node) -> Role | None:
        contextual = _CONTEXT_ROLES.get(node.type)
        return contextual(node) if contextual is not None else self.roles.get(node.type)

    def is_alternative(self, node: Node, index: int) -> bool:
        # a let-else block is the guard's body, not an else branch
        return node.type == "if_expression" and super().is_alternative(node, index)

    def else_if(self, node: Node) -> Node | None:
        return _child(node, "if_expression") if node.type == "else_clause" else None

    def bool_token(self, node: Node) -> str | None:
        return {"&&": "and", "||": "or"}.get(node.type)

    def is_recursive_call(self, node: Node, fn_name: str) -> bool:
        """`f(..)`, `Self::f(..)` or `self.f(..)`."""
        if node.type != "call_expression":
            return False
        target = node.child_by_field_name("function")
        if target is None:
            return False
        if target.type == "identifier":
            return text(target) == fn_name
        return text(target) in (f"Self::{fn_name}", f"self.{fn_name}")

    def unwrap_loop(self, stmt: Node) -> Node | None:
        if stmt.type == "expression_statement" and stmt.named_child_count == 1:
            stmt = stmt.named_children[0]
        return super().unwrap_loop(stmt)

    # --- module/class smell hooks --------------------------------------------------------

    def base_types(self, type_text: str) -> list[str] | None:
        return _type_leaves(type_text) or None

    def member_refs(self, cls: Class) -> dict[str, set[str]]:
        """`self.x` fields and `self.m()` calls per method. In Rust anything
        reached through `self` is the type's own state or method. Traits
        have no state, and builder setters (`fn x(mut self, ..) -> Self`)
        each touch one field by design: neither is judged for cohesion."""
        if cls.kind == "trait":
            return {}
        return {
            fn.qualname: set() if _is_builder(fn) else _self_members(fn.body) for fn in cls.methods
        }

    # --- extraction ---------------------------------------------------------------------

    def _extract(self, module: Module) -> None:
        self._scan_items(ExtractionScope(module), module.root)

    def _scan_items(self, scope: ExtractionScope, parent: Node) -> None:
        for node in parent.named_children:
            self._add_item(scope, node)

    def _add_item(self, scope: ExtractionScope, node: Node) -> None:
        if node.type == "function_item":
            self._add_function(scope, node)
        elif node.type in _CONTAINERS:
            self._add_container(scope, node)
        elif node.type == "mod_item" and not self._skipped(node):
            body = node.child_by_field_name("body")
            if body is not None:
                name = text(node.child_by_field_name("name"))
                self._scan_items(scope.nested(f"{scope.prefix}{name}"), body)

    def _add_container(self, scope: ExtractionScope, node: Node) -> None:
        cls = Class(
            name=f"{scope.prefix}{_container_name(node)}",
            kind=_CONTAINERS[node.type],
            start_line=_line(node),
            end_line=_end_line(node),
            node=node,
        )
        scope.module.classes.append(cls)
        body = node.child_by_field_name("body")
        if body is not None:
            self._scan_items(ExtractionScope(scope.module, cls, f"{cls.name}."), body)

    def _add_function(self, scope: ExtractionScope, node: Node) -> None:
        body = node.child_by_field_name("body")
        if body is None:
            return  # a trait method without a default body
        name = text(node.child_by_field_name("name"))
        fn = Function(
            name=name,
            qualname=f"{scope.prefix}{name}",
            kind="method" if scope.cls is not None else "function",
            start_line=_line(node),
            end_line=_end_line(node),
            params=_params(node),
            body=body,
            class_name=scope.cls.name if scope.cls else None,
        )
        scope.add(fn)
        nested = scope.nested(fn.qualname)
        walk_units(body, lambda n: self._add_nested_item(nested, n))

    def _add_nested_item(self, scope: ExtractionScope, node: Node) -> bool:
        """Items inside a function body are scored on their own."""
        if node.type not in _ITEMS:
            return False
        self._add_item(scope, node)
        return True


def _is_builder(fn: Function) -> bool:
    """Takes `self` by value and returns `Self`: a builder setter."""
    item = fn.body.parent
    plist = item.child_by_field_name("parameters") if item is not None else None
    receiver = _child(plist, "self_parameter")
    by_value = receiver is not None and "&" not in text(receiver)
    returns = item.child_by_field_name("return_type") if item is not None else None
    return by_value and text(returns) == "Self"


def _self_members(body: Node) -> set[str]:
    names: set[str] = set()
    stack = list(body.children)
    while stack:
        n = stack.pop()
        if n.type in _ITEMS:
            continue
        field = _self_field(n)
        if field:
            names.add(field)
        stack.extend(n.children)
    return names


def _self_field(node: Node) -> str | None:
    """`x` for `self.x` (a field, or a method when called), else None."""
    if node.type != "field_expression":
        return None
    value = node.child_by_field_name("value")
    if value is None or value.type != "self":
        return None
    return text(node.child_by_field_name("field")) or None
