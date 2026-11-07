"""TypeScript adapters (tree-sitter `typescript` and `tsx` grammars).

Grammar notes (see cards/parser-choice.md):
- an else-if is `else_clause > if_statement`, unwrapped by `else_if()`;
- `switch_default` is the default arm; `catch_clause` / `finally_clause`
  are fields of `try_statement`;
- functions come in many shapes. Named units: declarations, class members,
  functions assigned to a name (`const f = () => {}`, class fields, methods
  of a named object literal) and test closures (`describe`, `it`, ...).
  Every other function expression is an inline callback (a closure).
"""

from __future__ import annotations

from typing import ClassVar

from tree_sitter import Node

from aihealth.lang.base import (
    ExtractionScope,
    LanguageAdapter,
    text,
    type_leaves,
    walk_units,
)
from aihealth.metrics import Role
from aihealth.model import Class, Function, Module, Param

DEFAULT_TEST_CALLS = frozenset(
    {
        "describe",
        "context",
        "suite",
        "it",
        "test",
        "beforeEach",
        "afterEach",
        "beforeAll",
        "afterAll",
    }
)

_DECLARATIONS = ("function_declaration", "generator_function_declaration")
_FUNCTION_VALUES = ("arrow_function", "function_expression", "generator_function")
_CLASSES = ("class_declaration", "abstract_class_declaration")
_PARAMS = ("required_parameter", "optional_parameter")


def _line(node: Node) -> int:
    return node.start_point[0] + 1


def _end_line(node: Node) -> int:
    return node.end_point[0] + 1


def _parent_type(node: Node) -> str | None:
    return node.parent.type if node.parent is not None else None


def _name_of(node: Node | None) -> str:
    return text(node.child_by_field_name("name")) if node is not None else ""


# --- which function nodes are named units -----------------------------------------------


def _object_owner(obj: Node | None) -> str | None:
    """`api` for an object literal in `const api = {...}`, else None."""
    if obj is None or obj.type != "object":
        return None
    named = _parent_type(obj) == "variable_declarator"
    return (_name_of(obj.parent) or None) if named else None


def _value_name(fn: Node) -> str | None:
    """Name a function expression is bound to, if it is a named unit:
    `const f = () => {}`, a class field `f = () => {}`, `{ f: () => {} }` in
    a named object, or `export default () => {}`."""
    parent = fn.parent
    kind = parent.type if parent is not None else None
    if kind in ("variable_declarator", "public_field_definition"):
        return _name_of(parent) or None
    if kind == "export_statement":
        return "default"
    if kind == "pair":
        owner = _object_owner(parent.parent)
        key = text(parent.child_by_field_name("key"))
        return f"{owner}.{key}" if owner else None
    return None


def _method_owner(method: Node) -> str | None:
    """For a `method_definition`: `''` in a class body (named by the class
    scope), `api` in a named object literal, None in an anonymous object."""
    if _parent_type(method) == "class_body":
        return ""
    return _object_owner(method.parent)


def _callee(call: Node) -> str:
    fn = call.child_by_field_name("function")
    return text(fn) if fn is not None and fn.type == "identifier" else ""


def _closure_call(fn: Node) -> Node | None:
    """The call a function expression is passed to as an argument."""
    args = fn.parent
    if args is None or args.type != "arguments":
        return None
    call = args.parent
    return call if call is not None and call.type == "call_expression" else None


def _description(call: Node) -> str:
    args = call.child_by_field_name("arguments")
    first = args.named_children[0] if args is not None and args.named_child_count else None
    if first is None or first.type not in ("string", "template_string"):
        return ""
    desc = " ".join(text(first).strip("'\"`").split())
    return desc if len(desc) <= 60 else desc[:57] + "..."


# --- parameters ------------------------------------------------------------------------


def _param(node: Node) -> Param:
    pattern = node.child_by_field_name("pattern")
    annotation = node.child_by_field_name("type")
    type_text = text(annotation).lstrip(":").strip() if annotation is not None else None
    return Param(
        name=text(pattern),
        type_text=type_text or None,
        optional=node.type == "optional_parameter" or node.child_by_field_name("value") is not None,
        variadic=pattern is not None and pattern.type == "rest_pattern",
    )


def _params(fn: Node) -> list[Param]:
    plist = fn.child_by_field_name("parameters")
    if plist is None:  # `x => x * 2`
        single = fn.child_by_field_name("parameter")
        return [Param(name=text(single))] if single is not None else []
    params = [_param(p) for p in plist.named_children if p.type in _PARAMS]
    return [p for p in params if p.name != "this"]  # `this: Window` is not passed


# --- roles that depend on context -------------------------------------------------------


def _binary_role(node: Node) -> Role | None:
    op = node.child_by_field_name("operator")
    return Role.BOOL_EXPR if op is not None and op.type in ("&&", "||") else None


