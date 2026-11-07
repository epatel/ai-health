"""M1 spike: tree-sitter grammars parse real Python and Dart 3 / Flutter code.

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
    [("python", "sample.py"), ("dart", "flutter_widget.dart")],
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
