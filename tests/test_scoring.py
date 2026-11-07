"""Smells, scoring, config, duplication and scan (M4)."""

import json
import textwrap
from pathlib import Path

import pytest

from aihealth.cli import main
from aihealth.config import Config, ConfigError, load_config
from aihealth.duplication import DuplicationIndex
from aihealth.engine import is_excluded, scan, score_source
from aihealth.lang import adapter_for, parse_source
from aihealth.scoring import band, file_score, project_score
from aihealth.smells import Finding, _severity, lcom4


def smells(src: str, path: str, config: Config | None = None) -> dict[str, list[Finding]]:
    fs = score_source(textwrap.dedent(src).encode(), path, config)
    out: dict[str, list[Finding]] = {}
    for f in fs.findings:
        out.setdefault(f.smell, []).append(f)
    return out


# --- severity / score -----------------------------------------------------------


def test_severity_scale():
    assert _severity(71, 70) == 1.0  # first triggering value
    assert _severity(141, 70) == 2.0
    assert _severity(10_000, 70) == 3.0  # capped
    assert _severity(4, 3) == 1.0 and _severity(5, 3) == pytest.approx(1.333, abs=1e-3)
    assert _severity(0.9, 0.6, integer=False) == 1.5


def _finding(weight=1.0, severity=1.0, level="function", share=1.0):
    return Finding("large_method", level, "f", 1, "x", 0, 0, severity, "", "", weight, share)


def test_file_score_curve():
    assert file_score([]) == 10.0
    assert file_score([_finding()]) == pytest.approx(9.14, abs=0.01)
    assert file_score([_finding(severity=3)] * 10) >= 1.0
    # tiny functions in big files count half
    small = _finding(share=0.01)
    file_score([small])
    assert small.penalty == pytest.approx(0.52, abs=0.01)


def test_bands_and_project_weighting():
    assert band(9.0) == "healthy" and band(8.99) == "warning" and band(3.99) == "alert"

    class F:
        def __init__(self, score, loc):
            self.score, self.loc = score, loc

    # CodeScene's example: 2.0@5000, 10@100, 10@10 -> 2.18, not 7.33
    assert project_score([F(2.0, 5000), F(10.0, 100), F(10.0, 10)]) == 2.17
    assert project_score([]) is None


# --- function smells ---------------------------------------------------------------


def test_clean_file_scores_ten():
    fs = score_source(b"def add(a: int, b: int) -> int:\n    return a + b\n", "ok.py")
    assert fs.score == 10.0 and fs.findings == [] and fs.band == "healthy"


def test_deep_nesting_complex_conditional_bumpy_road():
    found = smells(
        """
        def f(xs, a, b, c):
            for x in xs:
                if x:
                    if a and b or c:
                        while x:
                            if b:
                                pass
                if not x:
                    for y in xs:
                        pass
        """,
        "f.py",
    )
    assert found["deep_nesting"][0].value == 5
    assert found["deep_nesting"][0].line == 7  # deepest `if b:` (dedent keeps a leading blank)
    assert found["complex_conditional"][0].value == 2
    assert found["bumpy_road"][0].value == 2


def test_brain_method():
    body = "\n".join(
        f"        if x == {i}:\n            for y in x:\n                if y:\n                    if y > {i}:\n                        print(y)"
        for i in range(16)
    )
    found = smells(f"def brain(x):\n    while x:\n{body}\n", "b.py")
    assert {"brain_method", "large_method", "complex_method", "deep_nesting"} <= set(found)


def test_excess_arguments_dart_named_and_field_params():
    found = smells("void f(int a, int b, int c, int d, int e) {}", "a.dart")
    assert found["excess_arguments"][0].value == 5
    # named params weigh 0.5, `this.x` initializers are free
    found = smells(
        "class W { W({this.a, this.b, this.c, this.d, this.e, int? f, int? g}); void m() {} }",
        "w.dart",
    )
    assert "excess_arguments" not in found


def test_flutter_build_threshold_and_widget_depth():
    lines = "\n".join(f"    final v{i} = {i};" for i in range(100))
    deep = "A(child: " * 10 + "B()" + ")" * 10
    src = f"class S {{\n  Widget build(BuildContext c) {{\n{lines}\n    return {deep};\n  }}\n}}\n"
    found = smells(src, "s.dart")
    assert "large_method" not in found  # 102 lines < 120 build limit
    assert found["deep_widget_tree"][0].value == 11
    other = src.replace("build(", "render(")
    assert "large_method" in smells(other, "s.dart")


