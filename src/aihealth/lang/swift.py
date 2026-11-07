"""Swift adapter (tree-sitter `swift` grammar).

Grammar quirks handled here (see cards/parser-choice.md):
- struct / class / enum / actor / extension are all `class_declaration`,
  told apart by the `declaration_kind` field;
- an if's else branch has no `alternative` field: it is whatever follows
  the bare `else` token;
- the `name` field labels both a parameter's name and its type;
- default values are siblings of their `parameter`, not children;
- a loop label (`outer:`) is a `statement_label` *sibling* of the loop;
- operator precedence is unreliable (`a > b || c` nests the `||` inside
  the comparison), so comparison nodes are transparent for conditions.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import ClassVar

from tree_sitter import Node

from aihealth.lang.base import (
    ExtractionScope,
    LanguageAdapter,
    member_refs_by_method,
    split_top,
    text,
    type_leaves,
    walk_units,
)
from aihealth.metrics import Role
from aihealth.model import Class, Function, Module, Param

_FUNCTIONS = {
    "function_declaration": "function",
    "init_declaration": "constructor",
    "deinit_declaration": "destructor",
}

_STATIC = ("static", "class")

DEFAULT_TEST_CALLS = frozenset(
    {
        "describe",
        "context",
        "it",
        "fdescribe",
        "fcontext",
        "fit",
        "xdescribe",
        "xcontext",
        "xit",
        "beforeEach",
        "afterEach",
        "justBeforeEach",
        "aroundEach",
        "beforeSuite",
        "afterSuite",
    }
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


def _is_static(decl: Node) -> bool:
    """`static` / `class` members are type-level, not instance state."""
    mods = _child(decl, "modifiers")
    words = text(mods).split() if mods is not None else []
    return any(w in _STATIC for w in words) or any(c.type == "class" for c in decl.children)


# --- names ------------------------------------------------------------------------


def _function_name(node: Node) -> str:
    if node.type == "init_declaration":
        return "init"
    if node.type == "deinit_declaration":
        return "deinit"
    return text(node.child_by_field_name("name"))  # an identifier or an operator token


def _property_name(decl: Node) -> str:
    pattern = decl.child_by_field_name("name")
    ident = pattern.child_by_field_name("bound_identifier") if pattern is not None else None
    return text(ident or pattern) or "<property>"


def _container_name(node: Node) -> str:
    return text(node.child_by_field_name("name")) or "<anonymous>"


def _container_kind(node: Node) -> str:
    kind = node.child_by_field_name("declaration_kind")
    return text(kind) if kind is not None else "class"


# --- parameters ---------------------------------------------------------------------


def _param_type(param: Node) -> str | None:
    """Type text after the `:` (modifiers like `inout` / `@escaping` excluded)."""
    kids = param.children
    colon = next((i for i, c in enumerate(kids) if c.type == ":"), len(kids))
    after = [c for c in kids[colon + 1 :] if c.is_named]
    return next((text(c) for c in after if c.type != "parameter_modifiers"), None)


def _param(node: Node) -> Param:
    external = node.child_by_field_name("external_name")
    return Param(
        name=text(node.child_by_field_name("name")),
        type_text=_param_type(node),
        # a call-site label (`f(x: 1)`), unless the label is `_`
        named=external is None or text(external) != "_",
        variadic=any(c.type == "..." for c in node.children),
    )


def _params(decl: Node) -> list[Param]:
    out: list[Param] = []
    for i, c in enumerate(decl.children):
        if c.type == "parameter":
            out.append(_param(c))
        elif out and decl.field_name_for_child(i) == "default_value":
            out[-1].optional = True
    return out


def _lambda_params(lam: Node) -> list[Param]:
    ftype = lam.child_by_field_name("type")
    plist = _child(ftype, "lambda_function_type_parameters")
    if plist is None:
        return []
    return [
        Param(name=text(p.child_by_field_name("name")) or text(p)) for p in plist.named_children
    ]


def _closure_fields(owner: Node, lam: Node) -> dict:
    """Function fields for a closure scored on its own (`#Preview { }`,
    `it("...") { }`); it starts at ``owner``, the macro or call."""
    return {
        "start_line": _line(owner),
        "end_line": _end_line(lam),
        "params": _lambda_params(lam),
        "body": _child(lam, "statements") or lam,
    }


# --- computed properties ----------------------------------------------------------------

_ACCESSORS = {
    "computed_getter": ("getter", ""),
    "computed_setter": ("setter", "="),
    "computed_modify": ("setter", ".modify"),
    "willset_clause": ("observer", ".willSet"),
    "didset_clause": ("observer", ".didSet"),
}


def _accessors(holder: Node) -> Iterator[tuple[str, str, Node]]:
    """(kind, name suffix, body) for each accessor of a computed property,
    subscript or observed property. A bare `{ ... }` is a getter."""
    for block in (_child(holder, "computed_property"), _child(holder, "willset_didset_block")):
        if block is None:
            continue
        accessors = [c for c in block.named_children if c.type in _ACCESSORS]
        if not accessors and block.type == "computed_property":
            yield "getter", "", block
        for acc in accessors:
            kind, suffix = _ACCESSORS[acc.type]
            yield kind, suffix, acc


# --- calls ------------------------------------------------------------------------------


def _call_target(call: Node) -> Node | None:
    """The called expression. The grammar parses `n * f(x)` as `(n * f)(x)`,
    so it is the rightmost operand of a leading binary expression."""
    target = call.named_children[0] if call.named_child_count else None
    while target is not None and target.child_by_field_name("rhs") is not None:
        target = target.child_by_field_name("rhs")
    return target


def _ident(node: Node | None) -> Node | None:
    return node if node is not None and node.type == "simple_identifier" else None


def _callee(call: Node) -> Node | None:
    """The identifier called by `f(...)`."""
    return _ident(_call_target(call))


def _self_member(nav: Node) -> Node | None:
    """`x` in `self.x`, else None."""
    on = nav.child_by_field_name("target")
    if on is None or on.type != "self_expression":
        return None
    suffix = nav.child_by_field_name("suffix")
    return suffix.child_by_field_name("suffix") if suffix is not None else None


def _called_name(call: Node) -> Node | None:
    """The identifier called by `f(...)` or `self.f(...)`."""
    target = _call_target(call)
    if target is not None and target.type == "navigation_expression":
        target = _self_member(target)
    return _ident(target)


def _is_view_call(call: Node) -> bool:
    """`Text(...)`, `VStack { ... }`: a call of a capitalised name."""
    name = text(_callee(call)).lstrip("_")
    return bool(name) and name[0].isupper()


def _closure_call(lam: Node) -> Node | None:
    """The call_expression a closure is passed to, as a trailing closure or
    as an argument (`it("x") { }`, `it("x", closure: { })`)."""
    node = lam.parent
    while node is not None and node.type in ("value_argument", "value_arguments", "call_suffix"):
        node = node.parent
    return node if node is not None and node.type == "call_expression" else None


def _description(call: Node) -> str:
    args = _child(_child(call, "call_suffix"), "value_arguments")
    first = _child(args, "value_argument")
    lit = first.child_by_field_name("value") if first is not None else None
    if lit is None or lit.type != "line_string_literal":
        return ""
    desc = " ".join(text(lit).strip('"').split())
    return desc if len(desc) <= 60 else desc[:57] + "..."


# --- class members (cohesion) -----------------------------------------------------------


def _class_body(cls: Class) -> Node | None:
    return cls.node.child_by_field_name("body") if cls.node is not None else None


def _instance_fields(cls: Class) -> set[str]:
    body = _class_body(cls)
    if body is None:
        return set()
    return {
        _property_name(d)
        for d in body.named_children
        if d.type == "property_declaration" and not _is_static(d)
    }


def _is_foreign_member(ident: Node) -> bool:
    """`other.x` (a navigation suffix not on `self`) or an argument label."""
    parent = ident.parent
    if parent is None:
        return False
    if parent.type == "value_argument_label":
        return True
    if parent.type != "navigation_suffix":
        return False
    target = parent.parent.child_by_field_name("target") if parent.parent is not None else None
    return target is None or target.type != "self_expression"


def _is_member_ref(node: Node, members: set[str]) -> bool:
    """An identifier naming one of this type's members (not `other.x`)."""
    is_name = node.type == "simple_identifier" and text(node) in members
    return is_name and not _is_foreign_member(node)


