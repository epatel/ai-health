"""Dart adapter (tree-sitter `dart` grammar).

Grammar quirks handled here (see cards/parser-choice.md):
- a function body is the *next sibling* of its signature, not a child;
- in class bodies the signature is wrapped in `method_signature`;
- local functions are `local_function_declaration > lambda_expression`.
"""

from __future__ import annotations

from typing import ClassVar

from tree_sitter import Node

from aihealth.lang.base import LanguageAdapter, iter_nodes, text
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


def _signature_of(node: Node) -> Node | None:
    """The concrete *_signature node for a top-level sig or method_signature."""
    if node.type in _SIGNATURES:
        return node
    if node.type == "method_signature":
        return next((c for c in node.named_children if c.type in _SIGNATURES), None)
    return None


def _name(sig: Node) -> str:
    kind = _SIGNATURES[sig.type]
    if kind in ("function", "getter"):
        return text(sig.child_by_field_name("name"))
    if kind == "setter":
        return text(sig.child_by_field_name("name")) + "="
    if kind == "operator":
        op = next((c for c in sig.children if c.type.endswith("operator") and c.is_named), None)
        if op is None:  # e.g. `operator []`, `operator ~`
            parts = [text(c) for c in sig.children if c.type != "formal_parameter_list"]
            return "operator " + "".join(parts[parts.index("operator") + 1 :])
        return f"operator {text(op)}"
    # constructors: identifiers (and dots) before the parameter list
    parts = []
    for c in sig.children:
        if c.type in ("formal_parameter_list", "initializers"):
            break
        if c.type in ("identifier", "."):
            parts.append(text(c))
    return "".join(parts)


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


def _params(sig: Node) -> list[Param]:
    plist = sig.child_by_field_name("parameters") or next(
        (c for c in sig.named_children if c.type == "formal_parameter_list"), None
    )
    if plist is None:
        return []
    out: list[Param] = []

    def add(node: Node, named: bool, optional: bool) -> None:
        prev = node.prev_sibling
        required = prev is not None and prev.type == "required"
        out.append(
            Param(
                name=_param_name(node),
                type_text=_param_type(node),
                named=named,
                optional=optional and not required,
            )
        )

    for child in plist.named_children:
        if child.type == "formal_parameter":
            add(child, named=False, optional=False)
        elif child.type == "optional_formal_parameters":
            named = text(child).lstrip().startswith("{")
            for p in child.named_children:
                if p.type == "formal_parameter":
                    add(p, named=named, optional=True)
    return out


def _is_widgetish_call(args: Node) -> bool:
    """True if an `arguments` node belongs to `Foo(...)`, `Foo.named(...)`,
    `const Foo(...)` or `new Foo(...)` with a capitalised type name."""
    parent = args.parent
    if parent is None:
        return False
    if parent.type in ("const_object_expression", "new_expression"):
        return True
    if parent.type != "argument_part" or parent.parent is None:
        return False
    selector = parent.parent  # selector > argument_part > arguments
    prev = selector.prev_sibling
    if prev is not None and prev.type == "selector":  # Foo.named(...)
        if not any(c.type == "unconditional_assignable_selector" for c in prev.children):
            return False
        prev = prev.prev_sibling
    if prev is None or prev.type != "identifier":
        return False
    name = text(prev).lstrip("_$")
    return bool(name) and name[0].isupper()


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


def _closure_call(fexpr: Node) -> tuple[str, Node] | None:
    """For `callee('desc', () {...})` return (callee, arguments node)."""
    arg = fexpr.parent
    if arg is None or arg.type not in ("argument", "named_argument"):
        return None
    args = arg.parent
    if args is None or args.type != "arguments":
        return None
    part = args.parent
    if part is None or part.type != "argument_part" or part.parent is None:
        return None
    prev = part.parent.prev_sibling
    if prev is None or prev.type != "identifier":
        return None
    return text(prev), args


