"""M1 spike: tree-sitter grammars parse real Python, Dart 3 / Flutter, Swift, Rust,
TypeScript, CMake, C, C++, Objective-C and shell code.

These tests pin the node type names the M2 adapters will rely on, so a
grammar upgrade that renames them fails loudly here.
"""

from itertools import pairwise
from pathlib import Path

import pytest
from tree_sitter_language_pack import get_parser

FIXTURES = Path(__file__).parent / "fixtures"


def _parse(lang: str, name: str):
    source = (FIXTURES / name).read_bytes()
    return get_parser(lang).parse(source).root_node


def _types(node) -> set[str]:
    seen = set()
    stack = [node]
    while stack:
        n = stack.pop()
        seen.add(n.type)
        stack.extend(n.children)
    return seen


def _errors(node) -> list[tuple[int, str]]:
    out = []
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type == "ERROR" or n.is_missing:
            out.append((n.start_point[0] + 1, n.type))
        stack.extend(n.children)
    return sorted(out)


@pytest.mark.parametrize(
    ("lang", "fixture"),
    [
        ("python", "sample.py"),
        ("dart", "flutter_widget.dart"),
        ("swift", "sample.swift"),
        ("rust", "sample.rs"),
        ("typescript", "sample.ts"),
        ("tsx", "sample.tsx"),
        ("javascript", "sample.js"),
        ("cmake", "CMakeLists.txt"),
        ("c", "sample.c"),
        ("cpp", "sample.cpp"),
        ("objc", "sample.m"),
        ("bash", "sample.sh"),
    ],
)
def test_parses_without_errors(lang, fixture):
    root = _parse(lang, fixture)
    assert _errors(root) == []


def test_python_node_types():
    types = _types(_parse("python", "sample.py"))
    expected = {
        "function_definition",
        "class_definition",
        "if_statement",
        "elif_clause",
        "for_statement",
        "boolean_operator",
        "match_statement",
        "parameters",
    }
    assert expected <= types


def test_dart_node_types(capsys):
    types = _types(_parse("dart", "flutter_widget.dart"))
    # Printed so the spike run documents the grammar's vocabulary (pytest -s).
    print(sorted(types))
    expected = {
        "class_definition",
        "if_statement",
        "for_statement",
        "switch_expression",
        "if_element",
        "conditional_expression",
        "function_expression",
        "optional_formal_parameters",
        "logical_and_operator",
        "logical_or_operator",
    }
    assert expected <= types


def test_dart_signature_and_body_are_siblings():
    """Dart quirk: a function's body is the next sibling of its signature,
    not a child. Adapters must pair them."""
    root = _parse("dart", "flutter_widget.dart")
    pairs = []
    stack = [root]
    while stack:
        n = stack.pop()
        kids = n.children
        for a, b in pairwise(kids):
            if a.type in ("function_signature", "method_signature") and b.type == "function_body":
                pairs.append(a)
        stack.extend(kids)
    names = set()
    for sig in pairs:
        fn = sig if sig.type == "function_signature" else sig.named_children[0]
        names.add(fn.child_by_field_name("name").text.decode())
    assert {"area", "parseEntry", "bump", "createState", "_increment", "build"} <= names


def test_swift_node_types():
    types = _types(_parse("swift", "sample.swift"))
    expected = {
        "class_declaration",
        "protocol_declaration",
        "function_declaration",
        "init_declaration",
        "deinit_declaration",
        "subscript_declaration",
        "property_declaration",
        "computed_property",
        "computed_getter",
        "computed_setter",
        "function_body",
        "statements",
        "parameter",
        "if_statement",
        "guard_statement",
        "for_statement",
        "while_statement",
        "repeat_while_statement",
        "switch_statement",
        "switch_entry",
        "default_keyword",
        "do_statement",
        "catch_block",
        "ternary_expression",
        "lambda_literal",
        "conjunction_expression",
        "disjunction_expression",
        "control_transfer_statement",
        "statement_label",
        "macro_invocation",
    }
    assert expected <= types


def test_swift_else_is_a_bare_token_and_kinds_are_a_field():
    """Swift quirks: no `alternative` field (the else branch follows an
    `else` token), and struct/class/enum/actor/extension share one node type."""
    root = _parse("swift", "sample.swift")
    kinds, else_follows = set(), set()
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type == "class_declaration":
            kinds.add(n.child_by_field_name("declaration_kind").type)
        for a, b in pairwise(n.children):
            if a.type == "else" and n.type == "if_statement":
                else_follows.add(b.type)
        stack.extend(n.children)
    assert kinds == {"struct", "class", "enum", "actor", "extension"}
    assert else_follows == {"if_statement", "{"}


RUST_NODE_TYPES = """
function_item function_signature_item impl_item trait_item mod_item
attribute_item parameters self_parameter parameter block expression_statement
if_expression else_clause let_chain let_declaration for_expression
while_expression loop_expression match_expression match_arm closure_expression
binary_expression break_expression label line_comment
"""


def test_rust_node_types():
    types = _types(_parse("rust", "sample.rs"))
    assert set(RUST_NODE_TYPES.split()) <= types


def test_rust_else_if_is_wrapped_in_else_clause():
    """Rust quirk: the else branch is an `else_clause` that wraps the inner
    `if_expression` (or a block); adapters unwrap it via `else_if()`."""
    root = _parse("rust", "sample.rs")
    inner = set()
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type == "if_expression":
            alt = n.child_by_field_name("alternative")
            if alt is not None:
                assert alt.type == "else_clause"
                inner |= {c.type for c in alt.named_children}
        stack.extend(n.children)
    assert inner == {"if_expression", "block"}


