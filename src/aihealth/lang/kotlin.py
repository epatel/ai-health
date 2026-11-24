"""Kotlin adapter (tree-sitter `kotlin` grammar).

Grammar notes (see cards/parser-choice.md):
- declarations have no `name` / `body` fields: names are the first
  `simple_identifier` / `type_identifier` child, bodies a `function_body`
  (or, for `init` blocks and secondary constructors, a `statements` child);
- an else-if is wrapped: the `alternative` is a `control_structure_body`
  holding a single `if_expression`;
- a getter or setter on its own line is a *sibling* of its
  `property_declaration`, on the same line its child;
- a loop label (`outer@`) is a `label` sibling before the loop;
- a default value is a sibling after its `parameter`, `vararg` a
  `parameter_modifiers` sibling before it.

Units: functions (top-level, members, extensions, local), secondary
constructors, `init` blocks, property getters and setters, and lambdas or
anonymous functions bound to a name (`val f = { .. }`). Methods of object
expressions are units named after the enclosing unit; closures passed to
test-framework calls (Kotest, Spek) are units of kind `test`.

Lambdas passed to a capitalised call (Compose `Column { }`, `Card(onClick =
{ })`) are layout and add no cognitive nesting, like SwiftUI view builders;
effect calls (`LaunchedEffect`, ...) and lowercase calls (`let`, `map`,
`launch`) keep the closure penalty. `?.`, `?:` and `!!` add no complexity.
"""

from __future__ import annotations

from typing import ClassVar, NamedTuple

from tree_sitter import Node

from aihealth.lang.base import (
    ExtractionScope,
    LanguageAdapter,
    call_nesting,
    member_refs_by_method,
    names_in,
    text,
    type_leaves,
    walk_units,
)
from aihealth.metrics import Role
from aihealth.model import Class, Function, Module, Param

_LOOPS = ("for_statement", "while_statement", "do_while_statement")
_CLASS_NODES = ("class_declaration", "object_declaration", "companion_object")
_DECLARATIONS = frozenset({"function_declaration", *_CLASS_NODES})
_ACCESSORS = {"getter": ("getter", ""), "setter": ("setter", "=")}
_SKIPPED_SIBLINGS = frozenset({*_ACCESSORS, "line_comment", "multiline_comment"})

# capitalised calls whose lambda runs code (side effects), not layout
_EFFECT_CALLS = frozenset(
    {"LaunchedEffect", "DisposableEffect", "SideEffect", "Thread", "Runnable", "Callable"}
)

# Kotest / Spek specs: a closure passed to one of these is a test unit
DEFAULT_TEST_CALLS = frozenset(
    {
        *("describe", "context", "it", "test", "should", "feature", "scenario"),
        *("given", "Given", "When", "then", "Then", "xdescribe", "xcontext", "xit", "xtest"),
    }
)


class _Unit(NamedTuple):
    """What a unit is called and holds; its span comes from the node."""

    name: str
    kind: str
    body: Node
    params: list[Param]


def _line(node: Node) -> int:
    return node.start_point[0] + 1


def _end_line(node: Node) -> int:
    return node.end_point[0] + 1


def _type_of(node: Node | None) -> str:
    return node.type if node is not None else ""


def _child(node: Node | None, *types: str) -> Node | None:
    if node is None:
        return None
    return next((c for c in node.named_children if c.type in types), None)


def _has_token(node: Node, token: str) -> bool:
    return any(c.type == token for c in node.children)


def _modifier_words(decl: Node) -> list[str]:
    return text(_child(decl, "modifiers")).split()


# --- names ------------------------------------------------------------------------


def _simple_type(type_text: str) -> str:
    """`List<T>?` -> `List`, `kotlin.String` -> `String`."""
    return type_text.split("<", 1)[0].rstrip("?").strip().rsplit(".", 1)[-1]


def _function_name(fn: Node) -> str:
    """`f`, or `String.f` for an extension function."""
    name = text(_child(fn, "simple_identifier"))
    receiver = _child(fn, "receiver_type")
    return f"{_simple_type(text(receiver))}.{name}" if receiver is not None else name


