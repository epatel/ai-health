"""Java adapter (tree-sitter `java` grammar).

Grammar notes (see cards/parser-choice.md):
- an else-if is the `alternative` `if_statement` itself (no wrapper);
- `switch_expression` is both the switch statement and expression; its arms
  are `switch_rule` (`->`) or `switch_block_statement_group` (`:`) in a
  `switch_block`, the default arm a `switch_label` holding `default`;
- a constructor body is a `constructor_body`, not a `block`;
- an anonymous class is a `class_body` inside `object_creation_expression`,
  an enum constant with methods has a `class_body` too.

Units: methods, constructors (record compact constructors too), static and
instance initializers, and lambdas bound to a name (`Runnable r = () -> ..`,
fields too). Methods of anonymous classes and enum constants are units of
their own, named after the enclosing unit; other lambdas are closures.

Java 25 syntax the grammar (tree-sitter-java 0.23) cannot parse is rewritten
before parsing, keeping length and line breaks (`prepare`): `import module`
and statements before `super(..)` / `this(..)`.
"""

from __future__ import annotations

import re
from typing import ClassVar

from tree_sitter import Node

from aihealth.lang.base import (
    ExtractionScope,
    LanguageAdapter,
    member_refs_by_method,
    names_in,
    scan_units,
    text,
    type_leaves,
)
from aihealth.metrics import Role
from aihealth.model import Class, Function, Module, Param

_CLASSES = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
}
_METHODS = ("method_declaration", "constructor_declaration", "compact_constructor_declaration")
_LOOPS = ("for_statement", "enhanced_for_statement", "while_statement", "do_statement")
_ARMS = ("switch_rule", "switch_block_statement_group")


def _line(node: Node) -> int:
    return node.start_point[0] + 1


def _end_line(node: Node) -> int:
    return node.end_point[0] + 1


def _name(node: Node | None) -> str:
    return text(node.child_by_field_name("name")) if node is not None else ""


def _type_of(node: Node | None) -> str:
    return node.type if node is not None else ""


def _child(node: Node | None, *types: str) -> Node | None:
    if node is None:
        return None
    return next((c for c in node.named_children if c.type in types), None)


# --- source preparation: Java 25 syntax the grammar lacks --------------------------------

# Strings, text blocks, chars and comments match first and stay as they are.
_KEEP = (
    rb'(?P<keep>"""[\s\S]*?"""|"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\''
    rb"|//[^\n]*|/\*[\s\S]*?\*/)"
)
# `import module java.base;` (JEP 511): the word `module` is blanked
# `super(..)` / `this(..)` after other statements (JEP 513): read as plain calls
# a type annotation before varargs or array brackets (`Object @Nullable ... args`,
# `int @Nullable []`, JSpecify style): blanked, the grammar only takes it elsewhere
_REWRITE = re.compile(
    _KEEP
    + rb"|(?<=import )(?P<module>module)(?=\s+[\w.]+\s*;)"
    + rb"|(?<![\w$.])(?P<ctor>super|this)(?=\s*\()"
    + rb"|(?P<blank>@[\w.]+(?:\([^()]*\))?)(?=\s*(?:\.\.\.|\[))"
)
_RENAMED = {b"super": b"s_per", b"this": b"t_is"}


def _rewritten(match: re.Match[bytes]) -> bytes:
    if match["keep"]:
        return match["keep"]
    if match["ctor"]:
        return _RENAMED[match["ctor"]]
    blank = match["module"] or match["blank"]
    return re.sub(rb"[^\r\n]", b" ", blank)


def prepare(source: bytes) -> bytes:
    """``source`` with `import module` and explicit constructor calls made
    parseable; same length and line breaks."""
    return _REWRITE.sub(_rewritten, source)


# --- parameters ---------------------------------------------------------------------


def _spread_param(node: Node) -> Param:
    """`String... rest`: the type is an unnamed child, the name a declarator."""
    type_node = next(
        (c for c in node.named_children if c.type not in ("modifiers", "variable_declarator")),
        None,
    )
    decl = _child(node, "variable_declarator")
    type_text = f"{text(type_node)}..." if type_node is not None else None
    return Param(name=_name(decl), type_text=type_text, variadic=True)


def _param(node: Node) -> Param | None:
    if node.type == "formal_parameter":
        type_node = node.child_by_field_name("type")
        return Param(name=_name(node), type_text=text(type_node) or None)
    if node.type == "spread_parameter":
        return _spread_param(node)
    if node.type == "identifier":  # lambda `(a, b) -> ..`
        return Param(name=text(node))
    return None  # receiver parameter (`Foo this`), comments


