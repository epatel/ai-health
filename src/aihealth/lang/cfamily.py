"""C, C++ and Objective-C adapter (tree-sitter `c`, `cpp` and `objc` grammars).

One adapter for the three languages: a `.h` header may be any of them, and
the registry routes by extension, so the adapter picks the grammar per file
(`grammar_for`) and the module reports it (`module.language`).

Grammar notes (see cards/parser-choice.md):
- all three share C's statements: an else-if is `else_clause > if_statement`
  (unwrapped by `else_if()`), `default:` is a `case_statement` without a
  `value`, `goto` is a `goto_statement` (+1 cognitive, as SonarSource says);
- C++ conditions are wrapped in `condition_clause`; `template_declaration`
  wraps what it templates;
- declarators: see `cfamily_decl`; Objective-C methods: see `cfamily_objc`.

Units: every `function_definition` (also inside `#if` blocks, namespaces,
`extern "C"` and class bodies), C++ out-of-line methods (`A::f` named
`A.f`), and Objective-C methods in `@implementation` (`Foo.greet:times:`).
Lambdas and blocks are closures. Parse errors are not reported: a third of
C files hold macros the grammar can't expand.
"""

from __future__ import annotations

import re
from typing import ClassVar

from tree_sitter import Node

from aihealth.lang import cfamily_decl as decl
from aihealth.lang import cfamily_objc as objc
from aihealth.lang.base import (
    ExtractionScope,
    LanguageAdapter,
    iter_nodes,
    text,
    walk_units,
)
from aihealth.metrics import Role
from aihealth.model import Class, Function, Module

# --- header routing ---------------------------------------------------------------------