def _property_name(decl: Node) -> str:
    var = _child(decl, "variable_declaration")
    return text(_child(var, "simple_identifier")) or "<property>"


def _class_kind(node: Node) -> str:
    if node.type != "class_declaration":
        return "companion object" if node.type == "companion_object" else "object"
    if _has_token(node, "interface"):
        return "interface"
    if _has_token(node, "enum"):
        return "enum"
    words = _modifier_words(node)
    flavour = next((w for w in ("data", "sealed", "value", "annotation") if w in words), "")
    return f"{flavour} class" if flavour else "class"


def _class_name(node: Node) -> str:
    name = _child(node, "type_identifier")
    if name is not None:
        return text(name)
    return "Companion" if node.type == "companion_object" else "<anonymous>"


# --- parameters ---------------------------------------------------------------------


def _param(node: Node) -> Param:
    type_node = next((c for c in node.named_children if c.type != "simple_identifier"), None)
    return Param(name=text(_child(node, "simple_identifier")), type_text=text(type_node) or None)


def _params(fn: Node) -> list[Param]:
    """Declared parameters. One with a default value counts half for Excess
    Arguments (`named`): callers usually skip it or pass it by name."""
    plist = _child(fn, "function_value_parameters")
    out: list[Param] = []
    vararg = False
    for c in plist.named_children if plist is not None else ():
        if c.type == "parameter":
            out.append(_param(c))
            out[-1].variadic = vararg
            vararg = False
        elif c.type == "parameter_modifiers":
            vararg = "vararg" in text(c).split()
        elif out and c.type not in ("line_comment", "multiline_comment"):
            out[-1].optional = out[-1].named = True
    return out


def _lambda_params(lam: Node) -> list[Param]:
    plist = _child(lam, "lambda_parameters")
    return [Param(name=text(p)) for p in plist.named_children] if plist is not None else []


# --- calls --------------------------------------------------------------------------


def _callee(call: Node) -> str:
    """`Column` for `Column { }`, `let` for `x?.let { }`."""
    target = call.named_children[0] if call.named_child_count else None
    if _type_of(target) == "navigation_expression":
        target = _child(target.named_children[-1], "simple_identifier")
    return text(target) if _type_of(target) == "simple_identifier" else ""


def _lambda_call(lam: Node) -> Node | None:
    """The call a lambda is passed to: trailing (`f { }`) or as an argument
    (`f(content = { })`)."""
    node = lam.parent
    while _type_of(node) in ("annotated_lambda", "value_argument", "value_arguments"):
        node = node.parent
    if _type_of(node) != "call_suffix":
        return None
    return node.parent if _type_of(node.parent) == "call_expression" else None


def _is_trailing(lam: Node) -> bool:
    return _type_of(lam.parent) == "annotated_lambda"


def _is_layout_call(call: Node) -> bool:
    """`Column(..)`, `Text(..)`: a capitalised callee that isn't an effect."""
    name = _callee(call)
    return bool(name) and name[0].isupper() and name not in _EFFECT_CALLS


def _description(call: Node) -> str:
    args = _child(_child(call, "call_suffix"), "value_arguments")
    lit = _child(_child(args, "value_argument"), "string_literal")
    desc = " ".join(text(lit).strip('"').split()) if lit is not None else ""
    return desc if len(desc) <= 60 else desc[:57] + "..."


def _bound_name(fn: Node) -> str | None:
    """`f` for `val f = { .. }` / `val f = fun(..) { .. }` (member, top-level
    or local)."""
    parent = fn.parent
    if _type_of(parent) != "property_declaration":
        return None
    return _property_name(parent)


# --- roles that depend on context -------------------------------------------------------


def _jump_role(node: Node) -> Role | None:
    """`break@l` / `continue@l`; other jumps (`return@l` too) add nothing."""
    first = node.children[0].type if node.child_count else ""
    return Role.JUMP_LABEL if first in ("break@", "continue@") else None


