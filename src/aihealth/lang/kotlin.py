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
expressions and enum constants are units named after the enclosing unit;
closures passed to test-framework calls (Kotest, Spek) are units of kind
`test`.

In a `@Composable` function, lambdas passed to a capitalised call (`Column
{ }`, `Card(onClick = { })`) are layout and add no cognitive nesting, like
SwiftUI view builders, and those calls make up the widget tree. Effects
(`LaunchedEffect`, ...) and lowercase calls (`let`, `map`, `launch`) keep
the closure penalty. `?.`, `?:` and `!!` add no complexity.
"""

from __future__ import annotations

from typing import ClassVar, NamedTuple

from tree_sitter import Node

from aihealth.lang.base import (
    ExtractionScope,
    LanguageAdapter,
    call_nesting,
    end_line,
    first_child,
    member_refs_by_method,
    names_in,
    scan_units,
    short_label,
    start_line,
    text,
    type_leaves,
    type_of,
    walk_units,
)
from aihealth.metrics import Role
from aihealth.model import Class, Function, Module, Param

_LOOPS = ("for_statement", "while_statement", "do_while_statement")
_CLASS_NODES = ("class_declaration", "object_declaration", "companion_object")
_DECLARATIONS = frozenset({"function_declaration", *_CLASS_NODES})
_ACCESSORS = {"getter": ("getter", ""), "setter": ("setter", "=")}
_SKIPPED_SIBLINGS = frozenset({*_ACCESSORS, "line_comment", "multiline_comment"})

# capitalised Compose calls whose lambda runs code (side effects), not layout
_EFFECT_CALLS = frozenset({"LaunchedEffect", "DisposableEffect", "SideEffect"})

# Kotest / Spek specs: a trailing closure passed to one of these is a test unit
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


# --- names ------------------------------------------------------------------------


def _simple_type(type_text: str) -> str:
    """`List<T>?` -> `List`, `kotlin.String` -> `String`."""
    return type_text.split("<", 1)[0].rstrip("?").strip().rsplit(".", 1)[-1]


def _with_receiver(decl: Node, name: str) -> str:
    """`String.f` for an extension function or property, else ``name``."""
    receiver = first_child(decl, "receiver_type")
    return f"{_simple_type(text(receiver))}.{name}" if receiver is not None else name


def _property_name(decl: Node) -> str:
    var = first_child(decl, "variable_declaration")
    return _with_receiver(decl, text(first_child(var, "simple_identifier")))


def _accessor_property(acc: Node) -> str:
    """The property a getter or setter belongs to: its parent, or the
    property declared before it when it stands on its own line."""
    prop = acc.parent if type_of(acc.parent) == "property_declaration" else acc.prev_named_sibling
    while prop is not None and prop.type in _SKIPPED_SIBLINGS:
        prop = prop.prev_named_sibling
    return _property_name(prop) if type_of(prop) == "property_declaration" else "<property>"


# --- parameters ---------------------------------------------------------------------


def _typed(node: Node) -> Param:
    """`name: Type` (a parameter or a typed lambda parameter); the type
    keeps its modifiers (`@Composable () -> Unit`, `suspend () -> Unit`)."""
    name = first_child(node, "simple_identifier")
    rest = [c for c in node.named_children if c != name]
    type_text = text(node)[rest[0].start_byte - node.start_byte :] if rest else None
    return Param(name=text(name), type_text=type_text)


def _params(fn: Node) -> list[Param]:
    """Declared parameters. One with a default value counts half for Excess
    Arguments (`named`): callers usually skip it or pass it by name."""
    out: list[Param] = []
    vararg = False
    for c in getattr(first_child(fn, "function_value_parameters"), "named_children", ()):
        if c.type == "parameter":
            out.append(_typed(c))
            out[-1].variadic, vararg = vararg, False
        elif c.type == "parameter_modifiers":
            vararg = "vararg" in text(c).split()
        elif out and c.type not in ("line_comment", "multiline_comment"):
            out[-1].optional = out[-1].named = True
    return out


def _lambda_params(lam: Node) -> list[Param]:
    """`{ a: Int, (k, v) -> .. }`: typed names and destructured groups."""
    plist = first_child(lam, "lambda_parameters")
    out = []
    for p in getattr(plist, "named_children", ()):
        if p.type == "variable_declaration":
            out.append(_typed(p))
        elif p.type == "multi_variable_declaration":
            out.append(Param(name=text(p)))
    return out


# --- calls --------------------------------------------------------------------------


def _callee(call: Node) -> str:
    """`Column` for `Column { }`, `let` for `x?.let { }`."""
    target = call.named_children[0] if call.named_child_count else None
    if type_of(target) == "navigation_expression":
        target = first_child(target.named_children[-1], "simple_identifier")
    return text(target) if type_of(target) == "simple_identifier" else ""


def _lambda_call(lam: Node) -> Node | None:
    """The call a lambda is passed to: trailing (`f { }`) or as an argument
    (`f(content = { })`)."""
    node = lam.parent
    while type_of(node) in ("annotated_lambda", "value_argument", "value_arguments"):
        node = node.parent
    if type_of(node) != "call_suffix":
        return None
    return node.parent if type_of(node.parent) == "call_expression" else None


def _is_composable(fn: Node | None) -> bool:
    """A function declaration annotated `@Composable`."""
    if type_of(fn) != "function_declaration":
        return False
    mods = first_child(fn, "modifiers")
    return any(
        text(a).rsplit(".", 1)[-1].lstrip("@") == "Composable"
        for a in getattr(mods, "named_children", ())
    )


def _in_composable(node: Node) -> bool:
    fn = node.parent
    while fn is not None and fn.type != "function_declaration":
        fn = fn.parent
    return _is_composable(fn)


def _is_layout_call(call: Node) -> bool:
    """`Column(..)`, `Text(..)`: a capitalised callee that isn't an effect."""
    name = _callee(call)
    return bool(name) and name[0].isupper() and name not in _EFFECT_CALLS