TS_NODE_TYPES = """
function_declaration generator_function_declaration class_declaration
abstract_class_declaration method_definition public_field_definition
arrow_function lexical_declaration variable_declarator formal_parameters
required_parameter optional_parameter statement_block if_statement else_clause
for_statement for_in_statement while_statement do_statement labeled_statement
break_statement switch_statement switch_case switch_default try_statement
catch_clause finally_clause ternary_expression binary_expression call_expression
member_expression object pair export_statement comment
"""


def test_typescript_node_types():
    types = _types(_parse("typescript", "sample.ts"))
    assert set(TS_NODE_TYPES.split()) <= types


def test_tsx_jsx_node_types():
    types = _types(_parse("tsx", "sample.tsx"))
    assert {"jsx_element", "jsx_self_closing_element", "jsx_expression"} <= types


def test_javascript_differs_from_typescript_in_params_and_fields():
    """JavaScript: bare parameter patterns and `field_definition` class fields
    (TypeScript wraps parameters in `required_parameter` and names fields
    `public_field_definition`)."""
    types = _types(_parse("javascript", "sample.js"))
    assert {"assignment_pattern", "rest_pattern", "field_definition", "jsx_element"} <= types
    assert not {"required_parameter", "public_field_definition"} & types


def test_cmake_if_is_flat():
    """CMake quirk: elseif/else commands and their bodies are siblings inside
    `if_condition`, not nested; AND/OR are plain unquoted arguments."""
    root = _parse("cmake", "CMakeLists.txt")
    first_if = next(c for c in root.named_children if c.type == "if_condition")
    kinds = [c.type for c in first_if.named_children]
    assert kinds == [
        "if_command",
        "body",
        "elseif_command",
        "body",
        "else_command",
        "body",
        "endif_command",
    ]
    types = _types(root)
    assert {"function_def", "macro_def", "foreach_loop", "while_loop", "block_def"} <= types


C_NODE_TYPES = """
function_definition function_declarator pointer_declarator parenthesized_declarator
parameter_list parameter_declaration compound_statement if_statement else_clause
for_statement while_statement do_statement switch_statement case_statement
goto_statement labeled_statement conditional_expression binary_expression
preproc_ifdef preproc_else preproc_function_def struct_specifier comment
"""
CPP_NODE_TYPES = """
namespace_definition declaration_list class_specifier field_declaration_list
access_specifier field_declaration qualified_identifier destructor_name operator_name
field_identifier template_declaration for_range_loop try_statement catch_clause
lambda_expression condition_clause reference_declarator
"""
OBJC_NODE_TYPES = """
class_interface class_implementation implementation_definition method_definition
method_declaration method_parameter method_type instance_variables message_expression
block_literal try_statement catch_clause finally_clause property_declaration
"""


def test_c_family_node_types():
    assert set(C_NODE_TYPES.split()) <= _types(_parse("c", "sample.c"))
    assert set(CPP_NODE_TYPES.split()) <= _types(_parse("cpp", "sample.cpp"))
    assert set(OBJC_NODE_TYPES.split()) <= _types(_parse("objc", "sample.m"))


def _find(node, kind):
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type == kind:
            yield n
        stack.extend(reversed(n.children))


def test_c_default_case_has_no_value_and_names_sit_in_the_inner_declarator():
    """C quirks: `default:` is a `case_statement` without a `value` field;
    a function returning a function pointer has two function_declarators,
    and only the inner one holds the name and parameters."""
    root = _parse("c", "sample.c")
    cases = [c.child_by_field_name("value") is not None for c in _find(root, "case_statement")]
    assert cases == [True, True, False]
    pick = next(f for f in _find(root, "function_definition") if b"pick" in f.text.split(b"{")[0])
    outer = pick.child_by_field_name("declarator")
    assert outer.type == "function_declarator"
    inner = next(_find(outer.child_by_field_name("declarator"), "function_declarator"))
    assert inner.child_by_field_name("declarator").text == b"pick"


def test_objc_methods_have_no_fields():
    """Objective-C quirk: a method's selector pieces are its direct
    `identifier` children and its body a plain `compound_statement`; the
    `-`/`+` marker is an anonymous token."""
    root = _parse("objc", "sample.m")
    greet = next(m for m in _find(root, "method_definition") if b"greet:" in m.text)
    assert greet.children[0].type == "-"
    assert [c.text for c in greet.named_children if c.type == "identifier"] == [b"greet", b"times"]
    assert greet.named_children[-1].type == "compound_statement"


BASH_NODE_TYPES = """
function_definition compound_statement if_statement elif_clause else_clause
for_statement c_style_for_statement while_statement do_group case_statement case_item
list test_command binary_expression ternary_expression subshell redirected_statement
heredoc_redirect heredoc_body command command_name declaration_command comment
"""


def test_bash_node_types():
    assert set(BASH_NODE_TYPES.split()) <= _types(_parse("bash", "sample.sh"))


def test_bash_until_is_a_while_and_elif_has_no_field():
    """Bash quirks: `until` is a `while_statement`; `elif_clause` and
    `else_clause` are children of `if_statement` without a field name."""
    root = _parse("bash", "sample.sh")
    ifs = [
        n for n in _find(root, "if_statement") if any(c.type == "elif_clause" for c in n.children)
    ]
    assert ifs
    for i, child in enumerate(ifs[0].children):
        if child.type in ("elif_clause", "else_clause"):
            assert ifs[0].field_name_for_child(i) is None
    loops = [n.children[0].type for n in _find(root, "while_statement")]
    assert loops == ["while", "until"]
