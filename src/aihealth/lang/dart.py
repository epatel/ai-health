"""Dart adapter (tree-sitter `dart` grammar).

Grammar quirks handled here (see cards/parser-choice.md):
- a function body is the *next sibling* of its signature, not a child;
- in class bodies the signature is wrapped in `method_signature`;
- local functions are `local_function_declaration > lambda_expression`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from itertools import pairwise
from typing import ClassVar

from tree_sitter import Node

from aihealth.lang.base import (
    ExtractionScope,
    LanguageAdapter,
    iter_nodes,
    member_refs_by_method,
    text,
    type_leaves,
    walk_units,
)
from aihealth.metrics import Role
from aihealth.model import Class, Function, Module, Param

_SIGNATURES = {
    "function_signature": "function",
    "getter_signature": "getter",
    "setter_signature": "setter",
    "constructor_signature": "constructor",
    "factory_constructor_signature": "constructor",
    "constant_constructor_signature": "constructor",
    "redirecting_factory_constructor_signature": "constructor",
    "operator_signature": "operator",
}

_CONTAINERS = {
    "class_definition": "class",
    "mixin_declaration": "mixin",
    "extension_declaration": "extension",
    "extension_type_declaration": "extension",
    "enum_declaration": "enum",
}

_BODIES = {"class_body", "extension_body", "enum_body", "extension_type_body"}


def _child(node: Node | None, *types: str) -> Node | None:
    """First named child of one of ``types``."""
    if node is None:
        return None
    return next((c for c in node.named_children if c.type in types), None)


def _parent_if(node: Node | None, *types: str) -> Node | None:
    """The node's parent if it is one of ``types``, else None."""
    parent = node.parent if node is not None else None
    return parent if parent is not None and parent.type in types else None


def _signature_of(node: Node) -> Node | None:
    """The concrete *_signature node for a top-level sig or method_signature."""
    if node.type in _SIGNATURES:
        return node
    if node.type == "method_signature":
        return _child(node, *_SIGNATURES)
    return None


# --- names ------------------------------------------------------------------------


def _field_name(sig: Node) -> str:
    return text(sig.child_by_field_name("name"))


def _setter_name(sig: Node) -> str:
    return _field_name(sig) + "="


def _operator_name(sig: Node) -> str:
    op = next((c for c in sig.children if c.type.endswith("operator") and c.is_named), None)
    if op is not None:
        return f"operator {text(op)}"
    # e.g. `operator []`, `operator ~`: the tokens after the keyword
    parts = [text(c) for c in sig.children if c.type != "formal_parameter_list"]
    return "operator " + "".join(parts[parts.index("operator") + 1 :])


def _constructor_name(sig: Node) -> str:
    """Identifiers (and dots) before the parameter list: `Repo`, `Repo.named`."""
    parts = []
    for c in sig.children:
        if c.type in ("formal_parameter_list", "initializers"):
            break
        if c.type in ("identifier", "."):
            parts.append(text(c))
    return "".join(parts)


_NAMERS = {
    "function": _field_name,
    "getter": _field_name,
    "setter": _setter_name,
    "operator": _operator_name,
    "constructor": _constructor_name,
}


def _name(sig: Node) -> str:
    return _NAMERS[_SIGNATURES[sig.type]](sig)


# --- parameters ---------------------------------------------------------------------

_NOT_TYPE_PARTS = {"identifier", "final_builtin", "const_builtin", "inferred_type", "comment"}


def _param_type(node: Node) -> str | None:
    """Declared type text of a formal_parameter, or None if untyped."""
    inner = node.named_children[0] if node.named_child_count else None
    if inner is not None and inner.type in ("constructor_param", "super_formal_parameter"):
        return "<field>"  # `this.x` / `super.x`: type comes from the field
    type_parts = [text(c) for c in node.children if c.is_named and c.type not in _NOT_TYPE_PARTS]
    # `final x`, `var x` or bare `x`: no declared type
    return "".join(type_parts) or None


def _param_name(node: Node) -> str:
    name = node.child_by_field_name("name")
    if name is not None:
        return text(name)
    idents = [c for c in iter_nodes(node) if c.type == "identifier"]
    return text(idents[-1]) if idents else text(node)


def _param_nodes(plist: Node) -> Iterator[tuple[Node, bool, bool]]:
    """(formal_parameter, named, optional) in order; `{...}` params are named,
    `[...]` ones positional-optional."""
    for child in plist.named_children:
        if child.type == "formal_parameter":
            yield child, False, False
        elif child.type == "optional_formal_parameters":
            named = text(child).lstrip().startswith("{")
            yield from (
                (p, named, True) for p in child.named_children if p.type == "formal_parameter"
            )