def _description(call: Node) -> str:
    args = first_child(first_child(call, "call_suffix"), "value_arguments")
    lit = first_child(first_child(args, "value_argument"), "string_literal")
    return short_label(text(lit).strip('"')) if lit is not None else ""


# --- roles that depend on context -------------------------------------------------------


def _jump_role(node: Node) -> Role | None:
    """`break@l` / `continue@l`; other jumps (`return@l` too) add nothing."""
    first = node.children[0].type if node.child_count else ""
    return Role.JUMP_LABEL if first in ("break@", "continue@") else None


def _entry_role(node: Node) -> Role | None:
    """A `when` branch; `else ->` adds nothing."""
    return None if any(c.type == "else" for c in node.children) else Role.CASE


_CONTEXT_ROLES = {"jump_expression": _jump_role, "when_entry": _entry_role}


# --- types ----------------------------------------------------------------------------


def _strip_type(t: str) -> str:
    t = t.strip()
    while t.startswith("@"):  # type annotations: `@Ann Int`
        t = t.partition(" ")[2].strip()
    return t.removeprefix("out ").removeprefix("in ").strip()


def _type_leaves(t: str) -> list[str] | None:
    """Innermost type names: `List<Order>` -> [Order], `Map<String, Int?>`
    -> [String, Int], `kotlin.Long` -> [Long]; function types are unknown."""
    if "->" in t:
        return None
    leaves = [_strip_type(leaf).rstrip("?") for leaf in type_leaves(_strip_type(t), "<", ">")]
    return [leaf.rsplit(".", 1)[-1] for leaf in leaves if leaf not in ("", "*")] or None


# --- cohesion ---------------------------------------------------------------------------


def _instance_fields(cls: Class) -> set[str]:
    """Body properties and `val` / `var` primary constructor parameters."""
    body = first_child(cls.node, "class_body", "enum_class_body")
    ctor = first_child(cls.node, "primary_constructor")
    props = {
        _property_name(d)
        for d in getattr(body, "named_children", ())
        if d.type == "property_declaration"
    }
    return props | {
        text(first_child(p, "simple_identifier"))
        for p in getattr(ctor, "named_children", ())
        if first_child(p, "binding_pattern_kind") is not None
    }


def _is_foreign(ident: Node) -> bool:
    """`other.x` (not on `this`), an argument name (`f(x = 1)`) or a
    declaration's own name."""
    parent = ident.parent
    if type_of(parent) == "navigation_suffix":
        return type_of(parent.prev_named_sibling) != "this_expression"
    if type_of(parent) == "value_argument":
        return parent.named_child_count > 1 and parent.named_children[0] == ident
    return type_of(parent) in ("variable_declaration", "parameter", "function_declaration")


