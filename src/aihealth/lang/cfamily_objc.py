"""Objective-C parts of the C-family adapter: selectors, method parameters
and cohesion (instance variables, properties, `self` and `[self m]`).

Grammar notes: `method_definition` has no fields. The selector pieces are
its direct `identifier` children, the parameters are `method_parameter`
children (each a `method_type` and an `identifier`), and the body is a
`compound_statement`. A message send's selector pieces are its `method`
fields.
"""

from __future__ import annotations

from tree_sitter import Node

from aihealth.lang.base import iter_nodes, text
from aihealth.lang.cfamily_decl import source_text
from aihealth.model import Class, Param


def _first(node: Node | None, kind: str) -> Node | None:
    if node is None:
        return None
    return next((c for c in node.named_children if c.type == kind), None)


def method_body(method: Node) -> Node | None:
    return _first(method, "compound_statement")


def _method_param(node: Node, source: bytes) -> Param:
    """Selector parameters have labels, so they count as named (half)."""
    type_text = source_text(_first(node, "method_type"), source).strip("() ")
    return Param(name=text(_first(node, "identifier")), type_text=type_text or None, named=True)


def method_params(method: Node, source: bytes) -> list[Param]:
    params = [
        _method_param(c, source) for c in method.named_children if c.type == "method_parameter"
    ]
    if any(c.type == "..." for c in method.children):
        params.append(Param(name="...", variadic=True))
    return params


def selector(method: Node) -> str:
    """`greet:times:` for `- (id)greet:(id)who times:(int)n`, `run` for `- run`."""
    pieces = [text(c) for c in method.named_children if c.type == "identifier"]
    if not any(c.type == "method_parameter" for c in method.named_children):
        return pieces[0] if pieces else "?"
    return "".join(f"{p}:" for p in pieces)


def message_selector(msg: Node) -> str:
    """Selector of `[obj greet:x times:2]` (`greet:times:`) or `[obj run]`."""
    pieces = [text(c) for c in msg.children_by_field_name("method")]
    has_args = msg.named_child_count > len(pieces) + 1
    return "".join(f"{p}:" for p in pieces) if has_args else "".join(pieces)


def implementation_name(node: Node) -> str:
    return text(_first(node, "identifier"))


# --- cohesion ---------------------------------------------------------------------------


def _ivars(implementation: Node) -> set[str]:
    """Instance variable names as written (`_count`)."""
    names: set[str] = set()
    for block in (c for c in implementation.named_children if c.type == "instance_variables"):
        names |= {text(n) for n in iter_nodes(block) if _is_declared_name(n)}
    return names


def _is_declared_name(node: Node) -> bool:
    """An identifier that names a variable, not a type."""
    parent = node.parent
    return node.type == "identifier" and parent is not None and parent.type != "type_name"


def _property_name(prop: Node) -> str | None:
    names = [text(n) for n in iter_nodes(prop) if n.type == "identifier"]
    return names[-1] if names else None


def _properties(implementation: Node, class_name: str) -> set[str]:
    """Properties declared by an `@interface` of the class in this file."""
    root = implementation.parent
    props: set[str] = set()
    for iface in root.named_children if root is not None else []:
        if iface.type == "class_interface" and implementation_name(iface) == class_name:
            names = map(_property_name, _children(iface, "property_declaration"))
            props |= {n for n in names if n}
    return props


def _children(node: Node, kind: str) -> list[Node]:
    return [c for c in node.named_children if c.type == kind]


def member_refs(cls: Class) -> dict[str, set[str]]:
    """Per method: the instance variables (`_x`, also a property's backing
    `_x`), properties (`self.x`) and methods (`[self m]`) it uses. Members
    are keyed without the leading `_`."""
    node = cls.node  # a class_implementation, or an ERROR holding `@implementation`
    props = _properties(node, cls.name.rpartition(".")[2])
    ivars = _ivars(node) | {f"_{p}" for p in props}
    members = {v.lstrip("_") for v in ivars} | props | {m.name for m in cls.methods}
    return {fn.qualname: _refs(fn.body, ivars) & members for fn in cls.methods}


def _refs(body: Node, ivars: set[str]) -> set[str]:
    refs: set[str] = set()
    for n in iter_nodes(body):
        if n.type == "identifier" and text(n) in ivars:
            refs.add(text(n).lstrip("_"))
        elif n.type == "field_expression" and text(n.child_by_field_name("argument")) == "self":
            refs.add(text(n.child_by_field_name("field")))
        elif n.type == "message_expression" and text(n.child_by_field_name("receiver")) == "self":
            refs.add(message_selector(n))
    return refs