def _entry_role(node: Node) -> Role | None:
    """A `when` branch; `else ->` adds nothing."""
    return None if _has_token(node, "else") else Role.CASE


_CONTEXT_ROLES = {"jump_expression": _jump_role, "when_entry": _entry_role}


# --- types ----------------------------------------------------------------------------


def _strip_type(t: str) -> str:
    t = t.strip()
    while t.startswith("@"):  # annotations: `@Composable () -> Unit`
        t = t.partition(" ")[2].strip()
    for prefix in ("suspend ", "out ", "in "):
        t = t.removeprefix(prefix)
    return t.strip()


def _type_leaves(t: str) -> list[str] | None:
    """Innermost type names: `List<Order>` -> [Order], `Map<String, Int?>`
    -> [String, Int], `kotlin.Long` -> [Long]; function types are unknown."""
    t = _strip_type(t)
    if "->" in t:
        return None
    leaves = []
    for leaf in type_leaves(t, "<", ">"):
        leaf = _strip_type(leaf).rstrip("?").strip()
        if leaf and leaf != "*":
            leaves.append(leaf.rsplit(".", 1)[-1])
    if not leaves and "<" in t:  # star projections only: `List<*>` -> [List]
        return [_simple_type(t)]
    return leaves or None


# --- cohesion ---------------------------------------------------------------------------


def _class_body(cls: Class) -> Node | None:
    return _child(cls.node, "class_body", "enum_class_body")


def _constructor_properties(cls: Class) -> set[str]:
    """`val` / `var` parameters of the primary constructor."""
    ctor = _child(cls.node, "primary_constructor")
    params = ctor.named_children if ctor is not None else []
    return {
        text(_child(p, "simple_identifier"))
        for p in params
        if p.type == "class_parameter" and _child(p, "binding_pattern_kind") is not None
    }


def _instance_fields(cls: Class) -> set[str]:
    body = _class_body(cls)
    declared = body.named_children if body is not None else []
    props = {_property_name(d) for d in declared if d.type == "property_declaration"}
    return props | _constructor_properties(cls)


def _is_foreign(ident: Node) -> bool:
    """`other.x` (not on `this`), an argument name (`f(x = 1)`) or a
    declaration's own name."""
    parent = ident.parent
    if _type_of(parent) == "navigation_suffix":
        target = parent.parent.named_children[0] if parent.parent is not None else None
        return _type_of(target) != "this_expression"
    if _type_of(parent) == "value_argument":
        return parent.named_child_count > 1 and parent.named_children[0] == ident
    return _type_of(parent) in ("variable_declaration", "parameter", "function_declaration")


def _is_own_property(node: Node | None) -> bool:
    """`x` or `this.x`."""
    if _type_of(node) == "navigation_expression":
        return _type_of(node.named_children[0]) == "this_expression"
    return _type_of(node) == "simple_identifier"


def _is_store(node: Node | None) -> bool:
    """`x = value` / `this.x = value` / `field = value` with a plain name on
    the right."""
    if _type_of(node) != "assignment":
        return False
    target, value = node.named_children[0], node.named_children[-1]
    return _is_own_property(target.named_children[0]) and _type_of(value) == "simple_identifier"


def _single_expression(body: Node) -> Node | None:
    """The one expression of `= expr`, `{ expr }` or `{ return expr }`."""
    kids = body.named_children
    if len(kids) == 1 and kids[0].type == "statements":
        kids = kids[0].named_children
    if len(kids) != 1:
        return None
    only = kids[0]
    if only.type == "jump_expression" and text(only).startswith("return"):
        return only.named_children[0] if only.named_child_count == 1 else None
    return only


def _is_accessor(fn: Function) -> bool:
    """A property accessor, or a function that only returns or stores one
    of the class's properties (`fun type() = type`): each touches one field
    by design."""
    if fn.kind in ("getter", "setter"):
        return True
    expr = _single_expression(fn.body)
    return _is_own_property(expr) or _is_store(expr)


