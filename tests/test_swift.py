"""Swift adapter: extraction, golden metrics, Swift-specific constructs."""

import subprocess
from pathlib import Path

import pytest
from helpers import findings_of

from aihealth.config import Config, load_config
from aihealth.engine import scan, score_source
from aihealth.lang import adapter_for, is_supported, parse_file, parse_source
from aihealth.metrics import function_metrics
from aihealth.smells import lcom4

FIXTURE = Path(__file__).parent / "fixtures" / "sample.swift"


def metrics(src: str, config: Config | None = None) -> dict:
    module = parse_source(src.encode(), "x.swift", config)
    assert module.error_count == 0
    adapter = adapter_for("x.swift", config)
    return {f.qualname: function_metrics(f, adapter) for f in module.all_functions()}


def functions(path: Path = FIXTURE) -> dict:
    return {f.qualname: f for f in parse_file(path).all_functions()}


def test_registered_for_swift_files():
    assert is_supported("Sources/App/main.swift")
    assert adapter_for("a.swift").language == "swift"


# --- extraction -----------------------------------------------------------------------


FIXTURE_UNITS = """
Profile.init constructor
Profile.isAdult getter
Profile.displayName getter
Profile.displayName= setter
Profile.subscript getter
Profile.== method
ProfileCache.get method
ProfileCache.put method
ProfileLoader.init constructor
ProfileLoader.deinit destructor
ProfileLoader.load method
ProfileLoader.classify method
ProfileLoader.sum method
ProfileLoader.sum.add function
Profile.greeting method
ProfileView.body getter
topLevel function
#Preview function
"""


def test_fixture_units_and_kinds():
    expected = [tuple(line.split()) for line in FIXTURE_UNITS.strip().splitlines()]
    assert [(q, f.kind) for q, f in functions().items()] == expected


def test_type_kinds_and_extensions():
    classes = [(c.kind, c.name) for c in parse_file(FIXTURE).classes]
    assert classes == [
        ("struct", "Profile"),
        ("enum", "LoadError"),
        ("actor", "ProfileCache"),
        ("class", "ProfileLoader"),
        ("extension", "Profile"),
        ("struct", "ProfileView"),
    ]


def test_spans_exclude_nothing_and_loc_excludes_nested_functions():
    fns = functions()
    sum_fn = fns["ProfileLoader.sum"]
    assert (sum_fn.start_line, sum_fn.end_line) == (89, 104)
    assert sum_fn.loc == 15  # the nested `add` line belongs to `add`
    assert fns["ProfileLoader.sum.add"].loc == 1


def test_parameters_labels_defaults_variadics():
    fns = functions()
    params = [(p.name, p.type_text, p.named, p.optional) for p in fns["Profile.init"].params]
    assert params == [
        ("id", "UUID", True, True),
        ("name", "String", True, False),
        ("age", "Int?", True, True),
    ]
    top = fns["topLevel"].params
    assert [(p.name, p.named, p.variadic) for p in top] == [
        ("x", False, False),  # `_ x`: positional
        ("y", True, False),  # `label y`
        ("rest", False, True),
    ]


def test_parameter_modifiers_are_not_the_type():
    src = "func f(z: inout Int, cb: @escaping (Int) -> Void) {}"
    fn = parse_source(src.encode(), "x.swift").functions[0]
    assert [p.type_text for p in fn.params] == ["Int", "(Int) -> Void"]


def test_observers_nested_types_and_script_closures():
    src = """
struct Outer {
    struct Inner {
        func f() {}
    }
    var x = 0 {
        willSet { print(newValue) }
        didSet { if x < 0 { x = 0 } }
    }
}
measure {
    func helper() {}
}
"""
    module = parse_source(src.encode(), "x.swift")
    assert [c.name for c in module.classes] == ["Outer", "Outer.Inner"]
    names = [f.qualname for f in module.all_functions()]
    assert names == ["Outer.Inner.f", "Outer.x.willSet", "Outer.x.didSet", "helper"]


def test_protocol_requirements_are_not_units():
    src = "protocol P { func p(); var x: Int { get } }\nextension P { func p() {} }"
    assert [f.qualname for f in parse_source(src.encode(), "x.swift").all_functions()] == ["P.p"]


# --- golden metrics ----------------------------------------------------------------