def _params(fn: Node) -> list[Param]:
    plist = fn.child_by_field_name("parameters")
    if plist is None:
        return []
    if plist.type == "identifier":  # lambda `x -> ..`
        return [Param(name=text(plist))]
    return [p for p in (_param(c) for c in plist.named_children) if p is not None]


# --- roles that depend on context -------------------------------------------------------


def _binary_role(node: Node) -> Role | None:
    op = node.child_by_field_name("operator")
    return Role.BOOL_EXPR if op is not None and op.type in ("&&", "||") else None


def _jump_role(node: Node) -> Role | None:
    """`break label` / `continue label`; unlabeled jumps add nothing."""
    return Role.JUMP_LABEL if _child(node, "identifier") is not None else None


def _is_default_label(label: Node) -> bool:
    return any(c.type == "default" for c in label.children)


def _arm_role(node: Node) -> Role | None:
    """A switch arm; `default` (also `case null, default`) adds nothing."""
    labels = [c for c in node.named_children if c.type == "switch_label"]
    return None if any(_is_default_label(lb) for lb in labels) else Role.CASE


_CONTEXT_ROLES = {
    "binary_expression": _binary_role,
    "break_statement": _jump_role,
    "continue_statement": _jump_role,
    **dict.fromkeys(_ARMS, _arm_role),
}


# --- types ----------------------------------------------------------------------------

_ANNOTATION = re.compile(r"@[\w.]+(\([^()]*\))?\s*")


def _type_leaves(t: str) -> list[str] | None:
    """Innermost type names: `List<Order>` -> [Order], `Map<String, Integer>`
    -> [String, Integer], `int[]` / `String...` -> [int] / [String],
    `java.util.Date` -> [Date], `? extends Shape` -> [Shape]."""
    t = _ANNOTATION.sub("", t).replace("[]", "").replace("...", "").strip()
    leaves: list[str] = []
    for leaf in type_leaves(t, "<", ">"):
        leaf = leaf.removeprefix("?").strip().removeprefix("extends").removeprefix("super")
        leaf = leaf.split("&")[0].strip()
        if leaf:
            leaves.append(leaf.rsplit(".", 1)[-1])
    return leaves or None


# --- naming -----------------------------------------------------------------------------


def _lambda_name(fn: Node) -> str | None:
    """`f` for a lambda bound to a name: `Runnable f = () -> ..` (field or
    local) or `f = () -> ..`."""
    parent = fn.parent
    if _type_of(parent) == "variable_declarator":
        return _name(parent) or None
    if _type_of(parent) == "assignment_expression" and parent.child_by_field_name("right") == fn:
        left = parent.child_by_field_name("left")
        return text(left) if _type_of(left) == "identifier" else None
    return None


def _anonymous_type(body: Node) -> str:
    """`Comparator` for `new Comparator<>() { .. }`."""
    creation = body.parent
    type_text = text(creation.child_by_field_name("type")) if creation is not None else ""
    return type_text.split("<", 1)[0].rsplit(".", 1)[-1] or "anonymous"


def _is_member(scope: ExtractionScope, node: Node) -> bool:
    """Declared in a class, an anonymous class or an enum constant body."""
    return scope.cls is not None or _type_of(node.parent) == "class_body"


def _unit_label(node: Node, class_name: str) -> tuple[str, str]:
    """(name, kind) of a method-like declaration."""
    if node.type == "method_declaration":
        return _name(node), "function"
    simple = class_name.rsplit(".", 1)[-1] or _name(node)
    return simple, "constructor"


# --- cohesion ---------------------------------------------------------------------------


def _is_static(decl: Node) -> bool:
    mods = _child(decl, "modifiers")
    return mods is not None and any(c.type == "static" for c in mods.children)


def _class_body(cls: Class) -> Node | None:
    return cls.node.child_by_field_name("body") if cls.node is not None else None


def _instance_fields(cls: Class) -> set[str]:
    """Non-static fields, plus a record's components."""
    fields = {_name(p) for p in _record_components(cls)}
    for decl in _class_body(cls).named_children if _class_body(cls) is not None else ():
        if decl.type == "enum_body_declarations":
            fields |= _fields_in(decl)
        elif decl.type == "field_declaration" and not _is_static(decl):
            fields |= {_name(d) for d in decl.children_by_field_name("declarator")}
    return fields


