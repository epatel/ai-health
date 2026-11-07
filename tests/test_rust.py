"""Rust adapter: extraction, golden metrics, Rust-specific constructs."""

import subprocess
from pathlib import Path

import pytest

from aihealth.config import Config, load_config
from aihealth.engine import scan, score_source
from aihealth.lang import adapter_for, is_supported, parse_file, parse_source
from aihealth.metrics import function_metrics
from aihealth.smells import lcom4

FIXTURE = Path(__file__).parent / "fixtures" / "sample.rs"


def metrics(src: str, config: Config | None = None) -> dict:
    module = parse_source(src.encode(), "x.rs", config)
    assert module.error_count == 0
    adapter = adapter_for("x.rs", config)
    return {f.qualname: function_metrics(f, adapter) for f in module.all_functions()}


def test_registered_for_rust_files():
    assert is_supported("src/main.rs")
    assert adapter_for("lib.rs").language == "rust"


# --- extraction -----------------------------------------------------------------------

FIXTURE_UNITS = """
Store.exists method
Profile.new method
Profile.is_adult method
Profile.greeting method
Profile.fmt method
Cache.load method
Cache.classify method
Cache.sum method
Cache.sum.add function
parse function
raw_read function
"""


def test_fixture_units_and_kinds():
    expected = [tuple(line.split()) for line in FIXTURE_UNITS.strip().splitlines()]
    fns = parse_file(FIXTURE).all_functions()
    assert [(f.qualname, f.kind) for f in fns] == expected


def test_impl_and_trait_blocks_are_classes():
    classes = [f"{c.kind} {c.name}" for c in parse_file(FIXTURE).classes]
    # the second Profile is `impl fmt::Display for Profile`
    assert classes == ["trait Store", "impl Profile", "impl Profile", "impl Cache"]


def test_impl_names_drop_references_and_generics():
    src = "impl<'a, R> X for &'a mut Deserializer<R> { fn f(&self) {} }"
    assert [c.name for c in parse_source(src.encode(), "x.rs").classes] == ["Deserializer"]


def test_parameters_skip_self():
    fns = {f.qualname: f for f in parse_file(FIXTURE).all_functions()}
    params = [(p.name, p.type_text, p.named) for p in fns["Cache.load"].params]
    assert params == [("id", "u64", False), ("retries", "usize", False)]
    assert fns["Profile.is_adult"].params == []


def test_modules_prefix_qualnames_and_nested_items():
    src = """
mod net {
    pub fn get() {}
    pub mod http {
        pub fn post() {}
    }
}
fn outer() {
    fn helper() {}
    struct Local;
    impl Local { fn run(&self) {} }
}
"""
    names = [f.qualname for f in parse_source(src.encode(), "x.rs").all_functions()]
    assert names == ["net.get", "net.http.post", "outer", "outer.helper", "outer.Local.run"]


# --- test modules ---------------------------------------------------------------------

WITH_TESTS = """
pub fn add(a: i32, b: i32) -> i32 {
    a + b
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn adds() {
        assert_eq!(add(1, 2), 3);
    }
}
"""


def test_cfg_test_modules_are_left_out_by_default():
    module = parse_source(WITH_TESTS.encode(), "x.rs")
    assert [f.qualname for f in module.all_functions()] == ["add"]
    assert module.loc == 3 + 1  # add, plus the `#[cfg(test)]` attribute line


def test_cfg_test_modules_scored_when_configured(tmp_path):
    (tmp_path / ".aihealth.toml").write_text("[rust]\nscore_test_modules = true\n")
    module = parse_source(WITH_TESTS.encode(), "x.rs", load_config(tmp_path))
    assert [f.qualname for f in module.all_functions()] == ["add", "tests.adds"]


def test_test_module_duplication_is_ignored():
    test = (
        "    #[test]\n    fn t{i}() {{\n"
        + "        let v = vec![1, 2, 3, 4, 5];\n" * 8
        + "    }}\n"
    )
    body = "".join(test.format(i=i) for i in range(3))
    src = f'pub fn f() {{}}\n\n#[cfg(all(test, feature = "x"))]\nmod tests {{\n{body}}}\n'
    found = score_source(src.encode(), "x.rs").findings
    assert not any(f.smell == "code_duplication" for f in found)