def test_asserts_are_not_complex_conditionals():
    py = "def f(a, b, c):\n    assert a and b or c\n    return a\n"
    assert "complex_conditional" not in smells(py, "a.py")
    dart = "void f(a, b, c) { assert(a && b || c); }"
    assert "complex_conditional" not in smells(dart, "a.dart")
    assert "complex_conditional" in smells(dart.replace("assert(", "g("), "a.dart")


def test_bool_params_are_neutral_for_primitive_obsession():
    flags = "\n".join(f"def f{i}(a: bool, b: bool, c: Order) -> None: ..." for i in range(5))
    assert "primitive_obsession" not in smells(flags, "p.py")


def test_weight_zero_disables_smell():
    cfg = Config()
    cfg.data["weights"]["excess_arguments"] = 0
    assert "excess_arguments" not in smells("def f(a, b, c, d, e, f): pass", "x.py", cfg)


# --- class / file smells ---------------------------------------------------------------


COHESION = """
class Grab:
    def __init__(self):
        self.a = self.b = self.c = None
    def a1(self): return self.a
    def a2(self): return self.a + self.a1()
    def b1(self): return self.b
    def b2(self): self.b = 1
    def c1(self): return self.c
    def helper(self): return 1
"""


def test_lcom4():
    module = parse_source(COHESION.encode(), "c.py")
    # {a1,a2} {b1,b2} {c1}; helper touches nothing and is ignored
    assert lcom4(module.classes[0], adapter_for("c.py")) == 3
    found = smells(COHESION, "c.py")
    assert found["low_cohesion"][0].symbol == "Grab"


def test_lcom4_dart_shared_fields_connect():
    src = """
class C {
  int a = 0; int b = 0;
  void m1() { a++; }
  void m2() { a--; b++; }
  void m3() { b = 0; }
  int get total => a + b;
}
"""
    module = parse_source(src.encode(), "c.dart")
    assert lcom4(module.classes[0], adapter_for("c.dart")) == 1


def test_primitive_obsession():
    fns = "\n".join(f"def f{i}(a: str, b: int, c: dict) -> None: ..." for i in range(4))
    assert "primitive_obsession" in smells(fns, "p.py")
    typed = fns.replace("c: dict", "c: Order").replace("b: int", "b: Money")
    assert "primitive_obsession" not in smells(typed, "p.py")
    untyped = "\n".join(f"def f{i}(a, b, c): ..." for i in range(5))
    assert "primitive_obsession" not in smells(untyped, "p.py")  # unknown != primitive


def test_large_file():
    src = "\n".join(f"x{i} = {i}" for i in range(1001))
    assert smells(src, "big.py")["large_file"][0].value == 1001


# --- duplication -----------------------------------------------------------------


CLONE_BODY = """
    total = 0
    for item in items:
        if item.price > 100:
            total += item.price * 0.9
        else:
            total += item.price
        if item.vip:
            total -= 5
    log("total", total)
    return round(total, 2)
"""


def test_duplication_intra_and_cross_file():
    a = parse_source(
        f"def a(items):{CLONE_BODY}\ndef b(items):{CLONE_BODY.replace('0.9', '0.8')}".encode(),
        "a.py",
    )
    b = parse_source(f"def c(items):{CLONE_BODY}".encode(), "b.py")
    idx = DuplicationIndex()
    idx.add(a, adapter_for("a.py"))
    idx.add(b, adapter_for("b.py"))
    pairs = {(c.a.qualname, c.b.qualname) for c in idx.clones()}
    assert pairs == {("a", "b"), ("a", "c"), ("b", "c")}


def test_duplication_below_threshold_ignored():
    small = "def a(x):\n    return x + 1\n\ndef b(x):\n    return x + 1\n"
    assert "code_duplication" not in smells(small, "s.py")


def test_duplication_finding_in_file_score():
    src = f"def a(items):{CLONE_BODY}\ndef b(items):{CLONE_BODY}"
    found = smells(src, "d.py")["code_duplication"]
    assert {f.symbol for f in found} == {"a", "b"}
    assert "b" in found[0].message or "a" in found[0].message


# --- config --------------------------------------------------------------------------