def _fields_in(body: Node) -> set[str]:
    return {
        _name(d)
        for decl in body.named_children
        if decl.type == "field_declaration" and not _is_static(decl)
        for d in decl.children_by_field_name("declarator")
    }


def _record_components(cls: Class) -> list[Node]:
    if cls.node is None or cls.node.type != "record_declaration":
        return []
    plist = cls.node.child_by_field_name("parameters")
    return [c for c in plist.named_children if c.type == "formal_parameter"] if plist else []


def _is_foreign(ident: Node) -> bool:
    """`other.x` / `other.m()` (not on `this`), or a declaration's own name."""
    parent = ident.parent
    if parent is None:
        return True
    if parent.type in ("field_access", "method_invocation"):
        obj = parent.child_by_field_name("object")
        is_member = parent.child_by_field_name("field" if parent.type == "field_access" else "name")
        if is_member == ident:
            return obj is not None and obj.type != "this"
        return False
    return parent.type in ("variable_declarator", "formal_parameter", "method_declaration")


def _is_own_field(node: Node | None) -> bool:
    """`x` or `this.x`."""
    if _type_of(node) == "field_access":
        return _type_of(node.child_by_field_name("object")) == "this"
    return _type_of(node) == "identifier"


def _is_field_store(stmt: Node) -> bool:
    """`x = value;` / `this.x = value;` with a plain name on the right."""
    expr = stmt.named_children[0] if stmt.type == "expression_statement" else None
    if _type_of(expr) != "assignment_expression":
        return False
    right = expr.child_by_field_name("right")
    return _is_own_field(expr.child_by_field_name("left")) and _type_of(right) == "identifier"


def _returns(stmt: Node, kinds: tuple[str, ...]) -> bool:
    value = stmt.named_children[0] if stmt.named_child_count else None
    return stmt.type == "return_statement" and _type_of(value) in kinds


def _is_accessor(stmts: list[Node]) -> bool:
    """A getter (`return x;`), a setter (`this.x = x;`) or a fluent setter
    (`this.x = x; return this;`): each touches one field by design."""
    if len(stmts) == 1:
        only = stmts[0]
        is_getter = _returns(only, ("identifier", "field_access")) and _is_own_field(
            only.named_children[0]
        )
        return is_getter or _is_field_store(only)
    return len(stmts) == 2 and _is_field_store(stmts[0]) and _returns(stmts[1], ("this",))


def _is_member_ref(node: Node, members: set[str]) -> bool:
    """An identifier naming one of the class's members (not `other.x`)."""
    return node.type == "identifier" and text(node) in members and not _is_foreign(node)


def _member_names_in(body: Node, members: set[str]) -> set[str]:
    return names_in(body, frozenset(_CLASSES), lambda n: _is_member_ref(n, members))


