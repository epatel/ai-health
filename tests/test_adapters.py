"""Language adapters produce the uniform Module/Class/Function model."""

from pathlib import Path

import pytest

from aihealth.lang import adapter_for, parse_file, parse_source

FIXTURES = Path(__file__).parent / "fixtures"


def _fns(name: str) -> dict:
    module = parse_file(FIXTURES / name)
    return {f.qualname: f for f in module.all_functions()}


# --- registry -------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "lang"),
    [("a.py", "python"), ("b.pyi", "python"), ("w.dart", "dart"), ("X.DART", "dart")],
)
def test_adapter_by_extension(path, lang):
    assert adapter_for(path).language == lang


def test_unsupported_extension():
    assert adapter_for("main.go") is None
    assert parse_source(b"package main", "main.go") is None


def test_partial_parse_counts_errors():
    module = parse_source(b"def ok():\n    return 1\n\ndef broken(:\n", "x.py")
    assert module.error_count > 0
    assert "ok" in {f.qualname for f in module.all_functions()}


# --- python ---------------------------------------------------------------


def test_python_functions_and_qualnames():
    fns = _fns("declarations.py")
    assert set(fns) == {
        "typing_only",
        "outer",
        "outer.inner",
        "Service.build",
        "Service.ready",
        "Service.Config.load",
        "Service.run",
    }
    assert fns["Service.build"].kind == "method"
    assert fns["Service.Config.load"].class_name == "Service.Config"
    assert fns["outer.inner"].kind == "function"


def test_python_params():
    fns = _fns("declarations.py")
    outer = {p.name: p for p in fns["outer"].params}
    assert list(outer) == ["a", "b", "args", "c", "kwargs"]
    assert not outer["a"].typed and outer["b"].optional
    assert outer["args"].variadic and outer["kwargs"].variadic
    assert outer["c"].type_text == "int" and outer["c"].optional

    build = fns["Service.build"].params
    assert [(p.name, p.type_text, p.variadic) for p in build] == [
        ("name", "str", False),
        ("parts", "str", True),
    ]
    # self is dropped; separators are not params
    run = fns["Service.run"].params
    assert [p.name for p in run] == ["retries", "timeout"]
    assert fns["Service.ready"].params == []


def test_python_loc_excludes_docstrings_comments_blank():
    fns = _fns("declarations.py")
    # own lines only: `def outer(...)` and `return inner(...)`; inner's two
    # lines belong to outer.inner
    assert fns["outer"].loc == 2
    assert fns["outer.inner"].loc == 2
    # decorator line is part of the function span
    assert fns["Service.build"].loc == 3
    module = parse_file(FIXTURES / "declarations.py")
    assert module.loc < len((FIXTURES / "declarations.py").read_text().splitlines())


def test_python_constructor_kind():
    fns = _fns("sample.py")
    assert fns["Repository.__init__"].kind == "constructor"
    assert fns["classify"].loc == 10


# --- dart -----------------------------------------------------------------


def test_dart_declaration_kinds():
    fns = _fns("declarations.dart")
    kinds = {q: f.kind for q, f in fns.items()}
    assert kinds == {
        "Repo": "constructor",
        "Repo.make": "constructor",
        "Repo.size": "getter",
        "Repo.size=": "setter",
        "Repo.operator ==": "operator",
        "Repo.load": "method",
        "Repo.load.helper": "function",
        "Repo.each": "method",
        "Logs.log": "method",
        "StrX.twice": "method",
        "Color.warm": "getter",
        "topGetter": "getter",
        "main": "function",
    }
    # abstract `T? find(String id);` and the body-less initializer-list
    # constructor have nothing to score and are skipped


def test_dart_containers():
    module = parse_file(FIXTURES / "declarations.dart")
    assert [(c.name, c.kind) for c in module.classes] == [
        ("Repo", "class"),
        ("Logs", "mixin"),
        ("StrX", "extension"),
        ("Color", "enum"),
    ]


def test_dart_params():
    fns = _fns("declarations.dart")
    load = fns["Repo.load"].params
    assert [(p.name, p.type_text, p.named, p.optional) for p in load] == [
        ("limit", "int", False, True),
        ("q", "String?", False, True),
    ]
    each = fns["Repo.each"].params
    assert [(p.name, p.type_text) for p in each] == [
        ("cb", "void Function(T item)"),
        ("m", "Map<String, int>"),
    ]
    assert [(p.name, p.type_text) for p in fns["Repo"].params] == [("name", "<field>")]


def test_dart_named_and_required_params():
    src = b"class W { W({super.key, required this.title, int count = 0, final x}); }\n"
    src += b"void f({required String a, int? b, c}) {}\n"
    module = parse_source(src, "w.dart")
    f = next(fn for fn in module.all_functions() if fn.qualname == "f")
    assert [(p.name, p.type_text, p.named, p.optional) for p in f.params] == [
        ("a", "String", True, False),
        ("b", "int?", True, True),
        ("c", None, True, True),
    ]


def test_dart_local_function_in_constructor_initializer():
    src = b"""
class Row extends Field {
  Row({super.key})
      : super(builder: (s) {
          void onChanged(String v) { s.did(v); }
          return Text(onChanged);
        });
}
"""
    module = parse_source(src, "row.dart")
    assert [f.qualname for f in module.all_functions()] == ["Row.onChanged"]


def test_dart_flutter_build_span():
    fns = _fns("flutter_widget.dart")
    build = fns["_CounterPageState.build"]
    assert (build.start_line, build.end_line) == (63, 84)
    assert build.body.type == "function_body"
    assert [p.name for p in fns["Counter.bump"].params] == ["by", "clamp", "max"]
    assert all(p.named and p.optional for p in fns["Counter.bump"].params)
