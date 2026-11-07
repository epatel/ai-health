"""TypeScript / TSX adapters: which functions are units, golden metrics, JSX."""

import subprocess
from pathlib import Path

import pytest
from helpers import ECMASCRIPT_EXPECTED, ECMASCRIPT_SONAR, check_base_types, findings_of

from aihealth.config import Config, load_config
from aihealth.engine import scan
from aihealth.lang import adapter_for, is_supported, parse_file, parse_source
from aihealth.metrics import function_metrics
from aihealth.smells import lcom4

FIXTURES = Path(__file__).parent / "fixtures"


def metrics(src: str, path: str = "x.ts", config: Config | None = None) -> dict:
    module = parse_source(src.encode(), path, config)
    assert module.error_count == 0
    adapter = adapter_for(path, config)
    return {f.qualname: function_metrics(f, adapter) for f in module.all_functions()}


def units(src: str, path: str = "x.ts") -> list[str]:
    return [f.qualname for f in parse_source(src.encode(), path).all_functions()]


@pytest.mark.parametrize(
    ("path", "language"),
    [("a.ts", "typescript"), ("a.mts", "typescript"), ("a.cts", "typescript"), ("a.tsx", "tsx")],
)
def test_registered(path, language):
    assert is_supported(path)
    assert adapter_for(path).language == language


# --- which functions are units ---------------------------------------------------------

FIXTURE_UNITS = """
Store.exists method
ProfileCache.constructor constructor
ProfileCache.size getter
ProfileCache.limit= setter
ProfileCache.load method
ProfileCache.classify method
ProfileCache.sum method
ProfileCache.sum.add function
greeting function
parse function
helpers.double function
helpers.triple function
sealed function
ids function
describe("ProfileCache") test
describe("ProfileCache").it("classifies numbers") test
"""


def test_fixture_units_and_kinds():
    expected = [tuple(line.rsplit(" ", 1)) for line in FIXTURE_UNITS.strip().splitlines()]
    fns = parse_file(FIXTURES / "sample.ts").all_functions()
    assert [(f.qualname, f.kind) for f in fns] == expected


def test_named_function_values_are_units_inline_callbacks_are_not():
    src = """
export const handler = async (req: Request) => {
    const ids = req.items.map((item) => item.id);
    return ids.filter(function (id) { return id > 0; });
};
const config = { load() { return 1; }, save: () => 2 };
setTimeout(() => { console.log("tick"); }, 10);
export default () => 42;
"""
    assert units(src) == ["handler", "config.load", "config.save", "default"]


def test_class_fields_namespaces_and_nested_declarations():
    src = """
namespace Api {
    export function get() {}
}
class Button {
    onClick = () => { this.clicked = true; };
    private render(): void {
        function local() {}
        const helper = () => 1;
    }
}
"""
    assert units(src) == [
        "Api.get",
        "Button.onClick",
        "Button.render",
        "Button.render.local",
        "Button.render.helper",
    ]


def test_abstract_methods_and_overloads_are_not_units():
    src = """
abstract class A {
    abstract run(): void;
    go(x: number): void;
    go(x: string): void;
    go(x: unknown) {}
}
declare function external(): void;
"""
    assert units(src) == ["A.go"]


def test_parameters():
    fns = {f.qualname: f for f in parse_file(FIXTURES / "sample.ts").all_functions()}
    load = [(p.name, p.type_text, p.optional) for p in fns["ProfileCache.load"].params]
    assert load == [("id", "string", False), ("retries", None, True)]
    src = "function f(this: Window, a: number, ...rest: string[]) {}"
    params = parse_source(src.encode(), "x.ts").functions[0].params
    assert [(p.name, p.variadic) for p in params] == [("a", False), ("...rest", True)]


# --- golden metrics ----------------------------------------------------------------


@pytest.mark.parametrize(("qualname", "cyc", "cog", "nest"), ECMASCRIPT_EXPECTED)
def test_typescript_complexity(qualname, cyc, cog, nest):
    m = metrics(ECMASCRIPT_SONAR)[qualname]
    assert (m.cyclomatic, m.cognitive, m.max_nesting) == (cyc, cog, nest)