def _is_member_ref(node: Node, members: set[str]) -> bool:
    is_name = node.type == "simple_identifier" and text(node) in members
    return is_name and not _is_foreign(node)


def _member_names_in(body: Node, members: set[str]) -> set[str]:
    return names_in(body, _DECLARATIONS, lambda n: _is_member_ref(n, members))


# --- extraction helpers ------------------------------------------------------------------


def _accessor_body(acc: Node) -> Node | None:
    return _child(acc, "function_body")


def _statements_body(node: Node) -> Node:
    """`init { .. }` / `constructor(..) { .. }`: the statements, or the node
    itself when the block is empty."""
    return _child(node, "statements") or node


def _accessor_property(acc: Node) -> str:
    """The property a getter or setter belongs to: its parent, or the
    property declared before it when it stands on its own line."""
    if _type_of(acc.parent) == "property_declaration":
        return _property_name(acc.parent)
    prev = acc.prev_named_sibling
    while prev is not None and prev.type in _SKIPPED_SIBLINGS:
        prev = prev.prev_named_sibling
    return _property_name(prev) if _type_of(prev) == "property_declaration" else "<property>"


def _outside_class(scope: ExtractionScope) -> ExtractionScope:
    """``scope``'s naming without its class: units in initializers and
    headers are not methods."""
    return scope if scope.cls is None else ExtractionScope(scope.module, None, scope.prefix)