def _param(node: Node, named: bool, optional: bool) -> Param:
    prev = node.prev_sibling
    required = prev is not None and prev.type == "required"
    return Param(
        name=_param_name(node),
        type_text=_param_type(node),
        named=named,
        optional=optional and not required,
    )


def _params(sig: Node) -> list[Param]:
    plist = sig.child_by_field_name("parameters") or _child(sig, "formal_parameter_list")
    if plist is None:
        return []
    return [_param(node, named, optional) for node, named, optional in _param_nodes(plist)]


# --- calls ------------------------------------------------------------------------------


def _call_target(holder: Node) -> Node | None:
    """The identifier called by `Foo(...)` or `Foo.named(...)`, whose argument
    list is in ``holder`` (the `selector` around the `argument_part`)."""
    prev = holder.prev_sibling
    if prev is not None and prev.type == "selector":  # Foo.named(...)
        if _child(prev, "unconditional_assignable_selector") is None:
            return None
        prev = prev.prev_sibling
    return prev if prev is not None and prev.type == "identifier" else None


def _is_widgetish_call(args: Node) -> bool:
    """True if an `arguments` node belongs to `Foo(...)`, `Foo.named(...)`,
    `const Foo(...)` or `new Foo(...)` with a capitalised type name."""
    if _parent_if(args, "const_object_expression", "new_expression") is not None:
        return True
    part = _parent_if(args, "argument_part")
    holder = part.parent if part is not None else None  # usually a `selector`
    target = _call_target(holder) if holder is not None else None
    name = text(target).lstrip("_$") if target is not None else ""
    return bool(name) and name[0].isupper()


def _closure_call(fexpr: Node) -> tuple[str, Node] | None:
    """For `callee('desc', () {...})` return (callee, arguments node)."""
    args = _parent_if(_parent_if(fexpr, "argument", "named_argument"), "arguments")
    part = _parent_if(args, "argument_part")
    callee = part.parent.prev_sibling if part is not None and part.parent is not None else None
    if callee is None or callee.type != "identifier":
        return None
    return text(callee), args


def _description(args: Node) -> str:
    first = _child(args, "argument")
    lit = first.named_children[0] if first is not None and first.named_child_count else None
    if lit is None or lit.type != "string_literal":
        return ""
    desc = " ".join(text(lit).strip("'\"").split())
    return desc if len(desc) <= 60 else desc[:57] + "..."


# --- class members (cohesion) -----------------------------------------------------------


def _is_member_of_other(ident: Node) -> bool:
    """`other.name` (inside a `.name` selector not preceded by `this`) is a
    member of another object, not of this class. Named-argument labels
    (`foo(name: x)`) are not references either."""
    parent = ident.parent
    if parent is None:
        return False
    if parent.type == "label":
        return True
    if parent.type == "unconditional_assignable_selector":
        sel = parent.parent  # selector, or assignable_expression
        prev = sel.prev_sibling if sel is not None else None
        return not (prev is not None and prev.type == "this")
    return False


def _class_body(cls: Class) -> Node | None:
    if cls.node is None:
        return None
    return cls.node.child_by_field_name("body") or _child(cls.node, *_BODIES)


def _is_instance_declaration(node: Node) -> bool:
    # static members are class constants, not instance state
    return node.type == "declaration" and not text(node).lstrip().startswith("static")


def _instance_fields(cls: Class) -> set[str]:
    """Names of the class's non-static fields."""
    body = _class_body(cls)
    if body is None:
        return set()
    fields = set()
    for decl in filter(_is_instance_declaration, body.named_children):
        for n in iter_nodes(decl):
            ident = next((c for c in n.children if c.type == "identifier"), None)
            if n.type == "initialized_identifier" and ident is not None:
                fields.add(text(ident))
    return fields


def _is_member_ref(node: Node, members: set[str]) -> bool:
    """An identifier naming one of this class's members (not `other.x`)."""
    is_name = node.type == "identifier" and text(node) in members
    return is_name and not _is_member_of_other(node)


def _member_names_in(body: Node, members: set[str]) -> set[str]:
    """Class members referenced in a method body (local functions excluded)."""
    names: set[str] = set()
    stack = list(body.children)
    while stack:
        n = stack.pop()
        if n.type == "local_function_declaration":
            continue
        if _is_member_ref(n, members):
            names.add(text(n))
        stack.extend(n.children)
    return names


# --- roles that depend on context -------------------------------------------------------


def _block_role(node: Node) -> Role | None:
    """Handler bodies are siblings of catch_clause/on-type inside try."""
    parent = node.parent
    if parent is None or parent.type != "try_statement":
        return None
    body = parent.child_by_field_name("body")
    return Role.CATCH if body is None or body.id != node.id else None


def _jump_role(node: Node) -> Role | None:
    """`break label;` / `continue label;`; unlabeled jumps add nothing."""
    return Role.JUMP_LABEL if any(c.type == "identifier" for c in node.children) else None