def _own_name(node: Node | None) -> str | None:
    """`x` for `x` or `this.x` (an expression or an assignment target)."""
    kids = node.named_children if node is not None else []
    if type_of(node) == "simple_identifier":
        return text(node)
    if len(kids) == 1 and kids[0].type == "simple_identifier":  # assignment target `x`
        return text(kids[0])
    if len(kids) == 2 and kids[0].type == "this_expression":  # `this.x`
        return text(first_child(kids[1], "simple_identifier"))
    return None


def _single_expression(body: Node) -> Node | None:
    """The one expression of `= expr`, `{ expr }` or `{ return expr }`."""
    kids = body.named_children
    if len(kids) == 1 and kids[0].type == "statements":
        kids = kids[0].named_children
    only = kids[0] if len(kids) == 1 else None
    if type_of(only) == "jump_expression" and only.children[0].type == "return":
        return only.named_children[0] if only.named_child_count == 1 else None
    return only


def _accessed_property(expr: Node | None) -> str | None:
    """The property `x` that `x` / `this.x` returns or `x = value` stores."""
    if type_of(expr) != "assignment":
        return _own_name(expr)
    target, value = expr.named_children[0], expr.named_children[-1]
    return _own_name(target) if value.type == "simple_identifier" else None


def _is_member_ref(node: Node, members: set[str]) -> bool:
    is_name = node.type == "simple_identifier" and text(node) in members
    return is_name and not _is_foreign(node)


def _member_names_in(body: Node, members: set[str]) -> set[str]:
    return names_in(body, _DECLARATIONS, lambda n: _is_member_ref(n, members))


# --- extraction helpers ------------------------------------------------------------------