def _member_names_in(body: Node, members: set[str]) -> set[str]:
    names: set[str] = set()
    stack = list(body.children)
    while stack:
        n = stack.pop()
        if n.type in ("function_declaration", "class_declaration"):
            continue
        if _is_member_ref(n, members):
            names.add(text(n))
        stack.extend(n.children)
    return names


# --- roles that depend on context -------------------------------------------------------


def _jump_role(node: Node) -> Role | None:
    """`break label` / `continue label`; other jumps add nothing."""
    words = text(node).split()
    labeled = len(words) == 2 and words[0] in ("break", "continue")
    return Role.JUMP_LABEL if labeled else None


def _case_role(node: Node) -> Role | None:
    return None if _child(node, "default_keyword") is not None else Role.CASE


# --- types --------------------------------------------------------------------------------


def _strip_type(t: str) -> str:
    t = t.strip()
    while t.startswith("@"):  # attributes: `@Sendable () -> Void`
        t = t.partition(" ")[2].strip()
    for prefix in ("some ", "any ", "borrowing ", "consuming "):
        t = t.removeprefix(prefix)
    return t.rstrip("?!").removesuffix("...").strip()


def _type_leaves(t: str) -> list[str] | None:
    """Innermost type names: `[Order]` -> [Order], `[String: Int]` ->
    [String, Int], `Set<UUID>` -> [UUID]; closures and tuples are unknown."""
    t = _strip_type(t)
    if "->" in t or t.startswith("("):
        return None
    if t.startswith("[") and t.endswith("]"):
        leaves: list[str] = []
        for part in split_top(t[1:-1], ":", "[", "]"):
            inner = _type_leaves(part)
            if inner is None:
                return None
            leaves += inner
        return leaves
    return [x.rstrip("?!").removeprefix("Swift.") for x in type_leaves(t, "<", ">")]