class KotlinAdapter(LanguageAdapter):
    language = "kotlin"
    extensions = (".kt",)
    comment_types = frozenset({"line_comment", "multiline_comment"})
    block_types = frozenset({"statements"})
    roles: ClassVar[dict[str, Role]] = {
        "if_expression": Role.IF,
        **dict.fromkeys(_LOOPS, Role.LOOP),
        "when_expression": Role.SWITCH,
        "catch_block": Role.CATCH,
        "anonymous_function": Role.LAMBDA,
        "conjunction_expression": Role.BOOL_EXPR,
        "disjunction_expression": Role.BOOL_EXPR,
        **dict.fromkeys(_DECLARATIONS, Role.STOP),
    }
    bool_transparent = frozenset({"parenthesized_expression", "prefix_expression"})
    condition_types = frozenset({"when_condition", "guard_condition"})
    primitive_types = frozenset(
        {
            *("Int", "Long", "Short", "Byte", "Float", "Double", "Char", "Number"),
            *("UInt", "ULong", "UShort", "UByte", "String", "CharSequence", "Any"),
            *("List", "MutableList", "Set", "MutableSet", "Map", "MutableMap"),
            *("Collection", "Iterable", "Sequence", "Array"),
            *(f"{p}Array" for p in ("Int", "Long", "Short", "Byte", "Float", "Double", "Char")),
        }
    )
    neutral_types = frozenset({"Boolean", "BooleanArray"})
    literal_types = frozenset(
        {
            *("integer_literal", "long_literal", "hex_literal", "bin_literal"),
            *("unsigned_literal", "real_literal", "character_literal", "string_literal"),
            *("boolean_literal", "null_literal"),
        }
    )

    def __init__(self, test_calls: frozenset[str] | None = None) -> None:
        super().__init__()
        self.test_calls = DEFAULT_TEST_CALLS if test_calls is None else frozenset(test_calls)
        self._units: set[int] = set()  # ids of lambdas scored as units

    # --- metric hooks --------------------------------------------------------------------

    def role(self, node: Node) -> Role | None:
        if node.type in ("lambda_literal", "anonymous_function") and node.id in self._units:
            return Role.STOP
        if node.type == "lambda_literal":
            call = _lambda_call(node)
            return None if call is not None and _is_layout_call(call) else Role.LAMBDA
        contextual = _CONTEXT_ROLES.get(node.type)
        return contextual(node) if contextual is not None else self.roles.get(node.type)

    def bool_token(self, node: Node) -> str | None:
        return {"&&": "and", "||": "or"}.get(node.type)

    def else_if(self, node: Node) -> Node | None:
        """`else if`: the alternative body holds just an `if_expression`."""
        kids = node.named_children
        is_wrapped = node.type == "control_structure_body" and len(kids) == 1
        return kids[0] if is_wrapped and kids[0].type == "if_expression" else None

    def statements(self, body: Node) -> list[Node]:
        return [s for s in super().statements(body) if s.type != "label"]

    def unwrap_loop(self, stmt: Node) -> Node | None:
        if self.role(stmt) is not Role.LOOP:
            return None
        return _child(stmt, "control_structure_body")

    def is_recursive_call(self, node: Node, fn_name: str) -> bool:
        """`f(..)` or `this.f(..)`."""
        if node.type != "call_expression" or _callee(node) != fn_name.rsplit(".", 1)[-1]:
            return False
        target = node.named_children[0]
        return target.type == "simple_identifier" or _type_of(target.named_children[0]) in (
            "this_expression",
        )

    def widget_depth(self, body: Node) -> int:
        """Deepest nesting of capitalised calls (`Column { Row { Text() } }`)."""
        return call_nesting(body, _is_layout_call, self.role)

    def hint(self, smell: str) -> str | None:
        if smell == "deep_widget_tree":
            return "Extract subtrees into smaller `@Composable` functions."
        return None

    # --- module/class smell hooks --------------------------------------------------------

    def base_types(self, type_text: str) -> list[str] | None:
        return _type_leaves(type_text)

    def member_refs(self, cls: Class) -> dict[str, set[str]]:
        """Properties and methods used bare or through `this` per method.
        Interfaces have no state, and accessors touch one property by design:
        neither is judged."""
        if cls.kind == "interface":
            return {}
        return member_refs_by_method(cls, _instance_fields(cls), _member_names_in, _is_accessor)

    # --- extraction ---------------------------------------------------------------------

    def _extract(self, module: Module) -> None:
        self._units = set()
        self._scan_members(ExtractionScope(module), module.root)

    def _scan_members(self, scope: ExtractionScope, container: Node) -> None:
        for node in container.named_children:
            if not self._add_unit(scope, node):
                walk_units(node, lambda n: self._add_unit(_outside_class(scope), n))

    def _scan_nested(self, scope: ExtractionScope, node: Node) -> None:
        """Units at or below ``node`` (an initializer, a statement, a class header)."""
        nested = _outside_class(scope)
        if not self._add_unit(nested, node):
            walk_units(node, lambda n: self._add_unit(nested, n))

    def _add_unit(self, scope: ExtractionScope, node: Node) -> bool:
        """Add ``node`` if it is a unit or a class; True if it was a boundary
        (its insides belong to that unit)."""
        if node.type == "function_declaration":
            self._add_function(scope, node)
        elif node.type in _ACCESSORS:
            self._add_accessor(scope, node, _accessor_property(node))
        elif node.type in _CLASS_NODES:
            self._add_class(scope, node)
        elif node.type == "secondary_constructor":
            self._add_block(scope, node, _params(node))
        elif node.type == "anonymous_initializer":
            self._add_block(scope, node, [])
        elif node.type == "property_declaration":
            return self._add_property(scope, node)
        elif node.type in ("object_literal", "enum_entry"):
            self._scan_object(scope, node)
        elif node.type in ("lambda_literal", "anonymous_function"):
            return self._add_closure(scope, node)
        else:
            return False
        return True

    def _add_class(self, scope: ExtractionScope, node: Node) -> None:
        cls = scope.add_class(_class_name(node), _class_kind(node), node)
        inner = scope.in_class(cls)
        for part in node.named_children:
            if part.type in ("class_body", "enum_class_body"):
                self._scan_members(inner, part)
            else:  # header: `FunSpec({ test(..) { } })`, constructor defaults
                self._scan_nested(inner, part)

    def _scan_object(self, scope: ExtractionScope, node: Node) -> None:
        """Methods of an object expression or an enum constant are named
        after the enclosing unit and the supertype or constant
        (`start.Runnable.run`, `Status.OPEN.next`), or the property it is
        bound to (`ticker.Runnable.run`)."""
        body = _child(node, "class_body")
        if body is None:
            nested = _outside_class(scope)  # enum constant arguments
            walk_units(node, lambda n: self._add_unit(nested, n))
            return
        if node.type == "enum_entry":
            name = text(_child(node, "simple_identifier"))
        else:
            spec = _child(node, "delegation_specifier")
            name = _simple_type(text(spec).split("(", 1)[0]) if spec is not None else "object"
            if _type_of(node.parent) == "property_declaration":  # `val ticker = object : ..`
                name = f"{_property_name(node.parent)}.{name}"
        self._scan_members(ExtractionScope(scope.module, None, f"{scope.prefix}{name}."), body)

    def _add_property(self, scope: ExtractionScope, node: Node) -> bool:
        """A property's same-line accessors and its initializer's units."""
        name = _property_name(node)
        for part in node.named_children:
            if part.type in _ACCESSORS:
                self._add_accessor(scope, part, name)
            else:
                self._scan_nested(scope, part)
        return True

    def _add_accessor(self, scope: ExtractionScope, acc: Node, prop: str) -> None:
        body = _accessor_body(acc)
        if body is None:
            return  # `private set`
        kind, suffix = _ACCESSORS[acc.type]
        self._add(scope, acc, _Unit(prop + suffix, kind, body, _params(acc)))

    def _add_block(self, scope: ExtractionScope, node: Node, params: list[Param]) -> None:
        """A secondary constructor (named after its class) or an `init` block."""
        if node.type == "anonymous_initializer":
            name = "init"
        else:
            name = scope.cls.name.rsplit(".", 1)[-1] if scope.cls else "constructor"
        self._add(scope, node, _Unit(name, "constructor", _statements_body(node), params))

    def _add_function(self, scope: ExtractionScope, node: Node) -> None:
        body = _child(node, "function_body")
        if body is not None:  # abstract and interface members have none
            self._add(scope, node, _Unit(_function_name(node), "function", body, _params(node)))

    def _test_call(self, fn: Node) -> Node | None:
        """The test-framework call a trailing lambda is passed to, if any."""
        call = _lambda_call(fn) if _is_trailing(fn) else None
        return call if call is not None and _callee(call) in self.test_calls else None

    def _add_closure(self, scope: ExtractionScope, fn: Node) -> bool:
        """A lambda bound to a name or passed to a test call is a unit."""
        name = _bound_name(fn)
        call = self._test_call(fn) if name is None else None
        if name is None and call is None:
            return False  # an inline callback: part of the enclosing unit
        self._units.add(fn.id)
        if fn.type == "anonymous_function":
            unit = _Unit(name, "function", _child(fn, "function_body"), _params(fn))
        else:
            unit = _Unit(name, "function", _statements_body(fn), _lambda_params(fn))
        if call is not None:
            desc = _description(call)
            label = f'{_callee(call)}("{desc}")' if desc else _callee(call)
            unit = unit._replace(name=label, kind="test")
        self._add(scope, call or fn, unit, end=fn)
        return True

    def _add(
        self, scope: ExtractionScope, node: Node, unit: _Unit, end: Node | None = None
    ) -> None:
        """Add ``unit``, spanning ``node`` (to ``end``), and the units in its body."""
        is_member = scope.cls is not None or _type_of(node.parent) == "class_body"
        kind = "method" if unit.kind == "function" and is_member else unit.kind
        fn = Function(
            name=unit.name,
            qualname=f"{scope.prefix}{unit.name}",
            kind=kind,
            start_line=_line(node),
            end_line=_end_line(end or node),
            params=unit.params,
            body=unit.body,
            class_name=scope.cls.name if scope.cls else None,
        )
        scope.add(fn)
        walk_units(unit.body, lambda n: self._add_unit(scope.nested(fn.qualname), n))