# `^[^\S\n]*`, not `^\s*`: the latter runs across blank lines from every line
# start (an 80 KB file of blank lines took 78 s).
_OBJC_MARKERS = re.compile(
    rb"^[^\S\n]*(@interface|@protocol|@implementation|@end\b|#import\b)", re.MULTILINE
)
_CPP_MARKERS = re.compile(
    rb"^[^\S\n]*(namespace\b|template\s*<|class\s+\w+\s*(final\s*)?[:{]|struct\s+\w+\s*(final\s*)?:[^:]|"
    rb"(public|private|protected)\s*:)|\w::\w",
    re.MULTILINE,
)
_GRAMMAR_BY_EXT = {".c": "c", ".m": "objc", ".mm": "objcpp"} | dict.fromkeys(
    (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"), "cpp"
)


def header_grammar(source: bytes) -> str:
    """Language of a `.h` file: Objective-C if it has `@interface`-style
    directives or `#import`, C++ if it has `namespace`, templates, class
    bodies, struct inheritance, access specifiers or `::`, Objective-C++
    if it has both; otherwise C."""
    cpp = _CPP_MARKERS.search(source) is not None
    if _OBJC_MARKERS.search(source):
        return "objcpp" if cpp else "objc"
    return "cpp" if cpp else "c"


# conditional directives, with continuation lines
_CONDITIONALS = re.compile(
    rb"^[ \t]*#[ \t]*(if|ifdef|ifndef|elif|elifdef|elifndef|else|endif)\b[^\n]*(\\\n[^\n]*)*",
    re.MULTILINE,
)


def _spaces(match: re.Match[bytes]) -> bytes:
    return re.sub(rb"[^\n]", b" ", match.group(0))


# Apple's enum macros: `typedef NS_ENUM(NSInteger, Kind) { ... }` derails the
# grammars (one costs GTMSessionFetcher.m 5,000 lines); `enum Kind {...};`
# parses (without `typedef`, which would need a name after the body)
_APPLE_ENUM = re.compile(
    rb"(?:\btypedef\s+)?\b(?:NS|CF)_(?:ENUM|OPTIONS|CLOSED_ENUM|ERROR_ENUM)\(\s*\w[\w ]*,\s*(\w+)\s*\)"
)
# a line holding only an ALL_CAPS macro: `Q_OBJECT`, `ABSL_NAMESPACE_BEGIN`,
# `NS_ASSUME_NONNULL_BEGIN`, `PACK_STRUCT_BEGIN`
_BARE_MACRO = re.compile(rb"^[ \t]*[A-Z_][A-Z0-9_]{2,}[ \t]*(?=\r?\n|\Z)", re.MULTILINE)


# Objective-C++: `a::b` and a leading `::b` (not `foo::)` in a selector)
_SCOPE = re.compile(rb"(?<=[\w>])::(?=[\w~])|(?<![\w:])::(?=\w)")


def _as_enum(match: re.Match[bytes]) -> bytes:
    enum = b"enum " + match.group(1)
    return enum.ljust(len(match.group(0)))


def prepare(source: bytes, language: str = "c") -> bytes:
    """What the grammars parse, with the same byte offsets as ``source``:

    - `#if`/`#ifdef`/`#else`/`#endif` lines blanked. A conditional block
      parses only when each branch is a complete construct; a branch that
      opens a brace or splits a statement turns the block, often the whole
      file, into an ERROR. Without the directives both branches are parsed
      as one sequence (C error lines 7.0 % -> 2.0 %);
    - Apple's `typedef NS_ENUM(T, Name)` as `enum Name` (Objective-C error
      lines 7.1 % -> 1.4 %);
    - lines holding only an ALL_CAPS macro blanked: the grammars read it
      as the start of a declaration and lose the class or namespace around
      it (C++ files with errors 26 % -> 21 %, Objective-C error lines
      1.3 % -> 0.7 %). At worst this drops an enumerator;
    - Objective-C++ (parsed with the `objc` grammar, which is C-based):
      `a::b` as `a__b`, one identifier (`.mm` error lines 24 % -> 14 %).
      Names and types are read from the original source.
    """
    if b"#" in source:
        source = _CONDITIONALS.sub(_spaces, source)
    if b"NS_" in source or b"CF_" in source:
        source = _APPLE_ENUM.sub(_as_enum, source)
    if language == "objcpp" and b"::" in source:
        source = _SCOPE.sub(b"__", source)
    return _BARE_MACRO.sub(_spaces, source)


def grammar_for(path: str, source: bytes) -> str:
    dot = path.rfind(".")
    ext = path[dot:].lower() if dot >= 0 else ""
    return _GRAMMAR_BY_EXT.get(ext) or header_grammar(source)


# --- roles that depend on context ---------------------------------------------------------

_BOOL_OPS = {"&&": "and", "||": "or", "and": "and", "or": "or"}


def _binary_role(node: Node) -> Role | None:
    op = node.child_by_field_name("operator")
    return Role.BOOL_EXPR if op is not None and op.type in _BOOL_OPS else None


def _case_role(node: Node) -> Role | None:
    """A `case` arm; `default:` has no value."""
    return Role.CASE if node.child_by_field_name("value") is not None else None


# --- extraction ------------------------------------------------------------------------------

_CLASS_SPECIFIERS = {
    "class_specifier": "class",
    "struct_specifier": "struct",
    "union_specifier": "union",
}
# containers whose children are file-level declarations
_TRANSPARENT = frozenset(
    {"preproc_if", "preproc_ifdef", "preproc_else", "preproc_elif", "preproc_elifdef"}
    | {"linkage_specification", "declaration_list", "template_declaration"}
    | {"implementation_definition", "export_declaration"}
    # misparse recovery: a stray top-level `{ ... }` (macros before it)
    | {"compound_statement", "friend_declaration"}
)
_DECLARATIONS = frozenset({"declaration", "type_definition", "field_declaration"})


def _line(node: Node) -> int:
    return node.start_point[0] + 1


def _end_line(node: Node) -> int:
    return node.end_point[0] + 1


def _has_methods(body: Node) -> bool:
    return any(c.type in ("function_definition", "template_declaration") for c in body.children)


def _is_function(node: Node) -> bool:
    """A function definition with a declarator naming it. Loop macros
    (`NETIF_FOREACH(n) { ... }`) parse as definitions without one: their
    body is code of the enclosing function."""
    if node.type != "function_definition" or node.child_by_field_name("body") is None:
        return False
    return decl.function_declarator(node.child_by_field_name("declarator")) is not None


def _function_role(node: Node) -> Role | None:
    return Role.STOP if _is_function(node) else None


def _kind(name: str, class_name: str | None) -> str:
    if name.startswith("operator"):
        return "operator"
    if class_name is None:
        return "function"
    return "constructor" if name == class_name.rpartition(".")[2] else "method"


_CONTEXT_ROLES = {
    "binary_expression": _binary_role,
    "case_statement": _case_role,
    "function_definition": _function_role,
}


class CFamilyAdapter(LanguageAdapter):
    language = "c"
    extensions = (".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx", ".m", ".mm")
    reports_parse_errors = False
    roles: ClassVar[dict[str, Role]] = {
        "if_statement": Role.IF,
        "for_statement": Role.LOOP,
        "for_range_loop": Role.LOOP,
        "while_statement": Role.LOOP,
        "do_statement": Role.LOOP,
        "switch_statement": Role.SWITCH,
        "catch_clause": Role.CATCH,
        "conditional_expression": Role.TERNARY,
        "lambda_expression": Role.LAMBDA,
        "block_literal": Role.LAMBDA,
        "goto_statement": Role.JUMP_LABEL,
        "method_definition": Role.STOP,
        **dict.fromkeys(_CLASS_SPECIFIERS, Role.STOP),
    }
    block_types = frozenset({"compound_statement"})
    bool_transparent = frozenset(
        {"parenthesized_expression", "unary_expression", "condition_clause"}
    )
    primitive_types = (
        frozenset({"int", "char", "short", "long", "float", "double", "string", "string_view"})
        | {"vector", "map", "unordered_map", "set", "unordered_set", "array", "list"}
        | {"NSString", "NSNumber", "NSInteger", "NSUInteger", "CGFloat", "NSArray"}
        | {"NSDictionary", "NSMutableString", "NSMutableArray", "NSMutableDictionary", "id"}
        | decl.STDINT
    )
    neutral_types = frozenset({"bool", "_Bool", "BOOL", "void"})
    literal_types = frozenset(
        {"string_literal", "char_literal", "number_literal", "true", "false", "null"}
        | {"concatenated_string", "raw_string_literal", "nil"}
    )

    grammars = ("cpp", "objc")

    def __init__(self) -> None:
        super().__init__()
        # Objective-C++: no grammar of its own, the objc one on prepared source
        self._parsers["objcpp"] = self._parsers["objc"]

    def _grammar_for(self, source: bytes, path: str) -> str:
        return grammar_for(path, source)

    def _prepare(self, source: bytes, language: str) -> bytes:
        return prepare(source, language)

    # --- metric hooks ---------------------------------------------------------------------

    def role(self, node: Node) -> Role | None:
        contextual = _CONTEXT_ROLES.get(node.type)
        return contextual(node) if contextual is not None else self.roles.get(node.type)

    def else_if(self, node: Node) -> Node | None:
        if node.type != "else_clause":
            return None
        return next((c for c in node.named_children if c.type == "if_statement"), None)

    def bool_token(self, node: Node) -> str | None:
        return _BOOL_OPS.get(node.type)

    def is_recursive_call(self, node: Node, fn_name: str) -> bool:
        """`f(..)` / `this->f(..)`, or `[self f..]` for an Objective-C selector."""
        if node.type == "call_expression":
            target = node.child_by_field_name("function")
            return text(target) in (fn_name, f"this->{fn_name}")
        if node.type == "message_expression":
            receiver = node.child_by_field_name("receiver")
            return text(receiver) == "self" and objc.message_selector(node) == fn_name
        return False

    def unwrap_loop(self, stmt: Node) -> Node | None:
        if stmt.type == "labeled_statement" and stmt.named_child_count:
            stmt = stmt.named_children[-1]
        return super().unwrap_loop(stmt)

    # --- module/class smell hooks --------------------------------------------------------

    def base_types(self, type_text: str) -> list[str] | None:
        return decl.base_types(type_text)

    def member_refs(self, cls: Class) -> dict[str, set[str]]:
        """C++: only classes whose methods are all defined in the class body
        (members are used without `this->`, and out-of-line methods live in
        another file). Objective-C: ivars, `self.x`, `_x` and `[self m]`."""
        if cls.node is None:
            return {}
        if cls.node.type in ("class_implementation", "ERROR"):
            return objc.member_refs(cls)
        return _cpp_member_refs(cls)

    # --- extraction -------------------------------------------------------------------------

    def _extract(self, module: Module) -> None:
        self._scan(ExtractionScope(module), module.root)

    def _scan(self, scope: ExtractionScope, parent: Node) -> None:
        for child in parent.named_children:
            self._add_item(scope, child)

    def _add_item(self, scope: ExtractionScope, node: Node) -> None:
        handler = _ITEM_HANDLERS.get(node.type)
        if handler is not None:
            handler(self, scope, node)
        elif node.type in _TRANSPARENT:
            self._scan(scope, node)

    def _add_specifier(self, scope: ExtractionScope, node: Node) -> None:
        """`struct S { ... } s;`, `typedef struct { ... } T;`"""
        spec = node.child_by_field_name("type")
        if spec is not None and spec.type in _CLASS_SPECIFIERS:
            self._add_class(scope, spec)

    def _add_namespace(self, scope: ExtractionScope, node: Node) -> None:
        name = node.child_by_field_name("name")
        body = node.child_by_field_name("body")
        if body is None:
            return
        prefix = scope.prefix
        if name is not None:
            prefix = f"{prefix}{decl.dotted(name, scope.module.source)}."
        self._scan(ExtractionScope(scope.module, None, prefix), body)

    def _add_class(self, scope: ExtractionScope, node: Node) -> None:
        """A C++ class/struct/union with methods in its body is a Class; a
        plain data struct is not, but may nest classes. C structs hold no
        functions."""
        body = node.child_by_field_name("body")
        if body is None or scope.module.language != "cpp":
            return
        name = node.child_by_field_name("name")
        short = decl.dotted(name, scope.module.source) if name is not None else "(anonymous)"
        qualname = f"{scope.prefix}{short}"
        if not _has_methods(body):
            self._scan_members(ExtractionScope(scope.module, None, f"{qualname}."), body)
            return
        cls = Class(
            name=qualname,
            kind=_CLASS_SPECIFIERS[node.type],
            start_line=_line(node),
            end_line=_end_line(node),
            node=node,
        )
        scope.module.classes.append(cls)
        self._scan_members(scope.in_class(cls), body)

    def _scan_members(self, scope: ExtractionScope, body: Node) -> None:
        """Methods of a class body. Nested classes and inline `friend`
        functions are named after the class but are not its methods."""
        outside = ExtractionScope(scope.module, None, scope.prefix)
        for child in body.named_children:
            if child.type == "function_definition":
                self._add_function(scope, child)
            elif child.type == "friend_declaration":
                self._scan(outside, child)
            elif child.type in _TRANSPARENT:
                self._scan_members(scope, child)
            else:
                self._add_item(outside, child)

    def _add_implementation(self, scope: ExtractionScope, node: Node) -> None:
        cls = Class(
            name=f"{scope.prefix}{objc.implementation_name(node) or '(anonymous)'}",
            kind="extension" if node.child_by_field_name("category") else "class",
            start_line=_line(node),
            end_line=_end_line(node),
            node=node,
        )
        scope.module.classes.append(cls)
        self._scan(scope.in_class(cls), node)

    def _add_objc_method(self, scope: ExtractionScope, node: Node) -> None:
        body = objc.method_body(node)
        if body is None:
            return
        name = objc.selector(node)
        unit = Function(
            name=name,
            qualname=f"{scope.prefix}{name}",
            kind="method",
            start_line=_line(node),
            end_line=_end_line(node),
            params=objc.method_params(node, scope.module.source),
            body=body,
            class_name=scope.cls.name if scope.cls else None,
        )
        self._add_unit(scope, unit)

    def _add_function(self, scope: ExtractionScope, node: Node) -> None:
        self._add_specifier(scope, node)  # `struct S { ... } f() {}` (misparse)
        if not _is_function(node):
            # `class EXPORT_MACRO C { ... }` parses as a definition without
            # a declarator: its body is a class body
            self._scan_macro_class(scope, node)
            return
        fdecl = decl.function_declarator(node.child_by_field_name("declarator"))
        name_node = decl.function_name(fdecl)
        full = decl.dotted(name_node, scope.module.source) if name_node is not None else "?"
        owner, _, name = full.rpartition(".")
        class_name = scope.cls.name if scope.cls else None
        if owner and scope.cls is None:  # out-of-line `int A::f()`
            class_name = f"{scope.prefix}{owner}"
        unit = Function(
            name=name,
            qualname=f"{scope.prefix}{full}",
            kind=_kind(name, class_name),
            start_line=_line(node),
            end_line=_end_line(node),
            params=decl.params(fdecl, scope.module.source),
            body=node.child_by_field_name("body"),
            class_name=class_name,
        )
        self._add_unit(scope, unit)

    def _scan_macro_class(self, scope: ExtractionScope, node: Node) -> None:
        """`class EXPORT C { ... }` parses as a definition of `C` without a
        function declarator: its body is the class body (members named
        `C.f`, not judged as a class)."""
        body = node.child_by_field_name("body")
        if body is None or scope.cls is not None:
            return
        name = node.child_by_field_name("declarator")
        prefix = scope.prefix
        if name is not None and name.type == "identifier":
            prefix = f"{prefix}{decl.source_text(name, scope.module.source)}."
        self._scan(ExtractionScope(scope.module, None, prefix), body)

    def _add_error(self, scope: ExtractionScope, node: Node) -> None:
        """Units the grammar kept intact inside an ERROR node. When an
        `@implementation` broke (C++ in Objective-C++, a lambda in a
        method), its `@implementation Foo` token is still a child: what
        follows, up to `@end`, are Foo's methods."""
        inner = scope
        for i, child in enumerate(node.children):
            if child.type == "@implementation":
                inner = self._recovered_implementation(scope, node, i)
            elif child.type == "@end":
                inner = scope
            elif child.is_named:
                self._add_item(inner, child)

    def _recovered_implementation(
        self, scope: ExtractionScope, error: Node, index: int
    ) -> ExtractionScope:
        siblings = error.children[index + 1 :]
        name = next((c for c in siblings if c.type == "identifier"), None)
        end = next((c for c in siblings if c.type == "@end"), error)
        cls = Class(
            name=f"{scope.prefix}{text(name) or '(anonymous)'}",
            kind="class",
            start_line=_line(error.children[index]),
            end_line=_end_line(end),
            node=error,
        )
        scope.module.classes.append(cls)
        return scope.in_class(cls)

    def _add_unit(self, scope: ExtractionScope, unit: Function) -> None:
        scope.add(unit)
        inner = scope.nested(unit.qualname)
        # local classes and GNU nested functions are their own units
        walk_units(unit.body, lambda n: self._add_local(inner, n))

    def _add_local(self, scope: ExtractionScope, node: Node) -> bool:
        if _is_function(node):
            self._add_function(scope, node)
        elif node.type in _CLASS_SPECIFIERS:
            self._add_class(scope, node)
        else:
            return False
        return True


_ITEM_HANDLERS = {
    "function_definition": CFamilyAdapter._add_function,
    "method_definition": CFamilyAdapter._add_objc_method,
    "namespace_definition": CFamilyAdapter._add_namespace,
    **dict.fromkeys(_CLASS_SPECIFIERS, CFamilyAdapter._add_class),
    "class_implementation": CFamilyAdapter._add_implementation,
    "ERROR": CFamilyAdapter._add_error,
    **dict.fromkeys(_DECLARATIONS, CFamilyAdapter._add_specifier),
}


# --- C++ cohesion -------------------------------------------------------------------------


def _cpp_member_refs(cls: Class) -> dict[str, set[str]]:
    body = cls.node.child_by_field_name("body")
    if _declares_methods(body):
        return {}
    members = _cpp_fields(body) | {m.name for m in cls.methods if m.kind != "constructor"}
    return {fn.qualname: _cpp_refs(fn.body, members) for fn in cls.methods}


def _is_method_declaration(node: Node) -> bool:
    """`int f(int);` in a class body: a member function defined elsewhere."""
    if node.type not in ("field_declaration", "declaration"):
        return False
    declarator = node.child_by_field_name("declarator")
    return declarator is not None and declarator.type == "function_declarator"


def _declares_methods(body: Node) -> bool:
    return any(_is_method_declaration(c) for c in body.named_children)


def _cpp_fields(body: Node) -> set[str]:
    fields = set()
    for child in (c for c in body.named_children if c.type == "field_declaration"):
        names = map(decl.declared_name, child.children_by_field_name("declarator"))
        fields |= {text(n) for n in names if n is not None and n.type == "field_identifier"}
    return fields


def _this_field(node: Node) -> str | None:
    """`x` for `this->x`."""
    if node.type != "field_expression":
        return None
    arg = node.child_by_field_name("argument")
    return (
        text(node.child_by_field_name("field")) if arg is not None and arg.type == "this" else None
    )


def _cpp_refs(body: Node, members: set[str]) -> set[str]:
    """Bare member names and `this->x`; `other.x` is another object's."""
    refs = set()
    for n in iter_nodes(body):
        name = text(n) if n.type == "identifier" else _this_field(n)
        if name in members:
            refs.add(name)
    return refs