class SwiftAdapter(LanguageAdapter):
    language = "swift"
    extensions = (".swift",)
    comment_types = frozenset({"comment", "multiline_comment"})
    block_types = frozenset({"statements"})

    def __init__(self, test_calls: frozenset[str] | None = None) -> None:
        super().__init__()
        # Quick-style `describe { it { } }` closures are scored as their own
        # units, so a spec file isn't one giant function
        self.test_calls = DEFAULT_TEST_CALLS if test_calls is None else frozenset(test_calls)

    roles: ClassVar[dict[str, Role]] = {
        "if_statement": Role.IF,
        "guard_statement": Role.IF,
        "for_statement": Role.LOOP,
        "while_statement": Role.LOOP,
        "repeat_while_statement": Role.LOOP,
        "switch_statement": Role.SWITCH,
        "catch_block": Role.CATCH,
        "ternary_expression": Role.TERNARY,
        "lambda_literal": Role.LAMBDA,
        "function_declaration": Role.STOP,
        "class_declaration": Role.STOP,
        "protocol_declaration": Role.STOP,
        "conjunction_expression": Role.BOOL_EXPR,
        "disjunction_expression": Role.BOOL_EXPR,
    }
    # comparisons are transparent: the grammar may put `||` inside `a > b`
    bool_transparent = frozenset(
        {
            "tuple_expression",
            "prefix_expression",
            "comparison_expression",
            "equality_expression",
            "infix_expression",
        }
    )
    primitive_types = frozenset(
        {
            "Int",
            "Int8",
            "Int16",
            "Int32",
            "Int64",
            "UInt",
            "UInt8",
            "UInt16",
            "UInt32",
            "UInt64",
            "Double",
            "Float",
            "CGFloat",
            "Decimal",
            "String",
            "Substring",
            "Character",
            "Array",
            "Dictionary",
            "Set",
            "Any",
            "AnyObject",
        }
    )
    neutral_types = frozenset({"Bool"})
    literal_types = frozenset(
        {
            "line_string_literal",
            "multi_line_string_literal",
            "raw_string_literal",
            "integer_literal",
            "real_literal",
            "hex_literal",
            "oct_literal",
            "bin_literal",
            "boolean_literal",
            "nil_literal",
            "regex_literal",
        }
    )

    # --- metric hooks --------------------------------------------------------------------

    def role(self, node: Node) -> Role | None:
        if node.type == "lambda_literal":
            return Role.STOP if self._test_call(node) is not None else Role.LAMBDA
        if node.type == "control_transfer_statement":
            return _jump_role(node)
        if node.type == "switch_entry":
            return _case_role(node)
        return self.roles.get(node.type)

    def is_alternative(self, node: Node, index: int) -> bool:
        """The child right after an if's bare `else` token."""
        return node.type == "if_statement" and index > 0 and node.children[index - 1].type == "else"

    def bool_token(self, node: Node) -> str | None:
        return {"&&": "and", "||": "or"}.get(node.type)

    def is_recursive_call(self, node: Node, fn_name: str) -> bool:
        if node.type != "call_expression":
            return False
        target = _called_name(node)
        return target is not None and text(target) == fn_name

    def statements(self, body: Node) -> list[Node]:
        # loop labels and `#if` lines sit between statements
        skip = ("statement_label", "directive")
        return [s for s in super().statements(body) if s.type not in skip]

    def unwrap_loop(self, stmt: Node) -> Node | None:
        if self.role(stmt) is not Role.LOOP:
            return None
        return _child(stmt, "statements")

    def widget_depth(self, body: Node) -> int:
        """Deepest nesting of view-like calls (`VStack { Text(...) }`)."""
        best = 0
        stack = [(body, 0)]
        while stack:
            node, depth = stack.pop()
            if node.type == "call_expression" and _is_view_call(node):
                depth += 1
                best = max(best, depth)
            if node is body or self.role(node) is not Role.STOP:
                stack.extend((c, depth) for c in node.children)
        return best

    def hint(self, smell: str) -> str | None:
        if smell == "deep_widget_tree":
            return "Extract subtrees into small `View` structs with their own `body`."
        return None

    # --- module/class smell hooks --------------------------------------------------------

    def base_types(self, type_text: str) -> list[str] | None:
        return _type_leaves(type_text) or None

    def member_refs(self, cls: Class) -> dict[str, set[str]]:
        """Bare or `self.`-qualified references to the type's own stored
        properties and methods in each method body."""
        return member_refs_by_method(cls, _instance_fields(cls), _member_names_in)

    def _test_call(self, lam: Node) -> Node | None:
        call = _closure_call(lam)
        if call is None or text(_callee(call)) not in self.test_calls:
            return None
        return call

    # --- extraction ---------------------------------------------------------------------

    def _extract(self, module: Module) -> None:
        self._scan_members(ExtractionScope(module), module.root)

    def _scan_members(self, scope: ExtractionScope, parent: Node) -> None:
        for node in parent.named_children:
            self._add_member(scope, node)

    def _add_member(self, scope: ExtractionScope, node: Node) -> None:
        if node.type in _FUNCTIONS:
            self._add_function(scope, node)
        elif node.type == "class_declaration":
            self._add_container(scope, node)
        elif node.type in ("property_declaration", "subscript_declaration"):
            self._add_accessors(scope, node)
        elif node.type == "macro_invocation":
            self._add_macro(scope, node)
        elif node.type == "ERROR":
            # error recovery can wrap whole declarations: still score them
            self._scan_members(scope, node)
        else:
            # script statements (main.swift), macro arguments: their closures
            # may declare functions and types
            self._add_nested_units(scope, node)

    def _add_container(self, scope: ExtractionScope, node: Node) -> None:
        cls = Class(
            name=f"{scope.prefix}{_container_name(node)}",
            kind=_container_kind(node),
            start_line=_line(node),
            end_line=_end_line(node),
            node=node,
        )
        scope.module.classes.append(cls)
        body = node.child_by_field_name("body")
        if body is not None:
            self._scan_members(scope.in_class(cls), body)

    def _unit(self, scope: ExtractionScope, name: str, kind: str, **fields) -> Function:
        """Add a function named ``name`` in ``scope`` and its nested units."""
        if kind == "function" and scope.cls is not None:
            kind = "method"
        fn = Function(
            name=name,
            qualname=f"{scope.prefix}{name}",
            kind=kind,
            class_name=scope.cls.name if scope.cls else None,
            **fields,
        )
        scope.add(fn)
        self._add_nested_units(scope.nested(fn.qualname), fn.body)
        return fn

    def _add_function(self, scope: ExtractionScope, node: Node) -> None:
        body = node.child_by_field_name("body")
        if body is None:
            return  # e.g. a protocol requirement
        self._unit(
            scope,
            _function_name(node),
            _FUNCTIONS[node.type],
            start_line=_line(node),
            end_line=_end_line(node),
            params=_params(node),
            body=body,
        )

    def _add_accessors(self, scope: ExtractionScope, node: Node) -> None:
        """Getters / setters / observers of a property or subscript; a stored
        property's initializer may still hold local functions."""
        base = "subscript" if node.type == "subscript_declaration" else _property_name(node)
        params = _params(node) if node.type == "subscript_declaration" else []
        found = False
        for kind, suffix, body in _accessors(node):
            found = True
            start = node if kind == "getter" and not suffix else body
            self._unit(
                scope,
                base + suffix,
                kind,
                start_line=_line(start),
                end_line=_end_line(body),
                params=params,
                body=body,
            )
        if not found:
            self._add_nested_units(scope.nested(base), node)

    def _add_macro(self, scope: ExtractionScope, node: Node) -> None:
        """`#Preview { ... }`: the trailing closure is a unit of its own."""
        lam = _child(_child(node, "call_suffix"), "lambda_literal")
        if lam is None:
            return
        name = "#" + text(_child(node, "simple_identifier"))
        self._unit(scope, name, "function", **_closure_fields(node, lam))

    def _add_test_unit(self, scope: ExtractionScope, lam: Node, call: Node) -> None:
        callee = text(_callee(call))
        desc = _description(call)
        name = f'{callee}("{desc}")' if desc else callee
        self._unit(scope, name, "test", **_closure_fields(call, lam))

    def _add_nested_units(self, scope: ExtractionScope, body: Node) -> None:
        """Local functions, local types and test closures are scored on their
        own; other closures stay part of the enclosing function."""
        walk_units(body, lambda n: self._add_nested_unit(scope, n))

    def _add_nested_unit(self, scope: ExtractionScope, n: Node) -> bool:
        """Add ``n`` if it is a nested unit; True if it was a unit boundary."""
        if n.type == "lambda_literal":
            call = self._test_call(n)
            if call is not None:
                self._add_test_unit(scope, n, call)
            return call is not None
        if n.type in _FUNCTIONS or n.type == "class_declaration":
            self._add_member(scope, n)
            return True
        return False