def _description(args: Node) -> str:
    first = next((c for c in args.named_children if c.type == "argument"), None)
    lit = first.named_children[0] if first is not None and first.named_child_count else None
    if lit is None or lit.type != "string_literal":
        return ""
    desc = " ".join(text(lit).strip("'\"").split())
    return desc if len(desc) <= 60 else desc[:57] + "..."


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
        if t == "<field>" or "Function" in t or t.startswith("("):
            return None  # field-typed, callbacks and records are not primitives
        base = t.split("<", 1)[0].rstrip("?").strip()
        return [base] if base else None

    def member_refs(self, cls: Class) -> dict[str, set[str]]:
        """Bare or `this.`-qualified references to the class's own fields and
        methods in each method body."""
        members = {m.name.rstrip("=") for m in cls.methods if m.kind != "constructor"}
        body = None
        if cls.node is not None:
            body = cls.node.child_by_field_name("body") or next(
                (c for c in cls.node.named_children if c.type in _BODIES), None
            )
        if body is not None:
            for decl in body.named_children:
                if decl.type != "declaration":
                    continue
                for n in iter_nodes(decl):
                    if n.type in ("initialized_identifier", "static_final_declaration"):
                        ident = next((c for c in n.children if c.type == "identifier"), None)
                        if ident is not None:
                            members.add(text(ident))
        refs: dict[str, set[str]] = {}
        for fn in cls.methods:
            names: set[str] = set()
            stack = list(fn.body.children)
            while stack:
                n = stack.pop()
                if n.type == "local_function_declaration":
                    continue
                if n.type == "identifier":
                    name = text(n)
                    if name in members and not _is_member_of_other(n):
                        names.add(name)
                stack.extend(n.children)
            refs[fn.qualname] = names
        return refs

    def _test_unit(self, fexpr: Node) -> tuple[str, Node] | None:
        call = _closure_call(fexpr)
        if call is None or call[0] not in self.test_calls:
            return None
        return call

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

    def role(self, node: Node) -> Role | None:
        t = node.type
        if t == "block":
            parent = node.parent
            # handler bodies are siblings of catch_clause/on-type inside try
            if parent is not None and parent.type == "try_statement":
                body = parent.child_by_field_name("body")
                if body is None or body.id != node.id:
                    return Role.CATCH
            return None
        if t in ("break_statement", "continue_statement"):
            return Role.JUMP_LABEL if any(c.type == "identifier" for c in node.children) else None
        if t == "function_expression" and self._test_unit(node) is not None:
            return Role.STOP
        r = self.roles.get(t)
        if t == "switch_expression_case":
            pattern = node.named_children[0] if node.named_child_count else None
            if pattern is not None and text(pattern).strip() == "_":
                return None  # wildcard arm is the default
        return r

    def bool_token(self, node: Node) -> str | None:
        if node.type == "logical_and_operator":
            return "and"
        if node.type == "logical_or_operator":
            return "or"
        return None

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
            if node.type == "local_function_declaration" or (
                node.type == "function_expression" and self._test_unit(node) is not None
            ):
                continue
            stack.extend((c, depth) for c in node.children)
        return best

    def _extract(self, module: Module) -> None:
        self._scan_members(module, module.root, cls=None, prefix="")

    def _scan_members(self, module: Module, parent: Node, cls: Class | None, prefix: str) -> None:
        kids = parent.named_children
        for i, node in enumerate(kids):
            if node.type in _CONTAINERS:
                self._add_container(module, node)
                continue
            sig = _signature_of(node)
            if sig is None and node.type == "declaration":
                # Body-less constructors (initializer lists) and abstract methods
                # are not scored, but closures in initializers may declare local
                # functions, e.g. `super(builder: (f) { void h() {...} ... })`.
                ctor = next((c for c in node.named_children if c.type in _SIGNATURES), None)
                owner = _name(ctor) if ctor is not None else "<init>"
                self._add_local_functions(module, node, f"{owner}.")
                continue
            if sig is None:
                continue
            nxt = kids[i + 1] if i + 1 < len(kids) else None
            if nxt is None or nxt.type != "function_body":
                continue  # abstract / external declaration
            self._add_function(module, sig, node, nxt, cls, prefix)

    def _add_container(self, module: Module, node: Node) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            name_node = next(
                (c for c in node.named_children if c.type in ("identifier", "type_identifier")),
                None,
            )
        cls = Class(
            name=text(name_node) or "<extension>",
            kind=_CONTAINERS[node.type],
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            node=node,
        )
        module.classes.append(cls)
        body = node.child_by_field_name("body") or next(
            (c for c in node.named_children if c.type in _BODIES), None
        )
        if body is not None:
            self._scan_members(module, body, cls=cls, prefix=f"{cls.name}.")

    def _add_function(
        self,
        module: Module,
        sig: Node,
        outer: Node,
        body: Node,
        cls: Class | None,
        prefix: str,
    ) -> None:
        kind = _SIGNATURES[sig.type]
        name = _name(sig)
        if kind == "function" and cls is not None:
            kind = "method"
        # constructor names already carry the class: `Repo`, `Repo.named`
        qualname = name if kind == "constructor" else f"{prefix}{name}"
        fn = Function(
            name=name,
            qualname=qualname,
            kind=kind,
            start_line=outer.start_point[0] + 1,
            end_line=body.end_point[0] + 1,
            params=_params(sig),
            body=body,
            class_name=cls.name if cls else None,
        )
        (cls.methods if cls is not None else module.functions).append(fn)
        self._add_local_functions(module, body, f"{qualname}.")

    def _add_test_unit(
        self, module: Module, fexpr: Node, callee: str, args: Node, prefix: str
    ) -> None:
        body = fexpr.child_by_field_name("body")
        if body is None:
            return
        desc = _description(args)
        name = f'{callee}("{desc}")' if desc else callee
        fn = Function(
            name=name,
            qualname=f"{prefix}{name}",
            kind="test",
            start_line=args.parent.parent.prev_sibling.start_point[0] + 1,
            end_line=fexpr.end_point[0] + 1,
            params=_params(fexpr),
            body=body,
        )
        module.functions.append(fn)
        self._add_local_functions(module, body, f"{fn.qualname}.")

    def _add_local_functions(self, module: Module, body: Node, prefix: str) -> None:
        """Local named functions and test closures are scored on their own;
        other closures stay part of the enclosing function."""
        stack = list(body.children)
        while stack:
            n = stack.pop()
            if n.type == "function_expression":
                unit = self._test_unit(n)
                if unit is not None:
                    self._add_test_unit(module, n, unit[0], unit[1], prefix)
                    continue
            if n.type == "local_function_declaration":
                lam = next((c for c in n.named_children if c.type == "lambda_expression"), None)
                if lam is None:
                    continue
                sig = next((c for c in lam.named_children if c.type == "function_signature"), None)
                fbody = next((c for c in lam.named_children if c.type == "function_body"), None)
                if sig is not None and fbody is not None:
                    self._add_function(module, sig, n, fbody, None, prefix)
                continue  # its own body is handled by the recursive call
            stack.extend(n.children)
