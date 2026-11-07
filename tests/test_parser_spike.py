"""M1 spike: tree-sitter grammars parse real Python, Dart 3 / Flutter, Swift and Rust code.

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