# --- golden metrics ----------------------------------------------------------------

SONAR = """
fn sum_of_primes(max: u64) -> u64 {
    let mut total = 0;
    'outer: for i in 1..=max {
        for j in 2..i {
            if i % j == 0 {
                continue 'outer;
            }
        }
        total += i;
    }
    total
}

fn get_words(number: u32) -> &'static str {
    match number {
        1 => "one",
        2 => "a couple",
        3 => "a few",
        _ => "lots",
    }
}

fn seq(a: bool, b: bool, c: bool, d: bool, e: bool, f: bool) -> bool {
    a && b && c || d || e && f
}

fn fact(n: u64) -> u64 {
    if n <= 1 {
        return 1;
    }
    n * fact(n - 1)
}

fn else_if(a: i32) -> i32 {
    if a > 1 {
        1
    } else if a > 0 {
        2
    } else {
        3
    }
}

fn let_else(x: Option<i32>) -> i32 {
    let Some(v) = x else { return 0 };
    v
}

fn closures(xs: &[i32]) -> Vec<i32> {
    xs.iter()
        .map(|x| {
            if *x > 0 {
                *x
            } else {
                -x
            }
        })
        .collect()
}

fn nested(xs: &[i32]) -> i32 {
    fn inner(v: i32) -> i32 {
        if v > 0 {
            v
        } else {
            0
        }
    }
    xs.iter().map(|x| inner(*x)).sum()
}

fn loops(n: i32) {
    let mut i = 0;
    'outer: while i < n {
        loop {
            i += 1;
            break 'outer;
        }
    }
}

fn let_chain(x: Option<i32>, ok: bool) -> bool {
    if let Some(v) = x && v > 0 && ok {
        return true;
    }
    false
}

struct Walker;

impl Walker {
    fn walk(&self, n: u32) -> u32 {
        if n == 0 {
            return 0;
        }
        self.walk(n - 1)
    }

    fn depth(n: u32) -> u32 {
        if n == 0 { 0 } else { Self::depth(n - 1) + 1 }
    }
}
"""

# qualname, cyclomatic, cognitive, max nesting
SONAR_EXPECTED = [
    ("sum_of_primes", 4, 7, 3),
    ("get_words", 4, 1, 1),  # `_` is the default arm
    ("seq", 6, 3, 0),
    ("fact", 2, 2, 1),
    ("else_if", 3, 3, 1),  # else-if is unwrapped from else_clause
    ("let_else", 2, 1, 1),  # like Swift `guard`: one branch, no else
    ("closures", 2, 3, 1),  # if at closure level 1 (+2), else +1
    ("nested", 1, 0, 0),
    ("nested.inner", 2, 2, 1),
    ("loops", 3, 4, 2),  # while +1, loop +2, `break 'outer` +1
    ("let_chain", 4, 2, 1),  # two `&&`: 2 paths, but one sequence (+1 cognitive)
    ("Walker.walk", 2, 2, 1),  # `self.walk(..)` is recursion
    ("Walker.depth", 2, 3, 1),  # `Self::depth(..)` is recursion
]


@pytest.mark.parametrize(("qualname", "cyc", "cog", "nest"), SONAR_EXPECTED)
def test_rust_complexity(qualname, cyc, cog, nest):
    m = metrics(SONAR)[qualname]
    assert (m.cyclomatic, m.cognitive, m.max_nesting) == (cyc, cog, nest)


def test_fixture_metrics():
    adapter = adapter_for(FIXTURE)
    fns = {f.qualname: f for f in parse_file(FIXTURE).all_functions()}
    load = function_metrics(fns["Cache.load"], adapter)
    # for +1, match +2, `break 'outer` +1, `&&` in a match guard +1
    assert (load.cyclomatic, load.cognitive, load.max_nesting) == (7, 5, 2)
    greeting = function_metrics(fns["Profile.greeting"], adapter)
    assert (greeting.cyclomatic, greeting.cognitive) == (5, 7)