def _jump_role(node: Node) -> Role | None:
    return Role.JUMP_LABEL if node.child_by_field_name("label") is not None else None


def _method_role(node: Node) -> Role | None:
    return Role.STOP if _method_owner(node) is not None else Role.LAMBDA


_CONTEXT_ROLES = {
    "binary_expression": _binary_role,
    "break_statement": _jump_role,
    "continue_statement": _jump_role,
    "method_definition": _method_role,
}


# --- types ----------------------------------------------------------------------------

_NOT_TYPES = frozenset({"undefined", "null", "void", "never"})


def _type_leaves(t: str) -> list[str] | None:
    """Innermost type names: `Order[]` -> [Order], `Map<string, number>` ->
    [string, number], `string | undefined` -> [string]; function, object
    and tuple types are unknown."""
    t = t.strip().removeprefix("readonly ").strip()
    if "=>" in t or t.startswith(("{", "[", "(")):
        return None
    leaves: list[str] = []
    for leaf in type_leaves(t.replace("[]", ""), "<", ">"):
        leaf = leaf.strip()
        if leaf.startswith(("{", "[", "(")):
            return None
        if leaf not in _NOT_TYPES:
            leaves.append(leaf)
    return leaves


class TypeScriptAdapter(LanguageAdapter):
    language = "typescript"
    extensions = (".ts", ".mts", ".cts")
    roles: ClassVar[dict[str, Role]] = {
        "if_statement": Role.IF,
        "for_statement": Role.LOOP,
        "for_in_statement": Role.LOOP,
        "while_statement": Role.LOOP,
        "do_statement": Role.LOOP,
        "switch_statement": Role.SWITCH,
        "switch_case": Role.CASE,  # switch_default is the default arm
        "catch_clause": Role.CATCH,
        "ternary_expression": Role.TERNARY,
        **dict.fromkeys(_DECLARATIONS, Role.STOP),
        **dict.fromkeys(_CLASSES, Role.STOP),
    }
    block_types = frozenset({"statement_block"})
    bool_transparent = frozenset({"parenthesized_expression", "unary_expression"})
    primitive_types = frozenset(
        {
            "number",
            "string",
            "bigint",
            "symbol",
            "object",
            "any",
            "unknown",
            "Array",
            "ReadonlyArray",
            "Record",
            "Map",
            "Set",
            "Object",
        }
    )
    neutral_types = frozenset({"boolean"})
    literal_types = frozenset(
        {"string", "template_string", "number", "regex", "true", "false", "null", "undefined"}
    )

    def __init__(self, test_calls: frozenset[str] | None = None) -> None:
        super().__init__()
        # closures passed to these calls are units, so a spec file isn't one
        # giant function (as for Dart and Swift)
        self.test_calls = DEFAULT_TEST_CALLS if test_calls is None else frozenset(test_calls)

    # --- metric hooks --------------------------------------------------------------------

    def role(self, node: Node) -> Role | None:
        if node.type in _FUNCTION_VALUES:
            return Role.STOP if self._unit_name(node) is not None else Role.LAMBDA
        contextual = _CONTEXT_ROLES.get(node.type)
        return contextual(node) if contextual is not None else self.roles.get(node.type)

    def else_if(self, node: Node) -> Node | None:
        if node.type != "else_clause":
            return None
        return next((c for c in node.named_children if c.type == "if_statement"), None)

    def bool_token(self, node: Node) -> str | None:
        return {"&&": "and", "||": "or"}.get(node.type)

    def is_recursive_call(self, node: Node, fn_name: str) -> bool:
        """`f(..)` or `this.f(..)`."""
        if node.type != "call_expression":
            return False
        target = node.child_by_field_name("function")
        return target is not None and text(target) in (fn_name, f"this.{fn_name}")

    def unwrap_loop(self, stmt: Node) -> Node | None:
        if stmt.type == "labeled_statement":
            stmt = stmt.child_by_field_name("body") or stmt
        return super().unwrap_loop(stmt)

    # --- module/class smell hooks --------------------------------------------------------

    def base_types(self, type_text: str) -> list[str] | None:
        return _type_leaves(type_text) or None

    def member_refs(self, cls: Class) -> dict[str, set[str]]:
        """`this.x` / `this.#x` per method; anything reached through `this`
        is the class's own field or method."""
        return {fn.qualname: _this_members(fn.body) for fn in cls.methods}

    # --- units ----------------------------------------------------------------------------

    def _unit_name(self, fn: Node) -> str | None:
        """Name of a function expression scored as its own unit, or None for
        an inline callback."""
        name = _value_name(fn)
        if name is not None:
            return name
        call = self._test_call(fn)
        if call is None:
            return None
        desc = _description(call)
        return f'{_callee(call)}("{desc}")' if desc else _callee(call)

    def _test_call(self, fn: Node) -> Node | None:
        call = _closure_call(fn)
        return call if call is not None and _callee(call) in self.test_calls else None

    # --- extraction ---------------------------------------------------------------------

    def _extract(self, module: Module) -> None:
        self._scan(ExtractionScope(module), module.root)

    def _scan(self, scope: ExtractionScope, parent: Node) -> None:
        """Units under ``parent``: direct children in source order, deeper
        ones (inside statements and expressions) as they are found."""
        for child in parent.named_children:
            if not self._add_unit(scope, child):
                walk_units(child, lambda n: self._add_unit(scope, n))

    def _add_unit(self, scope: ExtractionScope, node: Node) -> bool:
        """Add ``node`` if it is a unit; True if it was a boundary (its
        insides belong to that unit)."""
        if node.type in _DECLARATIONS:
            self._add_function(scope, node, (_name_of(node) or "default", "function"))
        elif node.type in _CLASSES:
            self._add_class(scope, node)
        elif node.type == "method_definition":
            return self._add_method(scope, node)
        elif node.type in _FUNCTION_VALUES:
            return self._add_value(scope, node)
        elif node.type == "internal_module":  # `namespace X { ... }`
            self._scan(scope.nested(f"{scope.prefix}{_name_of(node)}"), node)
        else:
            return False
        return True

    def _add_value(self, scope: ExtractionScope, fn: Node) -> bool:
        name = self._unit_name(fn)
        if name is None:
            return False  # inline callback: part of the enclosing function
        kind = "function" if _value_name(fn) is not None else "test"
        start = fn if kind == "function" else _closure_call(fn)
        self._add_function(scope, fn, (name, kind), start)
        return True

    def _add_method(self, scope: ExtractionScope, node: Node) -> bool:
        owner = _method_owner(node)
        if owner is None:
            return False  # method of an anonymous object: inline
        if owner:  # a named object literal outside any class
            scope = ExtractionScope(scope.module, None, f"{scope.prefix}{owner}.")
        self._add_function(scope, node, _method_name(node))
        return True

    def _add_class(self, scope: ExtractionScope, node: Node) -> None:
        cls = Class(
            name=f"{scope.prefix}{_name_of(node) or 'default'}",
            kind="class",
            start_line=_line(node),
            end_line=_end_line(node),
            node=node,
        )
        scope.module.classes.append(cls)
        body = node.child_by_field_name("body")
        if body is not None:
            self._scan(ExtractionScope(scope.module, cls, f"{cls.name}."), body)

    def _add_function(
        self, scope: ExtractionScope, fn: Node, label: tuple[str, str], start: Node | None = None
    ) -> None:
        """Add ``fn`` as a unit; ``label`` is (name, kind)."""
        name, kind = label
        body = fn.child_by_field_name("body")
        if body is None:
            return  # abstract method, overload signature
        if kind == "function" and scope.cls is not None:
            kind = "method"
        unit = Function(
            name=name,
            qualname=f"{scope.prefix}{name}",
            kind=kind,
            start_line=_line(start or fn),
            end_line=_end_line(fn),
            params=_params(fn),
            body=body,
            class_name=scope.cls.name if scope.cls else None,
        )
        scope.add(unit)
        self._scan(scope.nested(unit.qualname), body)