def test_fixture_metrics():
    path = FIXTURES / "sample.ts"
    fns = {f.qualname: f for f in parse_file(path).all_functions()}
    load = function_metrics(fns["ProfileCache.load"], adapter_for(path))
    # for +1, if in try +2, `break outer` +1, catch +2, if in catch +3, `&&` +1, ternary +1
    assert (load.cyclomatic, load.cognitive, load.max_nesting) == (7, 11, 3)


def test_conditions_and_bumps():
    src = """
function f(rows: number[][], a: boolean, b: boolean): void {
    if (a && !(b || rows.length > 0)) { }
    for (const row of rows) {
        for (const x of row) {
            if (x > 0) console.log(x);
        }
        for (const x of row) {
            if (x < 0) console.log(x);
        }
    }
}
"""
    m = metrics(src)["f"]
    assert [(b.operators, b.is_condition) for b in m.bool_exprs] == [(2, True)]
    assert len(m.bumps) == 1  # two top-level statements; only the for-of nests 2+ deep


# --- test closures --------------------------------------------------------------------

SPEC = """
describe("cache", () => {
    beforeEach(() => { reset(); });
    it("hits", async () => {
        if (ready) { expect(get(1)).toBe(1); }
    });
});
"""


def test_test_closures_are_units():
    m = metrics(SPEC)
    assert list(m) == [
        'describe("cache")',
        'describe("cache").beforeEach',
        'describe("cache").it("hits")',
    ]
    assert m['describe("cache").it("hits")'].cognitive == 1


def test_test_closure_calls_configurable(tmp_path):
    (tmp_path / ".aihealth.toml").write_text('[typescript]\ntest_closure_calls = ["it"]\n')
    config = load_config(tmp_path)
    assert list(metrics(SPEC, "x.tsx", config)) == ['it("hits")']


# --- class smells ---------------------------------------------------------------------

# declared type => base types for Primitive Obsession (- = unknown)
BASE_TYPES = """
number => number
string | undefined => string
Order[] => Order
readonly number[] => number
Map<string, Order> => string Order
Promise<Profile | null> => Profile
"a" | "b" => "a" "b"
(x: number) => void => -
{ id: string } => -
[number, number] => -
"""


def test_base_types():
    check_base_types("x.ts", BASE_TYPES)
    assert adapter_for("x.ts").is_primitive("Record<string, number>") is True
    assert adapter_for("x.ts").is_primitive("boolean") is None


def test_member_refs_and_lcom4():
    src = """
class Mixed {
    private users: string[] = [];
    #cache = new Map<number, number>();
    constructor() { this.users = []; }
    addUser(u: string) { this.users.push(u); }
    countUsers() { return this.users.length; }
    store(k: number) { this.#cache.set(k, k); }
    lookup(k: number) { return this.#cache.get(k); }
}
"""
    cls = parse_source(src.encode(), "x.ts").classes[0]
    adapter = adapter_for("x.ts")
    assert adapter.member_refs(cls)["Mixed.store"] == {"cache"}
    assert lcom4(cls, adapter) == 2


# --- JSX ------------------------------------------------------------------------------


def test_tsx_widget_depth_and_units():
    path = FIXTURES / "sample.tsx"
    adapter = adapter_for(path)
    fns = {f.qualname: f for f in parse_file(path).all_functions()}
    assert list(fns) == ["ItemList", "Badge"]
    item_list = function_metrics(fns["ItemList"], adapter)
    assert item_list.widget_depth == 4  # section > ul > li > span
    assert item_list.cognitive == 1  # `open && (...)` conditional rendering


def test_deep_jsx_gets_component_hint():
    tree = "<div>" * 10 + "x" + "</div>" * 10
    src = f"export function Deep() {{\n    return {tree};\n}}\n"
    found = findings_of(src, "x.tsx", "deep_widget_tree")
    assert found and found[0].value == 10
    assert "components" in found[0].hint


def test_scan_skips_declaration_files(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text("export const one = () => 1;\n")
    (tmp_path / "src" / "index.d.ts").write_text("export declare const one: () => number;\n")
    assert [f.path for f in scan(tmp_path, Config()).files] == ["src/index.ts"]