def _statements_body(node: Node) -> Node:
    """`init { .. }` / `constructor(..) { .. }` / `{ .. }`: the statements, or
    the node itself when the block is empty."""
    return first_child(node, "statements") or node


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
            is_layout = call is not None and _is_layout_call(call) and _in_composable(node)
            return None if is_layout else Role.LAMBDA
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
        return first_child(stmt, "control_structure_body")

    def is_recursive_call(self, node: Node, fn_name: str) -> bool:
        """`f(..)` or `this.f(..)`."""
        if node.type != "call_expression" or _callee(node) != fn_name.rsplit(".", 1)[-1]:
            return False
        target = node.named_children[0]
        return target.type == "simple_identifier" or target.named_children[0].type == (
            "this_expression"
        )

    def widget_depth(self, body: Node) -> int:
        """Deepest nesting of capitalised calls in a `@Composable` function
        (`Column { Row { Text() } }`)."""
        return call_nesting(body, _is_layout_call, self.role) if _is_composable(body.parent) else 0

    def hint(self, smell: str) -> str | None:
        if smell == "deep_widget_tree":
            return "Extract subtrees into smaller `@Composable` functions."
        return None

    # --- module/class smell hooks --------------------------------------------------------

    def base_types(self, type_text: str) -> list[str] | None:
        return _type_leaves(type_text)

    def member_refs(self, cls: Class) -> dict[str, set[str]]:
        """Properties and methods used bare or through `this` per method.
        Interfaces have no state, and accessors (property getters and
        setters, functions that only return or store one property) touch
        one property by design: neither is judged."""
        if cls.kind == "interface":
            return {}
        fields = _instance_fields(cls)

        def is_accessor(fn: Function) -> bool:
            in_fields = _accessed_property(_single_expression(fn.body)) in fields
            return fn.kind in ("getter", "setter") or in_fields

        return member_refs_by_method(cls, fields, _member_names_in, is_accessor)

    # --- extraction ---------------------------------------------------------------------

    def _extract(self, module: Module) -> None:
        self._units = set()
        scan_units(ExtractionScope(module), module.root, self._add_unit)

    def _scan_nested(self, scope: ExtractionScope, node: Node) -> None:
        """Units at or below ``node`` (an initializer, a class header): not
        members of the class."""
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
        elif node.type in ("secondary_constructor", "anonymous_initializer"):
            self._add_block(scope, node)
        elif node.type == "property_declaration":
            self._add_property(scope, node)
        elif node.type in ("object_literal", "enum_entry"):
            self._scan_object(scope, node)
        elif node.type in ("lambda_literal", "anonymous_function"):
            return self._add_closure(scope, node)
        else:
            return False
        return True

    def _add_class(self, scope: ExtractionScope, node: Node) -> None:
        name = text(first_child(node, "type_identifier")) or "Companion"
        is_interface = any(c.type == "interface" for c in node.children)
        cls = scope.add_class(name, "interface" if is_interface else "class", node)
        inner = scope.in_class(cls)
        for part in node.named_children:
            if part.type in ("class_body", "enum_class_body"):
                scan_units(inner, part, self._add_unit)
            else:  # header: `FunSpec({ test(..) { } })`, constructor defaults
                self._scan_nested(inner, part)

    def _scan_object(self, scope: ExtractionScope, node: Node) -> None:
        """Methods of an object expression or an enum constant are named
        after the enclosing unit and the supertype or constant
        (`start.Runnable.run`, `Status.OPEN.next`), or the property it is
        bound to (`ticker.Runnable.run`)."""
        body = first_child(node, "class_body")
        if body is None:  # enum constant arguments
            walk_units(node, lambda n: self._add_unit(_outside_class(scope), n))
            return
        if node.type == "enum_entry":
            name = text(first_child(node, "simple_identifier"))
        else:
            spec = text(first_child(node, "delegation_specifier")).split("(", 1)[0]
            name = _simple_type(spec) or "object"
            if type_of(node.parent) == "property_declaration":  # `val ticker = object : ..`
                name = f"{_property_name(node.parent)}.{name}"
        inner = ExtractionScope(scope.module, None, f"{scope.prefix}{name}.")
        scan_units(inner, body, self._add_unit)

    def _add_property(self, scope: ExtractionScope, node: Node) -> None:
        """Same-line accessors and a lambda bound to the property belong to
        its scope (a class's: methods); other initializer units don't."""
        name = _property_name(node)
        for part in node.named_children:
            if part.type in _ACCESSORS:
                self._add_accessor(scope, part, name)
            elif not (
                part.type in ("lambda_literal", "anonymous_function")
                and self._add_unit(scope, part)
            ):
                self._scan_nested(scope, part)

    def _add_accessor(self, scope: ExtractionScope, acc: Node, prop: str) -> None:
        body = first_child(acc, "function_body")
        if body is not None:  # not `private set`
            kind, suffix = _ACCESSORS[acc.type]
            self._add(scope, acc, _Unit(prop + suffix, kind, body, _params(acc)))

    def _add_block(self, scope: ExtractionScope, node: Node) -> None:
        """An `init` block, or a secondary constructor named after its class."""
        if node.type == "anonymous_initializer":
            name, params = "init", []
        else:
            name, params = scope.prefix.rstrip(".").rsplit(".", 1)[-1], _params(node)
        self._add(scope, node, _Unit(name, "constructor", _statements_body(node), params))

    def _add_function(self, scope: ExtractionScope, node: Node) -> None:
        body = first_child(node, "function_body")
        name = _with_receiver(node, text(first_child(node, "simple_identifier")))
        if body is not None:  # abstract and interface members have none
            self._add(scope, node, _Unit(name, "function", body, _params(node)))

    def _test_call(self, fn: Node) -> Node | None:
        """The test-framework call a trailing lambda is passed to, if any."""
        call = _lambda_call(fn) if type_of(fn.parent) == "annotated_lambda" else None
        return call if call is not None and _callee(call) in self.test_calls else None

    def _add_closure(self, scope: ExtractionScope, fn: Node) -> bool:
        """A lambda bound to a name or passed to a test call is a unit."""
        bound = type_of(fn.parent) == "property_declaration"
        call = None if bound else self._test_call(fn)
        if not bound and call is None:
            return False  # an inline callback: part of the enclosing unit
        self._units.add(fn.id)
        name = _property_name(fn.parent) if bound else ""
        if fn.type == "anonymous_function":
            unit = _Unit(name, "function", first_child(fn, "function_body"), _params(fn))
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
        is_member = scope.cls is not None or type_of(node.parent) == "class_body"
        kind = "method" if unit.kind == "function" and is_member else unit.kind
        fn = Function(
            name=unit.name,
            qualname=f"{scope.prefix}{unit.name}",
            kind=kind,
            start_line=start_line(node),
            end_line=end_line(end or node),
            params=unit.params,
            body=unit.body,
            class_name=scope.cls.name if scope.cls else None,
        )
        scope.add(fn)
        walk_units(unit.body, lambda n: self._add_unit(scope.nested(fn.qualname), n))
