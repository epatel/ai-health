"""Python adapter (tree-sitter `python` grammar)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import ClassVar

from tree_sitter import Node

from aihealth.lang.base import ExtractionScope, LanguageAdapter, iter_nodes, text, type_leaves
from aihealth.metrics import Role
from aihealth.model import Class, Function, Module, Param

_PARAM_TYPED = {"typed_parameter", "typed_default_parameter"}
_PARAM_DEFAULT = {"default_parameter", "typed_default_parameter"}
_SPLATS = {"list_splat_pattern", "dictionary_splat_pattern"}


def _unwrap(node: Node) -> Node:
    """`@decorator def f` is a decorated_definition wrapping the def."""
    if node.type == "decorated_definition":
        inner = node.child_by_field_name("definition")
        if inner is not None:
            return inner
    return node


_STRINGS = ("string", "concatenated_string")


def _is_string_statement(node: Node) -> bool:
    """A lone string statement. Grammar versions differ: newer ones put a
    bare `string` in the block, older ones wrap it in `expression_statement`."""
    if node.type == "expression_statement":
        return node.named_child_count == 1 and node.named_children[0].type in _STRINGS
    return node.type in _STRINGS


def _holds_docstring(parent: Node | None) -> bool:
    """A module, or the body block of a function or class."""
    if parent is None or parent.type == "module":
        return parent is not None
    owner = parent.parent if parent.type == "block" else None
    return owner is not None and owner.type in ("function_definition", "class_definition")


def _is_docstring(node: Node) -> bool:
    """A string that is the first statement of a module, class or function body."""
    if not _is_string_statement(node) or not _holds_docstring(node.parent):
        return False
    first = next((c for c in node.parent.named_children if c.type != "comment"), None)
    return first is not None and first.id == node.id


def _param_name(child: Node) -> tuple[str, str | None]:
    """(name, splat node type or None) of one parameter node."""
    if child.type == "identifier":
        return text(child), None
    if child.type in _SPLATS:
        return text(child).lstrip("*"), child.type
    if child.type == "typed_parameter":  # first named child: identifier or splat
        first = child.named_children[0]
        return text(first).lstrip("*"), first.type if first.type in _SPLATS else None
    return text(child.child_by_field_name("name")), None


def _is_bool_keyword(node: Node) -> bool:
    """The `and`/`or` token of a boolean_operator."""
    in_bool_op = node.parent is not None and node.parent.type == "boolean_operator"
    return node.type in ("and", "or") and in_bool_op


def _is_self(node: Node | None) -> bool:
    return node is not None and node.type == "identifier" and text(node) in ("self", "cls")


def _self_attr(node: Node) -> str | None:
    """`x` for an attribute node `self.x` / `cls.x`, else None."""
    if node.type != "attribute" or not _is_self(node.child_by_field_name("object")):
        return None
    return text(node.child_by_field_name("attribute"))


def _self_attrs_in(body: Node | None) -> set[str]:
    names: set[str] = set()
    stack = list(body.children) if body is not None else []
    while stack:
        n = stack.pop()
        if n.type in ("function_definition", "class_definition"):
            continue
        attr = _self_attr(n)
        if attr:
            names.add(attr)
        stack.extend(n.children)
    return names


def _assigned_self_attributes(cls: Class) -> set[str]:
    """Attributes that are assignment targets (`self.x = ...`, `self.x += ...`)."""
    found: set[str] = set()
    for fn in cls.methods:
        for n in iter_nodes(fn.body):
            if n.type in ("assignment", "augmented_assignment"):
                left = n.child_by_field_name("left")
                found.update(a for t in iter_nodes(left) if (a := _self_attr(t)))
    return found


def _param(child: Node, keyword_only: bool) -> tuple[Param, str | None]:
    """One parameter, and its splat node type (or None)."""
    name, splat = _param_name(child)
    type_node = child.child_by_field_name("type") if child.type in _PARAM_TYPED else None
    param = Param(
        name=name,
        type_text=text(type_node) if type_node else None,
        named=keyword_only or splat == "dictionary_splat_pattern",
        optional=child.type in _PARAM_DEFAULT,
        variadic=splat is not None,
    )
    return param, splat


def _starts_with_self(params: list[Param]) -> bool:
    """An untyped leading `self`/`cls`, which callers never pass."""
    return bool(params) and params[0].name in ("self", "cls") and not params[0].typed


_SEPARATORS = ("keyword_separator", "positional_separator", "comment")


def _params(params_node: Node | None, is_method: bool) -> list[Param]:
    if params_node is None:
        return []
    out: list[Param] = []
    keyword_only = False  # after `*` or `*args`: named at every call site
    for child in params_node.named_children:
        keyword_only = keyword_only or child.type == "keyword_separator"
        if child.type in _SEPARATORS:
            continue
        param, splat = _param(child, keyword_only)
        out.append(param)
        keyword_only = keyword_only or splat == "list_splat_pattern"
    return out[1:] if is_method and _starts_with_self(out) else out


_COMPOUND = ("if_statement", "try_statement", "with_statement")


def _compound_bodies(node: Node) -> Iterator[Node]:
    """The blocks of an if/try/with statement, in order: its own block and
    those of its elif/else/except/finally clauses."""
    for sub in node.named_children:
        if sub.type == "block":
            yield sub
            continue
        body = sub.child_by_field_name("body") or next(
            (c for c in sub.named_children if c.type == "block"), None
        )
        if body is not None:
            yield body


def _function_kind(name: str, in_class: bool) -> str:
    if not in_class:
        return "function"
    return "constructor" if name == "__init__" else "method"


class PythonAdapter(LanguageAdapter):
    language = "python"
    extensions = (".py", ".pyi")
    roles: ClassVar[dict[str, Role]] = {
        "if_statement": Role.IF,
        "elif_clause": Role.IF,  # only reached as an `alternative` -> else-if
        "for_statement": Role.LOOP,
        "while_statement": Role.LOOP,
        "match_statement": Role.SWITCH,
        "case_clause": Role.CASE,
        "except_clause": Role.CATCH,
        "except_group_clause": Role.CATCH,
        "conditional_expression": Role.TERNARY,
        "lambda": Role.LAMBDA,
        "function_definition": Role.STOP,
        "class_definition": Role.STOP,
        "decorated_definition": Role.STOP,
        "for_in_clause": Role.COMPREHENSION,
        "if_clause": Role.COMPREHENSION,
        "boolean_operator": Role.BOOL_EXPR,
    }
    bool_transparent = frozenset({"parenthesized_expression", "not_operator"})

    def role(self, node: Node) -> Role | None:
        r = self.roles.get(node.type)
        if r is Role.CASE:
            pattern = node.named_children[0] if node.named_child_count else None
            if pattern is not None and text(pattern).strip() == "_":
                return None  # `case _:` is the default arm
        return r

    def bool_token(self, node: Node) -> str | None:
        return node.type if _is_bool_keyword(node) else None

    def is_recursive_call(self, node: Node, fn_name: str) -> bool:
        if node.type != "call":
            return False
        target = node.child_by_field_name("function")
        if target is None:
            return False
        if target.type == "identifier":
            return text(target) == fn_name
        if target.type == "attribute":
            obj = target.child_by_field_name("object")
            attr = target.child_by_field_name("attribute")
            return text(obj) in ("self", "cls") and text(attr) == fn_name
        return False

    primitive_types = frozenset(
        {
            "int",
            "float",
            "complex",
            "str",
            "bytes",
            "bytearray",
            "dict",
            "list",
            "tuple",
            "set",
            "frozenset",
            "Dict",
            "List",
            "Tuple",
            "Set",
            "FrozenSet",
            "Any",
            "object",
        }
    )
    literal_types = frozenset(
        {"string", "concatenated_string", "integer", "float", "true", "false", "none"}
    )

    def base_types(self, type_text: str) -> list[str] | None:
        t = type_text.strip().strip("'\"")
        if "Callable" in t:
            return None  # callbacks are not primitive obsession
        leaves = [x.strip("'\"").removeprefix("typing.") for x in type_leaves(t, "[", "]")]
        return leaves or None

    def member_refs(self, cls: Class) -> dict[str, set[str]]:
        """Per method: the instance fields (attributes assigned as `self.x = ...`
        somewhere in the class) and methods it uses through `self`/`cls`.
        Class constants read via `self.X` are not shared *state*, so they don't
        tie methods together."""
        raw = self._self_attributes(cls)
        state = _assigned_self_attributes(cls) | {m.name for m in cls.methods}
        return {q: names & state for q, names in raw.items()}

    def _self_attributes(self, cls: Class) -> dict[str, set[str]]:
        """`self.x` / `cls.x` names used per method (nested defs excluded)."""
        return {fn.qualname: _self_attrs_in(fn.body) for fn in cls.methods}

    def _skip_for_loc(self, node: Node) -> bool:
        return node.type == "comment" or _is_docstring(node)

    def _extract(self, module: Module) -> None:
        self._walk_block(ExtractionScope(module), module.root)

    def _walk_block(self, scope: ExtractionScope, block: Node) -> None:
        for raw in block.named_children:
            self._add_statement(scope, raw)

    def _add_statement(self, scope: ExtractionScope, raw: Node) -> None:
        node = _unwrap(raw)
        if node.type == "function_definition":
            self._add_function(scope, node, raw)
        elif node.type == "class_definition":
            self._add_class(scope, node, raw)
        elif node.type in _COMPOUND:
            # defs under `if TYPE_CHECKING:` / try-import fallbacks
            for body in _compound_bodies(node):
                self._walk_block(scope, body)

    def _add_function(self, scope: ExtractionScope, node: Node, outer: Node) -> None:
        name = text(node.child_by_field_name("name"))
        qualname = f"{scope.prefix}{name}"
        body = node.child_by_field_name("body")
        cls = scope.cls
        scope.add(
            Function(
                name=name,
                qualname=qualname,
                kind=_function_kind(name, in_class=cls is not None),
                start_line=outer.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                params=_params(node.child_by_field_name("parameters"), cls is not None),
                body=body,
                class_name=cls.name if cls else None,
            )
        )
        if body is not None:
            # nested defs become their own functions; nested classes too
            self._walk_block(scope.nested(qualname), body)

    def _add_class(self, scope: ExtractionScope, node: Node, outer: Node) -> None:
        name = text(node.child_by_field_name("name"))
        cls = Class(
            name=f"{scope.prefix}{name}",
            kind="class",
            start_line=outer.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            node=node,
        )
        scope.module.classes.append(cls)
        body = node.child_by_field_name("body")
        if body is not None:
            self._walk_block(scope.in_class(cls), body)
