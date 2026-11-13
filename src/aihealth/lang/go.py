"""Go adapter (tree-sitter `go` grammar).

Grammar notes (see cards/parser-choice.md):
- a `block` holds a `statement_list` (absent when empty), so `statements()`
  looks through it;
- an else-if is the `alternative` `if_statement` itself (no wrapper);
- cases are `expression_case` / `type_case` / `communication_case`, the
  default arm `default_case`, all direct children of their switch/select;
- methods are top-level `method_declaration`s: those of one receiver type
  are gathered into one `Class` per file.

Units: function and method declarations, functions bound to a name
(`var f = func(..)`, `f := func(..)`) and subtest closures
(`t.Run("name", func(t *testing.T) {..})`). Every other `func` literal is
a closure of its enclosing function.

Error checks that only propagate (`if err != nil { return .., err }`) add
no complexity by default, like Rust's `?` (see cards/code-health-model.md).
"""

from __future__ import annotations

from typing import ClassVar

from tree_sitter import Node

from aihealth.lang.base import (
    ExtractionScope,
    LanguageAdapter,
    split_top,
    text,
    type_leaves,
    walk_units,
)
from aihealth.metrics import Role
from aihealth.model import Class, Function, Module, Param

# callees whose `func` literal argument is a unit of its own (subtests, benchmarks)
DEFAULT_TEST_CALLS = frozenset({"Run"})

_SWITCHES = frozenset({"expression_switch_statement", "type_switch_statement", "select_statement"})
_CASES = frozenset({"expression_case", "type_case", "communication_case"})


def _line(node: Node) -> int:
    return node.start_point[0] + 1


def _end_line(node: Node) -> int:
    return node.end_point[0] + 1


def _name(node: Node) -> str:
    return text(node.child_by_field_name("name"))


def _child(node: Node | None, *types: str) -> Node | None:
    if node is None:
        return None
    return next((c for c in node.named_children if c.type in types), None)


# --- parameters ---------------------------------------------------------------------


def _params(plist: Node | None) -> list[Param]:
    """One Param per name (`a, b int` is two); unnamed parameters
    (`func(int, string)`) count one each."""
    out: list[Param] = []
    for decl in plist.named_children if plist is not None else ():
        if decl.type not in ("parameter_declaration", "variadic_parameter_declaration"):
            continue
        type_node = decl.child_by_field_name("type")
        type_text = text(type_node) if type_node is not None else None
        variadic = decl.type == "variadic_parameter_declaration"
        names = [text(n) for n in decl.children_by_field_name("name")] or ["_"]
        out += [Param(name=n, type_text=type_text, variadic=variadic) for n in names]
    return out


# --- error checks -----------------------------------------------------------------------


def _type_of(node: Node | None) -> str:
    return node.type if node is not None else ""


def _is_err_nil(cond: Node | None) -> bool:
    """`err != nil` (or `xErr != nil`)."""
    if _type_of(cond) != "binary_expression":
        return False
    left, op, right = (cond.child_by_field_name(f) for f in ("left", "operator", "right"))
    name = text(left) if _type_of(left) == "identifier" else ""
    is_err = name == "err" or name.endswith("Err")
    return _type_of(op) == "!=" and _type_of(right) == "nil" and is_err


def _only_returns(block: Node | None) -> bool:
    stmts = _block_statements(block)
    return len(stmts) == 1 and stmts[0].type == "return_statement"


def is_error_propagation(node: Node) -> bool:
    """`if err != nil { return .. }` (optionally `if err := f(); ...`), no
    else: Go's spelled-out `?`."""
    if node.type != "if_statement" or node.child_by_field_name("alternative") is not None:
        return False
    return _is_err_nil(node.child_by_field_name("condition")) and _only_returns(
        node.child_by_field_name("consequence")
    )


def _block_statements(block: Node | None) -> list[Node]:
    if block is None:
        return []
    inner = _child(block, "statement_list")
    return [c for c in (inner or block).named_children if c.type != "comment"]


