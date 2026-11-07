"""C and C++ declarators, parameters and types for the C-family adapter.

Grammar notes: a function's name and parameters sit in the innermost
`function_declarator`, under pointer / reference / parenthesized
declarators. `int (*pick(int w))(int)` returns a function pointer: its
*outer* function_declarator holds the return type's parameters.
"""

from __future__ import annotations

from tree_sitter import Node

from aihealth.lang.base import text, type_leaves
from aihealth.model import Param

_WRAPPERS = frozenset(
    {
        "pointer_declarator",
        "reference_declarator",
        "parenthesized_declarator",
        "attributed_declarator",
        "block_pointer_declarator",
    }
)


def _inner(node: Node) -> Node | None:
    """The declarator a wrapper declarator holds."""
    inner = node.child_by_field_name("declarator")
    if inner is None and node.named_child_count:
        inner = node.named_children[-1]
    return inner


def function_declarator(decl: Node | None) -> Node | None:
    """The innermost function_declarator: the one naming the function."""
    found = None
    while decl is not None:
        if decl.type == "function_declarator":
            found = decl
            decl = decl.child_by_field_name("declarator")
        elif decl.type in _WRAPPERS:
            decl = _inner(decl)
        else:
            break
    return found


def function_name(fdecl: Node) -> Node | None:
    """The name node of a function_declarator: `f` also in `int (f)(int)`."""
    name = fdecl.child_by_field_name("declarator")
    while name is not None and name.type in _WRAPPERS:
        name = _inner(name)
    return name


def declared_name(decl: Node | None) -> Node | None:
    """The identifier node a variable/parameter declarator declares; None
    for a function pointer."""
    while decl is not None and decl.type in _WRAPPERS | {"init_declarator", "function_declarator"}:
        if decl.type == "function_declarator":
            return None
        decl = (
            _inner(decl)
            if decl.type != "init_declarator"
            else decl.child_by_field_name("declarator")
        )
    return decl


def _strip_templates(name: str) -> str:
    """`Box<T>::get` -> `Box::get`."""
    out, depth = [], 0
    for ch in name:
        if ch == "<":
            depth += 1
        elif ch == ">" and depth:
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def source_text(node: Node | None, source: bytes) -> str:
    """A node's text in the original file (the parsed text may be prepared:
    Objective-C++ `A::f` is parsed as `A__f`)."""
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace")


def dotted(node: Node, source: bytes) -> str:
    """`A::B::~C` -> `A.B.~C`, without template arguments or spaces;
    operator names keep their brackets (`A.operator<`)."""
    name = "".join(source_text(node, source).split())
    head, op, tail = name.partition("operator")
    return (_strip_templates(head) + op + tail).replace("::", ".").lstrip(".")


# --- parameters --------------------------------------------------------------------------

_PARAM_TYPES = frozenset(
    {"parameter_declaration", "optional_parameter_declaration", "variadic_parameter_declaration"}
)


def _type_before(node: Node, name: Node | None, source: bytes) -> str | None:
    """The parameter's text before its name (the type as written), without
    a default value."""
    end = name.start_byte if name is not None else node.end_byte
    raw = source[node.start_byte : end].decode("utf-8", "replace")
    return raw.split("=")[0].strip() or None


def _param(node: Node, source: bytes) -> Param | None:
    if node.type == "variadic_parameter":  # C `...`
        return Param(name="...", variadic=True)
    decl = node.child_by_field_name("declarator")
    if decl is None and text(node.child_by_field_name("type")) == "void":
        return None  # `f(void)`
    name = declared_name(decl)
    pointer_to_function = decl is not None and name is None
    return Param(
        name=text(name),
        type_text=None if pointer_to_function else _type_before(node, name, source),
        optional=node.type == "optional_parameter_declaration",
        variadic=node.type == "variadic_parameter_declaration",
    )


def _any_param(node: Node, source: bytes) -> Param | None:
    if node.type == "identifier":  # K&R `f(a, b) int a; ...`
        return Param(name=text(node))
    if node.type in _PARAM_TYPES or node.type == "variadic_parameter":
        return _param(node, source)
    return None  # comments


def params(fdecl: Node | None, source: bytes) -> list[Param]:
    plist = fdecl.child_by_field_name("parameters") if fdecl is not None else None
    if plist is None:
        return []
    found = (_any_param(p, source) for p in plist.named_children)
    return [p for p in found if p is not None]


# --- types ----------------------------------------------------------------------------------

QUALIFIERS = frozenset(
    {"const", "volatile", "restrict", "__restrict", "struct", "enum", "union", "class"}
    | {"typename", "unsigned", "signed", "static", "register", "constexpr", "mutable"}
    | {"in", "out", "inout", "nonnull", "nullable", "_Nonnull", "_Nullable", "__unused"}
    | {"__nonnull", "__nullable", "__strong", "__weak", "__kindof"}
)
_INTEGERS = frozenset({"long", "short", "int", "char", "double"})
STDINT = frozenset(
    f"{p}int{n}_t" for p in ("", "u") for n in ("8", "16", "32", "64", "max", "ptr")
) | frozenset({"size_t", "ssize_t", "ptrdiff_t", "off_t", "wchar_t"})


def _type_words(type_text: str) -> list[str]:
    """Words of a type without qualifiers, pointers, references and `std::`."""
    t = type_text.replace("*", " ").replace("&", " ").replace("std::", "")
    return [w for w in t.split() if w not in QUALIFIERS]


def _unknown(words: list[str]) -> bool:
    joined = " ".join(words)
    return "(" in joined or "..." in joined or joined == "auto"


def base_types(type_text: str) -> list[str] | None:
    """`const std::vector<Order> &` -> [Order], `unsigned long` -> [long],
    `unsigned` -> [int], `NSString *` -> [NSString]; `auto`, function
    types and packs are unknown."""
    words = _type_words(type_text)
    if not words:
        return ["int"] if "signed" in type_text else None  # `unsigned`
    if _unknown(words):
        return None
    if _INTEGERS.issuperset(words):  # `long long`, `unsigned char`
        return [words[-1]]
    return [leaf for leaf in type_leaves("".join(words), "<", ">") if leaf] or None