class JavaAdapter(LanguageAdapter):
    language = "java"
    extensions = (".java",)
    comment_types = frozenset({"line_comment", "block_comment"})
    roles: ClassVar[dict[str, Role]] = {
        "if_statement": Role.IF,
        **dict.fromkeys(_LOOPS, Role.LOOP),
        "switch_expression": Role.SWITCH,
        "catch_clause": Role.CATCH,
        "ternary_expression": Role.TERNARY,
        "lambda_expression": Role.LAMBDA,
        **dict.fromkeys(_METHODS, Role.STOP),
        **dict.fromkeys(_CLASSES, Role.STOP),
        "static_initializer": Role.STOP,
    }
    block_types = frozenset({"block", "constructor_body"})
    bool_transparent = frozenset({"parenthesized_expression", "unary_expression"})
    primitive_types = frozenset(
        {
            *("byte", "short", "int", "long", "float", "double", "char"),
            *("Byte", "Short", "Integer", "Long", "Float", "Double", "Character"),
            *("String", "CharSequence", "Object", "BigDecimal", "BigInteger"),
            *("List", "Set", "Map", "Collection", "Iterable"),
        }
    )
    neutral_types = frozenset({"boolean", "Boolean"})
    literal_types = frozenset(
        {
            *(f"{p}_integer_literal" for p in ("decimal", "hex", "octal", "binary")),
            *(f"{p}_floating_point_literal" for p in ("decimal", "hex")),
            "character_literal",
            "string_literal",
            "true",
            "false",
            "null_literal",
        }
    )

    def __init__(self) -> None:
        super().__init__()
        self._units: set[int] = set()  # ids of lambdas scored as units

    def _prepare(self, source: bytes, language: str) -> bytes:
        return prepare(source)

    # --- metric hooks --------------------------------------------------------------------

    def role(self, node: Node) -> Role | None:
        if node.type == "lambda_expression" and node.id in self._units:
            return Role.STOP
        if node.type == "block" and _type_of(node.parent) == "class_body":
            return Role.STOP  # instance initializer: a unit of its own
        contextual = _CONTEXT_ROLES.get(node.type)
        return contextual(node) if contextual is not None else self.roles.get(node.type)

    def bool_token(self, node: Node) -> str | None:
        return {"&&": "and", "||": "or"}.get(node.type)

    def unwrap_loop(self, stmt: Node) -> Node | None:
        if stmt.type == "labeled_statement" and stmt.named_child_count:
            stmt = stmt.named_children[-1]
        return super().unwrap_loop(stmt)

    def is_recursive_call(self, node: Node, fn_name: str) -> bool:
        """`f(..)` or `this.f(..)`."""
        if node.type != "method_invocation" or _name(node) != fn_name:
            return False
        obj = node.child_by_field_name("object")
        return obj is None or obj.type == "this"

    # --- module/class smell hooks --------------------------------------------------------

    def base_types(self, type_text: str) -> list[str] | None:
        return _type_leaves(type_text)

    def member_refs(self, cls: Class) -> dict[str, set[str]]:
        """Fields and methods used bare or through `this` per method.
        Interfaces have no state, and accessors (getters, setters, fluent
        setters) touch one field by design: neither is judged."""
        if cls.kind == "interface":
            return {}
        refs = member_refs_by_method(cls, _instance_fields(cls), _member_names_in)
        for fn in cls.methods:
            if _is_accessor(self.statements(fn.body)):
                refs[fn.qualname] = set()
        return refs

    # --- extraction ---------------------------------------------------------------------

    def _extract(self, module: Module) -> None:
        self._units = set()
        self._scan(ExtractionScope(module), module.root)

    def _scan(self, scope: ExtractionScope, parent: Node) -> None:
        scan_units(scope, parent, self._add_unit)

    def _add_unit(self, scope: ExtractionScope, node: Node) -> bool:
        """Add ``node`` if it is a unit or a class; True if it was a boundary
        (its insides belong to that unit)."""
        if node.type in _METHODS:
            cls_name = scope.cls.name if scope.cls is not None else scope.prefix.rstrip(".")
            self._add_function(scope, node, _unit_label(node, cls_name))
        elif node.type in _CLASSES:
            self._add_class(scope, node)
        elif node.type == "static_initializer":
            self._add_function(scope, node, ("static", "function"), _child(node, "block"))
        elif node.type == "block" and _type_of(node.parent) == "class_body":
            self._add_function(scope, node, ("init", "function"), node)
        elif node.type == "class_body":  # anonymous class, enum constant body
            self._scan(self._anonymous_scope(scope, node), node)
        elif node.type == "lambda_expression":
            return self._add_lambda(scope, node)
        else:
            return False
        return True

    def _anonymous_scope(self, scope: ExtractionScope, body: Node) -> ExtractionScope:
        owner = body.parent
        name = _name(owner) if _type_of(owner) == "enum_constant" else _anonymous_type(body)
        return ExtractionScope(scope.module, None, f"{scope.prefix}{name}.")

    def _add_lambda(self, scope: ExtractionScope, fn: Node) -> bool:
        name = _lambda_name(fn)
        if name is None:
            return False  # an inline callback: part of the enclosing unit
        self._units.add(fn.id)
        self._add_function(scope, fn, (name, "function"))
        return True

    def _add_class(self, scope: ExtractionScope, node: Node) -> None:
        cls = scope.add_class(_name(node), _CLASSES[node.type], node)
        body = node.child_by_field_name("body")
        if body is not None:  # an enum's members are found in enum_body_declarations
            self._scan(scope.in_class(cls), body)

    def _add_function(
        self, scope: ExtractionScope, node: Node, label: tuple[str, str], body: Node | None = None
    ) -> None:
        """Add ``node`` as a unit; ``label`` is (name, kind)."""
        name, kind = label
        body = body or node.child_by_field_name("body")
        if body is None:
            return  # abstract or interface method, native method
        if kind == "function" and _is_member(scope, node):
            kind = "method"
        unit = Function(
            name=name,
            qualname=f"{scope.prefix}{name}",
            kind=kind,
            start_line=_line(node),
            end_line=_end_line(node),
            params=_params(node) if node.type != "block" else [],
            body=body,
            class_name=scope.cls.name if scope.cls else None,
        )
        scope.add(unit)
        self._scan(scope.nested(unit.qualname), body)
