"""Dart adapter (tree-sitter `dart` grammar).

Grammar quirks handled here (see cards/parser-choice.md):
- a function body is the *next sibling* of its signature, not a child;
- in class bodies the signature is wrapped in `method_signature`;
- local functions are `local_function_declaration > lambda_expression`.
"""

from __future__ import annotations

from tree_sitter import Node

from aihealth.lang.base import LanguageAdapter, iter_nodes, text
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


class DartAdapter(LanguageAdapter):
    language = "dart"
    extensions = (".dart",)
    comment_types = frozenset({"comment", "documentation_comment"})

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

    def _add_local_functions(self, module: Module, body: Node, prefix: str) -> None:
        """Local named functions are scored on their own; closures are not."""
        stack = list(body.children)
        while stack:
            n = stack.pop()
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
