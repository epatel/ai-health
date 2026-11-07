"""JavaScript adapter: assigned functions, JS parameters, JSX, shared golden metrics.

The adapter reuses the TypeScript one (tests/test_typescript.py covers the
shared unit rules); these tests pin what differs in JavaScript.
"""

import subprocess
from pathlib import Path

import pytest
from helpers import ECMASCRIPT_EXPECTED, ECMASCRIPT_SONAR, findings_of

from aihealth.config import Config, load_config
from aihealth.engine import scan
from aihealth.lang import adapter_for, is_supported, parse_file, parse_source
from aihealth.metrics import function_metrics

FIXTURE = Path(__file__).parent / "fixtures" / "sample.js"


def units(src: str, path: str = "x.js", config: Config | None = None) -> list[str]:
    module = parse_source(src.encode(), path, config)
    assert module.error_count == 0
    return [f.qualname for f in module.all_functions()]


@pytest.mark.parametrize("path", ["a.js", "a.mjs", "a.cjs", "a.jsx"])
def test_registered(path):
    assert is_supported(path)
    assert adapter_for(path).language == "javascript"


def test_fixture_units_and_kinds():
    fns = parse_file(FIXTURE).all_functions()
    assert [f"{f.qualname} {f.kind}" for f in fns] == [
        "ProfileCache.onHit method",
        "ProfileCache.constructor constructor",
        "ProfileCache.size getter",
        "ProfileCache.load method",
        "ProfileCache.classify method",
        "sum function",
        "parse function",
        "module.exports.format function",
        "Card function",
    ]


ASSIGNED = """
module.exports = function createApp() {};
exports.helper = (x) => x + 1;
app.handle = function handle(req, res) {
    stack.forEach(function (layer) { layer(req, res); });
};
Router.prototype.route = function route(path) {};
module.exports.utils = { trim(s) { return s.trim(); } };
"""


def test_assigned_functions_are_units():
    assert units(ASSIGNED) == [
        "module.exports",
        "exports.helper",
        "app.handle",  # its forEach callback stays inline
        "Router.route",  # `.prototype.` dropped
        "module.exports.utils.trim",
    ]


def test_typescript_keeps_assignments_inline():
    """The assignment rule is JavaScript-only; TypeScript scores are unchanged."""
    assert units(ASSIGNED, "x.ts") == []


def test_javascript_parameters():
    fns = {f.qualname: f for f in parse_file(FIXTURE).all_functions()}
    load = [(p.name, p.optional, p.variadic) for p in fns["ProfileCache.load"].params]
    assert load == [("id", False, False), ("retries", True, False), ("...rest", False, True)]
    ctor = fns["ProfileCache.constructor"].params
    assert [(p.name, p.optional) for p in ctor] == [
        ("store", False),
        ("{ logger = console, limit }", True),
    ]
    assert all(p.type_text is None for p in ctor)  # untyped: no Primitive Obsession


@pytest.mark.parametrize(("qualname", "cyc", "cog", "nest"), ECMASCRIPT_EXPECTED)
def test_javascript_complexity(qualname, cyc, cog, nest):
    module = parse_source(ECMASCRIPT_SONAR.encode(), "x.js")
    fn = next(f for f in module.all_functions() if f.qualname == qualname)
    m = function_metrics(fn, adapter_for("x.js"))
    assert (m.cyclomatic, m.cognitive, m.max_nesting) == (cyc, cog, nest)


def test_jsx_in_js_files():
    fns = {f.qualname: f for f in parse_file(FIXTURE).all_functions()}
    assert function_metrics(fns["Card"], adapter_for(FIXTURE)).widget_depth == 2
    tree = "<div>" * 10 + "x" + "</div>" * 10
    found = findings_of(
        f"function Deep() {{\n    return {tree};\n}}\n", "x.jsx", "deep_widget_tree"
    )
    assert found and found[0].value == 10


def test_test_closures_and_config(tmp_path):
    spec = 'describe("app", () => {\n    it("works", () => { expect(1).toBe(1); });\n});\n'
    assert units(spec) == ['describe("app")', 'describe("app").it("works")']
    (tmp_path / ".aihealth.toml").write_text('[javascript]\ntest_closure_calls = ["it"]\n')
    assert units(spec, config=load_config(tmp_path)) == ['it("works")']


def test_scan_skips_minified_bundles(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "app.js").write_text("export const one = () => 1;\n")
    (tmp_path / "vendor.min.js").write_text("var a=function(){return 1};\n")
    (tmp_path / "app.bundle.js").write_text("var b=function(){return 2};\n")
    assert [f.path for f in scan(tmp_path, Config()).files] == ["app.js"]