# --- roles that depend on context -------------------------------------------------------


def _binary_role(node: Node) -> Role | None:
    op = node.child_by_field_name("operator")
    return Role.BOOL_EXPR if op is not None and op.type in ("&&", "||") else None


def _jump_role(node: Node) -> Role | None:
    """`break L` / `continue L`; unlabeled jumps add nothing."""
    return Role.JUMP_LABEL if _child(node, "label_name") is not None else None


_CONTEXT_ROLES = {
    "binary_expression": _binary_role,
    "break_statement": _jump_role,
    "continue_statement": _jump_role,
}


# --- types ----------------------------------------------------------------------------

_UNKNOWN_PREFIXES = ("func", "interface", "struct", "any")


def _element(t: str) -> str:
    """`T` for `*T`, `...T`, `chan T`, `<-chan T`, `chan<- T`."""
    t = t.strip().lstrip("*").strip().removeprefix("...").removeprefix("<-").strip()
    if t.startswith("chan"):
        t = t.removeprefix("chan").strip().removeprefix("<-").strip()
    return t


def _map_leaves(t: str) -> list[str]:
    key, _, value = t[4:].partition("]")
    return [*(_type_leaves(key) or []), *(_type_leaves(value) or [])]


def _named_leaves(t: str) -> list[str]:
    """`pkg.User` -> [User], `Set[Key]` -> [Key] (a generic by its arguments)."""
    leaves = []
    for leaf in type_leaves(t, "[", "]"):
        leaves += (_type_leaves(leaf) if leaf != t else [leaf]) or []
    return [x.rsplit(".", 1)[-1] for x in leaves]


def _type_leaves(t: str) -> list[str] | None:
    """Innermost type names: `[]Order` -> [Order], `map[string]int` ->
    [string, int], `*pkg.User` -> [User], `Set[Key]` -> [Key]; function,
    interface and anonymous struct types are unknown."""
    t = _element(t)
    if not t or t.startswith(_UNKNOWN_PREFIXES):
        return None
    if t.startswith("map["):
        return _map_leaves(t) or None
    if t.startswith("["):  # slice or array
        return _type_leaves(t.partition("]")[2])
    return _named_leaves(t) or None


def _receiver(node: Node) -> tuple[str, str]:
    """(receiver name or "", base type name) of a method_declaration."""
    decl = _child(node.child_by_field_name("receiver"), "parameter_declaration")
    if decl is None:
        return "", "<recv>"
    name = text(decl.child_by_field_name("name"))
    t = text(decl.child_by_field_name("type")).lstrip("*").strip()
    return name, split_top(t, "[", "(", ")")[0].split("[", 1)[0].strip() or "<recv>"


def _var_name(owner: Node, values: Node) -> str | None:
    names = owner.children_by_field_name("name")
    return text(names[0]) if len(names) == 1 else None


def _assigned_name(owner: Node, values: Node) -> str | None:
    left = owner.child_by_field_name("left")
    if owner.child_by_field_name("right") != values or left is None:
        return None
    targets = left.named_children
    return text(targets[0]) if len(targets) == 1 and targets[0].type == "identifier" else None


_NAMERS = {
    "var_spec": _var_name,
    "short_var_declaration": _assigned_name,
    "assignment_statement": _assigned_name,
}


def _func_value_name(fn: Node) -> str | None:
    """`f` for `var f = func(..)` / `f := func(..)` / `f = func(..)` with one
    name and one value."""
    values = fn.parent
    if _type_of(values) != "expression_list" or values.named_child_count != 1:
        return None
    owner = values.parent
    namer = _NAMERS.get(owner.type) if owner is not None else None
    return namer(owner, values) if namer is not None else None


def _call_of_argument(fn: Node) -> Node | None:
    """The call that ``fn`` is a direct argument of."""
    args = fn.parent
    if args is None or args.type != "argument_list":
        return None
    call = args.parent
    return call if call is not None and call.type == "call_expression" else None