def test_config_overrides(tmp_path):
    (tmp_path / ".aihealth.toml").write_text(
        '[thresholds]\nlarge_method = 5\n[weights]\nlarge_file = 0\n[scan]\nexclude = ["gen/*"]\n'
    )
    cfg = load_config(tmp_path)
    assert cfg.threshold("large_method") == 5
    assert cfg.weight("large_file") == 0
    assert "gen/*" in cfg.excludes and "*.g.dart" in cfg.excludes


def test_config_rejects_unknown_keys(tmp_path):
    (tmp_path / ".aihealth.toml").write_text("[thresholds]\nlarge_methd = 5\n")
    with pytest.raises(ConfigError, match="large_methd"):
        load_config(tmp_path)


def test_dart_test_calls_configurable():
    src = "void main() { group('g', () { test('t', () {}); }); }"
    default = parse_source(src.encode(), "a_test.dart")
    assert [f.kind for f in default.all_functions()] == ["function", "test", "test"]
    cfg = Config()
    cfg.data["dart"]["test_closure_calls"] = ["group"]
    only_group = parse_source(src.encode(), "a_test.dart", cfg)
    assert [f.qualname for f in only_group.all_functions()] == ["main", 'main.group("g")']


# --- dart test closures -------------------------------------------------------------


def test_dart_test_closures_are_units():
    tests = "\n".join(
        f"    test('case {i}', () {{\n      if (a) {{ if (b) {{ expect(1, 1); }} }}\n    }});"
        for i in range(30)
    )
    src = f"void main() {{\n  group('suite', () {{\n{tests}\n  }});\n}}\n"
    module = parse_source(src.encode(), "x_test.dart")
    by_q = {f.qualname: f for f in module.all_functions()}
    assert by_q["main"].loc == 2  # own lines only
    assert by_q['main.group("suite")'].loc == 2
    assert by_q['main.group("suite").test("case 0")'].loc == 3
    found = smells(src, "x_test.dart")
    assert "large_method" not in found and "cognitive_complexity" not in found


def test_duplicate_test_names_get_suffix():
    src = "void main() { test('same', () {}); test('same', () {}); }"
    names = [f.qualname for f in parse_source(src.encode(), "t.dart").all_functions()]
    assert names == ["main", 'main.test("same")', 'main.test("same")#2']


# --- scan + cli -------------------------------------------------------------------------


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "ok.dart").write_text("int add(int a, int b) => a + b;\n")
    (tmp_path / "lib" / "model.g.dart").write_text("int gen() => 1;\n")
    (tmp_path / "bad.py").write_text(
        "def f(a, b, c, d, e, g):\n"
        + "    if a:\n        if b:\n            if c:\n                if d:\n                    pass\n"
    )
    (tmp_path / "notes.txt").write_text("not code")
    return tmp_path


def test_exclude_matching():
    assert is_excluded("lib/model.g.dart", ["*.g.dart"])
    assert is_excluded("a/b/generated/x.dart", ["generated/*"])
    assert is_excluded("build/x.dart", ["build/*"])
    assert not is_excluded("lib/builder.dart", ["build/*"])


def test_scan_tree(tmp_path):
    result = scan(_repo(tmp_path), Config())
    paths = {f.path for f in result.files}
    assert paths == {"bad.py", "lib/ok.dart"}  # generated + non-code skipped
    by_path = {f.path: f for f in result.files}
    assert by_path["lib/ok.dart"].score == 10.0
    assert by_path["bad.py"].score < 10.0
    assert result.score == project_score(result.files)


def test_cli_scan_json_and_fail_under(tmp_path, capsys):
    root = _repo(tmp_path)
    assert main(["scan", str(root), "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["file_count"] == 2
    assert data["files"][0]["path"] == "bad.py"  # worst first
    assert data["files"][0]["findings"][0]["hint"]
    assert main(["scan", str(root), "--fail-under", "9.99"]) == 1


def test_cli_scan_text(tmp_path, capsys):
    assert main(["scan", str(_repo(tmp_path))]) == 0
    out = capsys.readouterr().out
    assert "project score" in out and "Deep Nesting" in out and "bad.py" in out


def test_cli_bad_config(tmp_path, capsys):
    (tmp_path / ".aihealth.toml").write_text("[nope]\n")
    assert main(["scan", str(tmp_path)]) == 2
    assert "config error" in capsys.readouterr().err