SONAR = """
func sumOfPrimes(_ max: Int) -> Int {
    var total = 0
    outer: for i in 1...max {
        for j in 2..<i {
            if i % j == 0 {
                continue outer
            }
        }
        total += i
    }
    return total
}

func getWords(_ number: Int) -> String {
    switch number {
    case 1: return "one"
    case 2: return "a couple"
    case 3: return "a few"
    default: return "lots"
    }
}

func seq(_ a: Bool, _ b: Bool, _ c: Bool, _ d: Bool, _ e: Bool, _ f: Bool) -> Bool {
    return a && b && c || d || e && f
}

func fact(_ n: Int) -> Int {
    if n <= 1 { return 1 }
    return n * fact(n - 1)
}

func elseIf(_ a: Int) -> Int {
    if a > 1 {
        return 1
    } else if a > 0 {
        return 2
    } else {
        return 3
    }
}

func handlers() {
    do {
        try work()
    } catch MyError.bad {
        recover()
    } catch {
        log(error)
    }
}

func guarded(_ x: Int?) -> Int {
    guard let x else { return 0 }
    return x
}

func closures(_ xs: [Int]) -> [Int] {
    xs.map { x in
        if x > 0 { return x }
        return -x
    }
}

func nested(_ xs: [Int]) -> Int {
    func inner(_ v: Int) -> Int {
        if v > 0 { return v }
        return 0
    }
    return xs.reduce(0) { $0 + inner($1) }
}

func ternary(_ x: Int) -> Int {
    return x > 0 ? 1 : 2
}

func loops(_ n: Int) {
    var i = 0
    outer: while i < n {
        repeat {
            i += 1
            break outer
        } while i < 10
    }
}

func unknownDefault(_ d: Dir) -> Int {
    switch d {
    case .up: return 1
    @unknown default: return 0
    }
}

func ifExpression(_ a: Int?) -> String {
    let s = if a == nil { "none" } else { "some" }
    return s
}

final class Walker {
    func walk(_ n: Int) -> Int {
        if n == 0 { return 0 }
        return self.walk(n - 1)
    }
}
"""

# qualname, cyclomatic, cognitive, max nesting
SONAR_EXPECTED = [
    ("sumOfPrimes", 4, 7, 3),
    ("getWords", 4, 1, 1),
    ("seq", 6, 3, 0),
    ("fact", 2, 2, 1),
    ("elseIf", 3, 3, 1),
    ("handlers", 3, 2, 1),
    ("guarded", 2, 1, 1),
    ("closures", 2, 2, 1),  # the if sits one level inside the closure
    ("nested", 1, 0, 0),  # `inner` is scored on its own
    ("nested.inner", 2, 1, 1),
    ("ternary", 2, 1, 0),
    ("loops", 3, 4, 2),  # while +1, repeat +2, `break outer` +1
    ("unknownDefault", 2, 1, 1),  # `@unknown default` is the default arm
    ("ifExpression", 2, 2, 1),
    ("Walker.walk", 2, 2, 1),  # `self.walk(...)` is recursion
]


@pytest.mark.parametrize(("qualname", "cyc", "cog", "nest"), SONAR_EXPECTED)
def test_swift_complexity(qualname, cyc, cog, nest):
    m = metrics(SONAR)[qualname]
    assert (m.cyclomatic, m.cognitive, m.max_nesting) == (cyc, cog, nest)


def test_fixture_metrics():
    adapter = adapter_for(FIXTURE)
    fns = functions()
    load = function_metrics(fns["ProfileLoader.load"], adapter)
    # for +1, three catches +2 each, `break outer` +1, `&&` in a where +1
    assert (load.cyclomatic, load.cognitive, load.max_nesting) == (6, 9, 2)
    total = function_metrics(fns["ProfileLoader.sum"], adapter)
    assert (total.cyclomatic, total.cognitive) == (7, 8)


def test_precedence_quirk_keeps_conditions():
    """The grammar nests `||` inside `n > 100`; it is still the if's condition."""
    m = metrics("func f(_ n: Int, _ b: Bool) {\n    if n > 100 || n == 42 && b { }\n}\n")["f"]
    assert [(b.operators, b.is_condition) for b in m.bool_exprs] == [(2, True)]
    assert m.cognitive == 1 + 2  # the if, plus two operator sequences


def test_bumps_look_through_a_labeled_loop():
    src = """
func f(_ xs: [[Int]]) {
    outer: for row in xs {
        for x in row {
            if x > 0 { print(x) }
        }
        for x in row {
            if x < 0 { print(x) }
        }
    }
}
"""
    assert len(metrics(src)["f"].bumps) == 2


def test_swiftui_body_widget_depth():
    m = function_metrics(functions()["ProfileView.body"], adapter_for(FIXTURE))
    assert m.widget_depth == 4  # NavigationStack > VStack > HStack > Text


# --- test closures --------------------------------------------------------------------

QUICK = """
final class ProfileSpec: QuickSpec {
    override class func spec() {
        describe("a profile") {
            beforeEach { setUp() }
            it("greets") {
                if ready { expect(1).to(equal(1)) }
            }
        }
    }
}
"""


def test_quick_closures_are_their_own_units():
    m = metrics(QUICK)
    assert list(m) == [
        "ProfileSpec.spec",
        'ProfileSpec.spec.describe("a profile")',
        'ProfileSpec.spec.describe("a profile").beforeEach',
        'ProfileSpec.spec.describe("a profile").it("greets")',
    ]
    assert m['ProfileSpec.spec.describe("a profile").it("greets")'].cognitive == 1