def _switch_expression_case_role(node: Node) -> Role | None:
    pattern = node.named_children[0] if node.named_child_count else None
    if pattern is not None and text(pattern).strip() == "_":
        return None  # wildcard arm is the default
    return Role.CASE


def _is_unknown_type(type_text: str) -> bool:
    """Field-typed params, callbacks and records: not judged as primitives."""
    return type_text == "<field>" or "Function" in type_text or type_text.startswith("(")


DEFAULT_TEST_CALLS = frozenset(
    {
        "group",
        "test",
        "testWidgets",
        "testGoldens",
        "blocTest",
        "setUp",
        "setUpAll",
        "tearDown",
        "tearDownAll",
    }
)


def _is_function_body(node: Node | None) -> bool:
    return node is not None and node.type == "function_body"


class DartAdapter(LanguageAdapter):
    language = "dart"
    extensions = (".dart",)
    comment_types = frozenset({"comment", "documentation_comment"})

    def __init__(self, test_calls: frozenset[str] | None = None) -> None:
        super().__init__()
        # closures passed to these calls are scored as their own units, so a
        # test file's `main()` isn't one giant function (see code-health-model)
        self.test_calls = DEFAULT_TEST_CALLS if test_calls is None else frozenset(test_calls)

    primitive_types = frozenset(
        {
            "int",
            "double",
            "num",
            "String",
            "List",
            "Map",
            "Set",
            "Iterable",
            "dynamic",
            "Object",
        }
    )
    literal_types = frozenset(
        {
            "string_literal",
            "decimal_integer_literal",
            "decimal_floating_point_literal",
            "hex_integer_literal",
            "true",
            "false",
            "null_literal",
        }
    )

    def base_types(self, type_text: str) -> list[str] | None:
        t = type_text.strip()
        if _is_unknown_type(t):
            return None
        return type_leaves(t, "<", ">") or None

    def member_refs(self, cls: Class) -> dict[str, set[str]]:
        """Bare or `this.`-qualified references to the class's own fields and
        methods in each method body."""
        return member_refs_by_method(cls, _instance_fields(cls), _member_names_in)

    def _test_unit(self, fexpr: Node) -> tuple[str, Node] | None:
        call = _closure_call(fexpr)
        if call is None or call[0] not in self.test_calls:
            return None
        return call

    def _is_test_closure(self, node: Node) -> bool:
        return node.type == "function_expression" and self._test_unit(node) is not None

    # --- metric hooks --------------------------------------------------------------------

    roles: ClassVar[dict[str, Role]] = {
        "if_statement": Role.IF,
        "if_element": Role.IF,
        "for_statement": Role.LOOP,
        "while_statement": Role.LOOP,
        "do_statement": Role.LOOP,
        "for_element": Role.LOOP,
        "switch_statement": Role.SWITCH,
        "switch_expression": Role.SWITCH,
        "switch_statement_case": Role.CASE,
        "switch_expression_case": Role.CASE,
        "conditional_expression": Role.TERNARY,
        "function_expression": Role.LAMBDA,
        "local_function_declaration": Role.STOP,
        "logical_and_expression": Role.BOOL_EXPR,
        "logical_or_expression": Role.BOOL_EXPR,
    }
    bool_transparent = frozenset({"parenthesized_expression", "unary_expression"})

    def _closure_role(self, node: Node) -> Role | None:
        # closures passed to test()/group() are units of their own
        return Role.STOP if self._test_unit(node) is not None else Role.LAMBDA

    # node types whose role depends on context (plain functions: dict values
    # are not bound as methods)
    _CONTEXT_ROLES: ClassVar[dict[str, Callable[[Node], Role | None]]] = {
        "block": _block_role,
        "break_statement": _jump_role,
        "continue_statement": _jump_role,
        "switch_expression_case": _switch_expression_case_role,
    }

    def role(self, node: Node) -> Role | None:
        if node.type == "function_expression":
            return self._closure_role(node)
        contextual = self._CONTEXT_ROLES.get(node.type)
        return contextual(node) if contextual is not None else self.roles.get(node.type)

    def bool_token(self, node: Node) -> str | None:
        return {"logical_and_operator": "and", "logical_or_operator": "or"}.get(node.type)

    def is_recursive_call(self, node: Node, fn_name: str) -> bool:
        if node.type != "identifier" or text(node) != fn_name:
            return False
        nxt = node.next_sibling
        if nxt is None or nxt.type != "selector":
            return False
        # `other.fact(...)` puts `fact` inside a selector, so a bare identifier
        # followed by an argument list is a direct (unqualified) call
        return any(c.type == "argument_part" for c in nxt.children)

    def unwrap_loop(self, stmt: Node) -> Node | None:
        if stmt.type == "labeled_statement" and stmt.named_child_count:
            stmt = stmt.named_children[-1]
        return super().unwrap_loop(stmt)

    def widget_depth(self, body: Node) -> int:
        """Deepest nesting of widget-like constructor calls in the body."""
        best = 0
        stack = [(body, 0)]
        while stack:
            node, depth = stack.pop()
            if node.type == "arguments" and _is_widgetish_call(node):
                depth += 1
                best = max(best, depth)
            if not self._is_unit_boundary(node):
                stack.extend((c, depth) for c in node.children)
        return best

    def _is_unit_boundary(self, node: Node) -> bool:
        """Nested units scored on their own: local functions, test closures."""
        return node.type == "local_function_declaration" or self._is_test_closure(node)

    # --- extraction ---------------------------------------------------------------------

    def _extract(self, module: Module) -> None:
        self._scan_members(ExtractionScope(module), module.root)

    def _scan_members(self, scope: ExtractionScope, parent: Node) -> None:
        # each member with its next sibling (a function body follows its signature)
        for node, nxt in pairwise([*parent.named_children, None]):
            self._add_member(scope, node, nxt)

    def _add_member(self, scope: ExtractionScope, node: Node, nxt: Node | None) -> None:
        """A container, a function (signature + body sibling), or a body-less
        declaration whose initializer closures may hold local functions."""
        if node.type in _CONTAINERS:
            self._add_container(scope.module, node)
            return
        sig = _signature_of(node)
        if sig is None and node.type == "declaration":
            # Body-less constructors (initializer lists) and abstract methods
            # are not scored, but closures in initializers may declare local
            # functions, e.g. `super(builder: (f) { void h() {...} ... })`.
            ctor = _child(node, *_SIGNATURES)
            owner = _name(ctor) if ctor is not None else "<init>"
            self._add_local_functions(scope.nested(owner), node)
        elif sig is not None and _is_function_body(nxt):
            self._add_function(scope, sig, node, nxt)
        # else: abstract / external declaration, nothing to score

    def _add_container(self, module: Module, node: Node) -> None:
        name_node = node.child_by_field_name("name") or _child(
            node, "identifier", "type_identifier"
        )
        cls = Class(
            name=text(name_node) or "<extension>",
            kind=_CONTAINERS[node.type],
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            node=node,
        )
        module.classes.append(cls)
        body = _class_body(cls)
        if body is not None:
            self._scan_members(ExtractionScope(module).in_class(cls), body)

    def _add_function(self, scope: ExtractionScope, sig: Node, outer: Node, body: Node) -> None:
        kind = _SIGNATURES[sig.type]
        name = _name(sig)
        if kind == "function" and scope.cls is not None:
            kind = "method"
        # constructor names already carry the class: `Repo`, `Repo.named`
        qualname = name if kind == "constructor" else f"{scope.prefix}{name}"
        scope.add(
            Function(
                name=name,
                qualname=qualname,
                kind=kind,
                start_line=outer.start_point[0] + 1,
                end_line=body.end_point[0] + 1,
                params=_params(sig),
                body=body,
                class_name=scope.cls.name if scope.cls else None,
            )
        )
        self._add_local_functions(scope.nested(qualname), body)

    def _add_test_unit(self, scope: ExtractionScope, fexpr: Node, call: tuple[str, Node]) -> None:
        callee, args = call
        body = fexpr.child_by_field_name("body")
        if body is None:
            return
        desc = _description(args)
        name = f'{callee}("{desc}")' if desc else callee
        fn = Function(
            name=name,
            qualname=f"{scope.prefix}{name}",
            kind="test",
            start_line=args.parent.parent.prev_sibling.start_point[0] + 1,
            end_line=fexpr.end_point[0] + 1,
            params=_params(fexpr),
            body=body,
        )
        scope.add(fn)
        self._add_local_functions(scope.nested(fn.qualname), body)

    def _add_local_functions(self, scope: ExtractionScope, body: Node) -> None:
        """Local named functions and test closures are scored on their own;
        other closures stay part of the enclosing function."""
        walk_units(body, lambda n: self._add_nested_unit(scope, n))

    def _add_nested_unit(self, scope: ExtractionScope, n: Node) -> bool:
        """Add ``n`` if it is a nested unit; True if it was a unit boundary
        (its insides are then handled by that unit, not the caller)."""
        if n.type == "function_expression":
            call = self._test_unit(n)
            if call is not None:
                self._add_test_unit(scope, n, call)
            return call is not None
        if n.type != "local_function_declaration":
            return False
        lam = _child(n, "lambda_expression")
        sig, fbody = _child(lam, "function_signature"), _child(lam, "function_body")
        if sig is not None and fbody is not None:
            self._add_function(scope, sig, n, fbody)
        return True
