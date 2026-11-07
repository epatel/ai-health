"""Python adapter (tree-sitter `python` grammar)."""

from __future__ import annotations

from typing import ClassVar

from tree_sitter import Node

from aihealth.lang.base import LanguageAdapter, text, type_leaves
from aihealth.metrics import Role
from aihealth.model import Class, Function, Module, Param

_PARAM_TYPED = {"typed_parameter", "typed_default_parameter"}
_PARAM_DEFAULT = {"default_parameter", "typed_default_parameter"}
_SPLATS = {"list_splat_pattern", "dictionary_splat_pattern"}


def _unwrap(node: Node) -> Node:
    """`@decorator def f` is a decorated_definition wrapping the def."""
    if node.type == "decorated_definition":
        inner = node.child_by_field_name("definition")
        if inner is not None:
            return inner
    return node


def _is_docstring(node: Node) -> bool:
    """A string that is the first statement of a module, class or function
    body. Grammar versions differ: newer ones put a bare `string` in the
    block, older ones wrap it in `expression_statement`."""
    if node.type == "expression_statement":
        if node.named_child_count != 1 or node.named_children[0].type not in (
            "string",
            "concatenated_string",
        ):
            return False
    elif node.type not in ("string", "concatenated_string"):
        return False
    parent = node.parent
    if parent is None or parent.type not in ("module", "block"):
        return False
    if parent.type == "block" and (
        parent.parent is None
        or parent.parent.type not in ("function_definition", "class_definition")
    ):
        return False
    first = next((c for c in parent.named_children if c.type != "comment"), None)
    return first is not None and first.id == node.id


def _params(params_node: Node | None, is_method: bool) -> list[Param]:
    if params_node is None:
        return []
    out: list[Param] = []
    for child in params_node.named_children:
        if child.type in ("keyword_separator", "positional_separator", "comment"):
            continue
        name_node = child.child_by_field_name("name")
        type_node = child.child_by_field_name("type")
        variadic = child.type in _SPLATS
        if child.type == "identifier":
            name = text(child)
        elif child.type in _SPLATS:
            name = text(child).lstrip("*")
        elif child.type == "typed_parameter":
            # name is the first named child (identifier or a splat pattern)
            first = child.named_children[0]
            variadic = first.type in _SPLATS
            name = text(first).lstrip("*")
        else:
            name = text(name_node)
        out.append(
            Param(
                name=name,
                type_text=text(type_node) if child.type in _PARAM_TYPED and type_node else None,
                optional=child.type in _PARAM_DEFAULT,
                variadic=variadic,
            )
        )
    if is_method and out and out[0].name in ("self", "cls") and not out[0].typed:
        out = out[1:]
    return out