def _callee_name(call: Node) -> str:
    """`Run` for `t.Run(..)`, `f` for `f(..)`."""
    callee = call.child_by_field_name("function")
    field = callee.child_by_field_name("field") if callee is not None else None
    return text(field) if field is not None else text(callee)


def _string_arg(call: Node) -> str | None:
    args = call.child_by_field_name("arguments")
    first = args.named_children[0] if args is not None and args.named_children else None
    if first is None or first.type not in ("interpreted_string_literal", "raw_string_literal"):
        return None
    return text(first)[1:-1] or None


class GoAdapter(LanguageAdapter):
    language = "go"
    extensions = (".go",)
    roles: ClassVar[dict[str, Role]] = {
        "if_statement": Role.IF,
        "for_statement": Role.LOOP,
        **dict.fromkeys(_SWITCHES, Role.SWITCH),
        **dict.fromkeys(_CASES, Role.CASE),
        "func_literal": Role.LAMBDA,
        "goto_statement": Role.JUMP_LABEL,
        "function_declaration": Role.STOP,
        "method_declaration": Role.STOP,
    }
    # `for_clause` sits between a three-clause loop and its condition
    bool_transparent = frozenset({"parenthesized_expression", "unary_expression", "for_clause"})
    primitive_types = frozenset(
        {
            *(f"{p}{n}" for p in ("int", "uint") for n in ("", "8", "16", "32", "64")),
            "uintptr",
            "float32",
            "float64",
            "complex64",
            "complex128",
            "byte",
            "rune",
            "string",
        }
    )
    literal_types = frozenset(
        {
            "int_literal",
            "float_literal",
            "imaginary_literal",
            "rune_literal",
            "interpreted_string_literal",
            "raw_string_literal",
            "true",
            "false",
        }
    )

    def __init__(
        self, count_error_checks: bool = False, test_calls: frozenset[str] = DEFAULT_TEST_CALLS
    ) -> None:
        super().__init__()
        self.count_error_checks = count_error_checks
        self.test_calls = test_calls
        self._units: set[int] = set()  # ids of func literals scored as units

    # --- metric hooks --------------------------------------------------------------------

    def role(self, node: Node) -> Role | None:
        if node.type == "func_literal" and node.id in self._units:
            return Role.STOP
        if node.type == "if_statement":
            if not self.count_error_checks and is_error_propagation(node):
                return None
            return Role.IF
        contextual = _CONTEXT_ROLES.get(node.type)
        return contextual(node) if contextual is not None else self.roles.get(node.type)

    def bool_token(self, node: Node) -> str | None:
        return {"&&": "and", "||": "or"}.get(node.type)

    def statements(self, body: Node) -> list[Node]:
        return _block_statements(body if body.type == "block" else None)

    def unwrap_loop(self, stmt: Node) -> Node | None:
        if stmt.type == "labeled_statement" and stmt.named_child_count == 2:
            stmt = stmt.named_children[1]
        return super().unwrap_loop(stmt)

    def is_recursive_call(self, node: Node, fn_name: str) -> bool:
        """`f(..)`, or `r.f(..)` on the method's own receiver `r`."""
        if node.type != "call_expression":
            return False
        callee = text(node.child_by_field_name("function"))
        if callee == fn_name:
            return True
        # only a call ending in `.f` is worth finding the receiver for
        recv = _enclosing_receiver(node) if callee.endswith(f".{fn_name}") else None
        return recv is not None and callee == f"{recv}.{fn_name}"

    # --- module/class smell hooks --------------------------------------------------------

    def base_types(self, type_text: str) -> list[str] | None:
        return _type_leaves(type_text)

    def member_refs(self, cls: Class) -> dict[str, set[str]]:
        """`r.x` fields and `r.m()` calls per method, `r` being its receiver."""
        refs = {}
        for fn in cls.methods:
            decl = fn.body.parent
            recv = _receiver(decl)[0] if decl is not None else ""
            refs[fn.qualname] = _receiver_members(fn.body, recv) if recv not in ("", "_") else set()
        return refs

    # --- extraction ---------------------------------------------------------------------

    def _extract(self, module: Module) -> None:
        self._units = set()
        scope = ExtractionScope(module)
        classes: dict[str, Class] = {}
        types = _type_lines(module.root)
        for node in module.root.named_children:
            if node.type == "function_declaration":
                self._add_function(scope, node, (_name(node), "function"))
            elif node.type == "method_declaration":
                cls = self._class_for(module, classes, types, node)
                self._add_function(scope.in_class(cls), node, (_name(node), "method"))
            else:
                walk_units(node, lambda n: self._add_unit(scope, n))

    def _class_for(
        self, module: Module, classes: dict[str, Class], types: dict[str, Node], method: Node
    ) -> Class:
        """The receiver type's Class, spanning its type declaration (if in
        this file) and all its methods."""
        name = _receiver(method)[1]
        cls = classes.get(name)
        if cls is None:
            decl = types.get(name, method)
            cls = Class(name, "type", _line(decl), _end_line(decl), node=decl)
            classes[name] = cls
            module.classes.append(cls)
        cls.start_line = min(cls.start_line, _line(method))
        cls.end_line = max(cls.end_line, _end_line(method))
        return cls

    def _add_unit(self, scope: ExtractionScope, node: Node) -> bool:
        """A named or subtest `func` literal becomes a unit (True: its
        insides belong to it)."""
        if node.type != "func_literal":
            return False
        name, kind, start = self._literal_label(node)
        if name is None:
            return False  # an inline closure
        self._units.add(node.id)
        self._add_function(scope, node, (name, kind), start)
        return True

    def _literal_label(self, fn: Node) -> tuple[str | None, str, Node]:
        """(name, kind, start node) of a func literal, name None if inline."""
        name = _func_value_name(fn)
        if name is not None:
            return name, "function", fn
        call = _call_of_argument(fn)
        callee = _callee_name(call) if call is not None else ""
        if call is None or callee not in self.test_calls:
            return None, "", fn
        return _string_arg(call) or callee, "test", call

    def _add_function(
        self,
        scope: ExtractionScope,
        node: Node,
        label: tuple[str, str],
        start: Node | None = None,
    ) -> None:
        """Add ``node`` as a unit; ``label`` is (name, kind)."""
        name, kind = label
        body = node.child_by_field_name("body")
        if body is None:
            return  # an external (assembly) function
        fn = Function(
            name=name,
            qualname=f"{scope.prefix}{name}",
            kind="method" if scope.cls is not None else kind,
            start_line=_line(start or node),
            end_line=_end_line(node),
            params=_params(node.child_by_field_name("parameters")),
            body=body,
            class_name=scope.cls.name if scope.cls else None,
        )
        scope.add(fn)
        nested = scope.nested(fn.qualname)
        walk_units(body, lambda n: self._add_unit(nested, n))


def _type_lines(root: Node) -> dict[str, Node]:
    """Top-level type specs by name."""
    out = {}
    for decl in root.named_children:
        if decl.type != "type_declaration":
            continue
        for spec in decl.named_children:
            if spec.type in ("type_spec", "type_alias"):
                out[text(spec.child_by_field_name("name"))] = spec
    return out


def _enclosing_receiver(node: Node) -> str | None:
    """Receiver name of the method declaration around ``node``."""
    n = node.parent
    while n is not None and n.type not in ("method_declaration", "function_declaration"):
        n = n.parent
    if n is None or n.type != "method_declaration":
        return None
    return _receiver(n)[0] or None


def _receiver_members(body: Node, recv: str) -> set[str]:
    names: set[str] = set()
    stack = list(body.children)
    while stack:
        n = stack.pop()
        if n.type == "selector_expression":
            operand = n.child_by_field_name("operand")
            if _type_of(operand) == "identifier" and text(operand) == recv:
                names.add(text(n.child_by_field_name("field")))
        stack.extend(n.children)
    return names