def test_test_closure_calls_configurable(tmp_path):
    (tmp_path / ".aihealth.toml").write_text('[swift]\ntest_closure_calls = ["it"]\n')
    m = metrics(QUICK, load_config(tmp_path))
    assert list(m) == ["ProfileSpec.spec", 'ProfileSpec.spec.it("greets")']


# --- class smells -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("type_text", "bases"),
    [
        ("Int", ["Int"]),
        ("String?", ["String"]),
        ("[Order]", ["Order"]),
        ("[String: Int]", ["String", "Int"]),
        ("[String: [Order]]", ["String", "Order"]),
        ("Set<UUID>", ["UUID"]),
        ("some Collection<Element>", ["Element"]),
        ("Swift.Int", ["Int"]),
        ("(Int) -> Void", None),
        ("@escaping @Sendable () -> Void", None),
        ("(Int, String)", None),
    ],
)
def test_base_types(type_text, bases):
    assert adapter_for("x.swift").base_types(type_text) == bases


def test_primitive_judgement():
    adapter = adapter_for("x.swift")
    assert adapter.is_primitive("[String: Int]") is True
    assert adapter.is_primitive("[Order]") is False
    assert adapter.is_primitive("Bool") is None  # flags are neutral


LOW_COHESION = """
final class Mixed {
    private var users: [String] = []
    private var cache: [Int: Int] = [:]
    static var shared = Mixed()

    func addUser(_ u: String) { users.append(u) }
    func countUsers() -> Int { self.users.count }
    func store(_ k: Int) { cache[k] = k }
    func lookup(_ k: Int) -> Int? { cache[k] }
    func other(_ o: Mixed) -> Int { o.users.count + Mixed.shared.cache.count }
}
"""


def test_member_refs_and_lcom4():
    module = parse_source(LOW_COHESION.encode(), "x.swift")
    cls = module.classes[0]
    refs = adapter_for("x.swift").member_refs(cls)
    assert refs["Mixed.countUsers"] == {"users"}  # via self.
    assert refs["Mixed.other"] == set()  # `o.users` belongs to another object
    assert lcom4(cls, adapter_for("x.swift")) == 2


# --- scan / config ----------------------------------------------------------------


def test_scan_skips_package_build_and_docc(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "Sources").mkdir()
    (tmp_path / "Sources" / "App.swift").write_text("func main() {}\n")
    for skipped in (".build/checkouts/x", "Docs.docc/Resources", "Pods/Alamofire"):
        (tmp_path / skipped).mkdir(parents=True)
        (tmp_path / skipped / "Code.swift").write_text("func f() {}\n")
    (tmp_path / "Sources" / "Strings.generated.swift").write_text("func g() {}\n")
    result = scan(tmp_path, Config())
    assert [f.path for f in result.files] == ["Sources/App.swift"]


def test_deep_nesting_is_reported():
    src = """
func f(_ xs: [[Int]]) {
    for row in xs {
        for x in row {
            if x > 0 {
                while x > 10 { break }
            }
        }
    }
}
"""
    found = score_source(src.encode(), "x.swift").findings
    assert any(f.smell == "deep_nesting" and f.symbol == "f" for f in found)


@pytest.mark.parametrize(
    "head",
    [
        b"// swiftlint:disable all\n// Generated using SwiftGen - https://github.com/SwiftGen\n",
        b"// Generated using Sourcery 2.1.7\n// DO NOT EDIT\n",
        b"// DO NOT EDIT.\n// swift-format-ignore-file\n// Generated by the Swift generator plugin\n",
    ],
)
def test_generated_swift_headers(head):
    from aihealth.lang.base import is_generated

    assert is_generated(head + b"enum L10n {}\n")


def test_deep_view_tree_gets_swiftui_hint():
    views = "".join(f"V{i} {{ " for i in range(10)) + 'Text("x")' + " }" * 10
    src = f"struct S: View {{\n    var body: some View {{\n        {views}\n    }}\n}}\n"
    found = findings_of(src, "x.swift", "deep_widget_tree")
    assert found and found[0].value == 11
    assert "View` structs" in found[0].hint


VIEW_BUILDERS = """
struct V: View {
    var body: some View {
        NavigationStack {
            VStack(alignment: .leading) {
                ForEach(items) { item in
                    if item.done {
                        Text(item.name)
                    }
                }
            }
        }
    }

    func work(_ xs: [Int]) {
        Task {
            xs.forEach { x in
                if x > 0 { print(x) }
            }
        }
    }
}
"""


def test_view_builder_closures_add_no_cognitive_nesting():
    m = metrics(VIEW_BUILDERS)
    assert m["V.body"].cognitive == 1  # the if, at level 0 despite 3 view closures
    assert m["V.work"].cognitive == 3  # Task { } and forEach { } still nest: +1 +2
    assert m["V.body"].widget_depth == 4  # depth is still measured


def test_fixture_body_cognitive():
    m = function_metrics(functions()["ProfileView.body"], adapter_for(FIXTURE))
    assert m.cognitive == 1