class PythonAdapter(LanguageAdapter):
    language = "python"
    extensions = (".py", ".pyi")
    roles: ClassVar[dict[str, Role]] = {
        "if_statement": Role.IF,
        "elif_clause": Role.IF,  # only reached as an `alternative` -> else-if
        "for_statement": Role.LOOP,
        "while_statement": Role.LOOP,
        "match_statement": Role.SWITCH,
        "case_clause": Role.CASE,
        "except_clause": Role.CATCH,
        "except_group_clause": Role.CATCH,
        "conditional_expression": Role.TERNARY,
        "lambda": Role.LAMBDA,
        "function_definition": Role.STOP,
        "class_definition": Role.STOP,
        "decorated_definition": Role.STOP,
        "for_in_clause": Role.COMPREHENSION,
        "if_clause": Role.COMPREHENSION,
        "boolean_operator": Role.BOOL_EXPR,
    }
    bool_transparent = frozenset({"parenthesized_expression", "not_operator"})

    def role(self, node: Node) -> Role | None:
        r = self.roles.get(node.type)
        if r is Role.CASE:
            pattern = node.named_children[0] if node.named_child_count else None
            if pattern is not None and text(pattern).strip() == "_":
                return None  # `case _:` is the default arm
        return r

    def bool_token(self, node: Node) -> str | None:
        if node.type in ("and", "or") and node.parent and node.parent.type == "boolean_operator":
            return node.type
        return None

    def is_recursive_call(self, node: Node, fn_name: str) -> bool:
        if node.type != "call":
            return False
        target = node.child_by_field_name("function")
        if target is None:
            return False
        if target.type == "identifier":
            return text(target) == fn_name
        if target.type == "attribute":
            obj = target.child_by_field_name("object")
            attr = target.child_by_field_name("attribute")
            return text(obj) in ("self", "cls") and text(attr) == fn_name
        return False

    primitive_types = frozenset(
        {
            "int",
            "float",
            "complex",
            "str",
            "bytes",
            "bytearray",
            "dict",
            "list",
            "tuple",
            "set",
            "frozenset",
            "Dict",
            "List",
            "Tuple",
            "Set",
            "FrozenSet",
            "Any",
            "object",
        }
    )
    literal_types = frozenset(
        {"string", "concatenated_string", "integer", "float", "true", "false", "none"}
    )

    def base_types(self, type_text: str) -> list[str] | None:
        t = type_text.strip().strip("'\"")
        if "Callable" in t:
            return None  # callbacks are not primitive obsession
        leaves = [x.strip("'\"").removeprefix("typing.") for x in type_leaves(t, "[", "]")]
        return leaves or None

    def member_refs(self, cls: Class) -> dict[str, set[str]]:
        """`self.x` / `cls.x` attribute names used in each method."""
        refs: dict[str, set[str]] = {}
        for fn in cls.methods:
            names: set[str] = set()
            stack = list(fn.body.children) if fn.body is not None else []
            while stack:
                n = stack.pop()
                if n.type in ("function_definition", "class_definition"):
                    continue
                if n.type == "attribute":
                    obj = n.child_by_field_name("object")
                    if (
                        obj is not None
                        and obj.type == "identifier"
                        and text(obj) in ("self", "cls")
                    ):
                        names.add(text(n.child_by_field_name("attribute")))
                stack.extend(n.children)
            refs[fn.qualname] = names
        return refs

    def _skip_for_loc(self, node: Node) -> bool:
        return node.type == "comment" or _is_docstring(node)

    def _extract(self, module: Module) -> None:
        self._walk_block(module, module.root, prefix="", cls=None)

    def _walk_block(self, module: Module, block: Node, prefix: str, cls: Class | None) -> None:
        for raw in block.named_children:
            node = _unwrap(raw)
            if node.type == "function_definition":
                self._add_function(module, node, raw, prefix, cls)
            elif node.type == "class_definition":
                self._add_class(module, node, raw, prefix)
            elif node.type in ("if_statement", "try_statement", "with_statement"):
                # defs under `if TYPE_CHECKING:` / try-import fallbacks
                for sub in node.named_children:
                    if sub.type == "block":
                        self._walk_block(module, sub, prefix, cls)
                    else:
                        body = sub.child_by_field_name("body") or next(
                            (c for c in sub.named_children if c.type == "block"), None
                        )
                        if body is not None:
                            self._walk_block(module, body, prefix, cls)

    def _add_function(
        self, module: Module, node: Node, outer: Node, prefix: str, cls: Class | None
    ) -> None:
        name = text(node.child_by_field_name("name"))
        qualname = f"{prefix}{name}"
        body = node.child_by_field_name("body")
        fn = Function(
            name=name,
            qualname=qualname,
            kind="method" if cls is not None else "function",
            start_line=outer.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            params=_params(node.child_by_field_name("parameters"), cls is not None),
            body=body,
            class_name=cls.name if cls else None,
        )
        if cls is not None:
            if name == "__init__":
                fn.kind = "constructor"
            cls.methods.append(fn)
        else:
            module.functions.append(fn)
        if body is not None:
            # nested defs become their own functions; nested classes too
            self._walk_block(module, body, prefix=f"{qualname}.", cls=None)

    def _add_class(self, module: Module, node: Node, outer: Node, prefix: str) -> None:
        name = text(node.child_by_field_name("name"))
        cls = Class(
            name=f"{prefix}{name}",
            kind="class",
            start_line=outer.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            node=node,
        )
        module.classes.append(cls)
        body = node.child_by_field_name("body")
        if body is not None:
            self._walk_block(module, body, prefix=f"{cls.name}.", cls=cls)