def test_let_chain_is_a_condition():
    m = metrics(
        "fn f(x: Option<i32>, a: bool, b: bool) {\n    if let Some(v) = x && a || b { }\n}\n"
    )
    assert [(b.operators, b.is_condition) for b in m["f"].bool_exprs] == [(2, True)]


def test_bumps_look_through_a_loop_statement():
    src = """
fn f(rows: &[Vec<i32>]) {
    for row in rows {
        for x in row {
            if *x > 0 { println!("{x}"); }
        }
        for x in row {
            if *x < 0 { println!("{x}"); }
        }
    }
}
"""
    assert len(metrics(src)["f"].bumps) == 2


# --- class smells -------------------------------------------------------------------


# declared type => base types judged for Primitive Obsession (- = unknown)
BASE_TYPES = """
u64 => u64
&str => str
&'a mut String => String
Vec<Order> => Order
&[u8] => u8
[u8; 32] => u8
HashMap<String, u32> => String u32
Option<Cow<'_, str>> => str
std::path::PathBuf => PathBuf
impl Into<String> => -
&dyn Fn(i32) -> i32 => -
(u8, u8) => -
"""


def test_base_types():
    adapter = adapter_for("x.rs")
    for line in BASE_TYPES.strip().splitlines():
        type_text, bases = line.rsplit(" => ", 1)
        expected = None if bases == "-" else bases.split()
        assert adapter.base_types(type_text) == expected, type_text


def test_primitive_judgement():
    adapter = adapter_for("x.rs")
    assert adapter.is_primitive("HashMap<String, u32>") is True
    assert adapter.is_primitive("Vec<Order>") is False
    assert adapter.is_primitive("bool") is None


LOW_COHESION = """
struct Mixed { users: Vec<String>, cache: HashMap<u32, u32> }

impl Mixed {
    fn new() -> Self { Mixed { users: vec![], cache: HashMap::new() } }
    fn add_user(&mut self, u: String) { self.users.push(u); }
    fn count_users(&self) -> usize { self.users.len() }
    fn store(&mut self, k: u32) { self.cache.insert(k, k); }
    fn lookup(&self, k: u32) -> Option<&u32> { self.cache.get(&k) }
    fn other(o: &Mixed) -> usize { o.users.len() }
}
"""


def test_member_refs_and_lcom4():
    cls = parse_source(LOW_COHESION.encode(), "x.rs").classes[0]
    adapter = adapter_for("x.rs")
    refs = adapter.member_refs(cls)
    assert refs["Mixed.count_users"] == {"users"}
    assert refs["Mixed.other"] == set()  # `o.users` is another value
    assert lcom4(cls, adapter) == 2


BUILDER = """
impl Chart {
    pub fn title(mut self, t: String) -> Self { self.title = t; self }
    pub fn style(mut self, s: Style) -> Self { self.style = s; self }
    pub fn width(mut self, w: u16) -> Self { self.width = w; self }
    pub fn height(mut self, h: u16) -> Self { self.height = h; self }
    pub fn area(&self) -> u32 { self.width as u32 * self.height as u32 }
}
"""


def test_builder_setters_and_traits_are_not_judged_for_cohesion():
    adapter = adapter_for("x.rs")
    cls = parse_source(BUILDER.encode(), "x.rs").classes[0]
    refs = adapter.member_refs(cls)
    assert refs["Chart.title"] == set() and refs["Chart.area"] == {"width", "height"}
    trait = parse_source(b"trait T { fn a(&self) { self.x(); } }", "x.rs").classes[0]
    assert adapter.member_refs(trait) == {}


# --- scan / config ----------------------------------------------------------------


def test_scan_skips_cargo_build_output(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.rs").write_text("fn main() {}\n")
    out = tmp_path / "target" / "debug" / "build" / "x" / "out"
    out.mkdir(parents=True)
    (out / "gen.rs").write_text("fn generated() {}\n")
    assert [f.path for f in scan(tmp_path, Config()).files] == ["src/main.rs"]