def _method_name(node: Node) -> tuple[str, str]:
    """(name, kind) of a method_definition: `x` getter, `x=` setter, ..."""
    name = text(node.child_by_field_name("name"))
    words = {c.type for c in node.children if not c.is_named}
    if name == "constructor":
        return name, "constructor"
    if "get" in words:
        return name, "getter"
    if "set" in words:
        return f"{name}=", "setter"
    return name, "function"


def _this_members(body: Node) -> set[str]:
    names: set[str] = set()
    stack = list(body.children)
    while stack:
        n = stack.pop()
        member = _this_member(n)
        if member:
            names.add(member)
        stack.extend(n.children)
    return names


def _this_member(node: Node) -> str | None:
    """`x` for `this.x` / `this.#x`, else None."""
    if node.type != "member_expression":
        return None
    obj = node.child_by_field_name("object")
    if obj is None or obj.type != "this":
        return None
    return text(node.child_by_field_name("property")).lstrip("#") or None


_JSX = ("jsx_element", "jsx_self_closing_element")


class TsxAdapter(TypeScriptAdapter):
    """TypeScript with JSX: element nesting is the widget-tree depth."""

    language = "tsx"
    extensions = (".tsx",)

    def widget_depth(self, body: Node) -> int:
        best = 0
        stack: list[tuple[Node, int]] = [(body, 0)]
        while stack:
            node, depth = stack.pop()
            if node.type in _JSX:
                depth += 1
                best = max(best, depth)
            if node is body or self.role(node) is not Role.STOP:
                stack.extend((c, depth) for c in node.children)
        return best

    def hint(self, smell: str) -> str | None:
        if smell == "deep_widget_tree":
            return "Extract subtrees into small components with their own props."
        return None
