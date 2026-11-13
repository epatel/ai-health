"""Go adapter: extraction, golden metrics, Go-specific constructs."""

from pathlib import Path

from helpers import check_base_types

from aihealth.config import Config, load_config
from aihealth.lang import adapter_for, is_supported, parse_file, parse_source
from aihealth.lang.base import is_generated
from aihealth.metrics import function_metrics
from aihealth.smells import lcom4

FIXTURE = Path(__file__).parent / "fixtures" / "sample.go"


def go_config(**go) -> Config:
    config = Config()
    config.data["go"].update(go)
    return config


def metrics(src: str, config: Config | None = None) -> dict:
    module = parse_source(src.encode(), "x.go", config)
    assert module.error_count == 0
    adapter = adapter_for("x.go", config)
    return {f.qualname: function_metrics(f, adapter) for f in module.all_functions()}


def triples(src: str, config: Config | None = None) -> dict:
    """qualname -> (cyclomatic, cognitive, max nesting)."""
    return {q: (m.cyclomatic, m.cognitive, m.max_nesting) for q, m in metrics(src, config).items()}


def test_registered_for_go_files():
    assert is_supported("cmd/main.go")
    assert adapter_for("x.go").language == "go"


# --- extraction -----------------------------------------------------------------------

FIXTURE_UNITS = """
Stack.Push method
Stack.All method
Sum function
describe function
classify function
worker function
loadAll function
read function
fact function
"""


def test_fixture_units_and_kinds():
    expected = [tuple(line.split()) for line in FIXTURE_UNITS.strip().splitlines()]
    fns = parse_file(FIXTURE).all_functions()
    assert [(f.qualname, f.kind) for f in fns] == expected


def test_methods_are_grouped_by_receiver_type():
    src = """package p
type T struct{ a, b int }
func (t *T) A() int { return t.a }
func helper() {}
func (t T) B() int { return t.b }
func (Other) C() {}
func (s *Set[K]) D() {}
"""
    module = parse_source(src.encode(), "x.go")
    classes = {
        c.name: (c.start_line, c.end_line, [m.name for m in c.methods]) for c in module.classes
    }
    # T spans its type declaration and both methods; receivers without a
    # type declaration in the file start at their first method
    assert classes == {
        "T": (2, 5, ["A", "B"]),
        "Other": (6, 6, ["C"]),
        "Set": (7, 7, ["D"]),
    }


def test_parameters_one_per_name():
    src = "package p\nfunc f(a, b int, s string, rest ...float64) {}\nfunc g(int, string) {}\n"
    fns = {f.name: f for f in parse_source(src.encode(), "x.go").all_functions()}
    params = [(p.name, p.type_text, p.variadic) for p in fns["f"].params]
    assert params == [
        ("a", "int", False),
        ("b", "int", False),
        ("s", "string", False),
        ("rest", "float64", True),
    ]
    assert [(p.name, p.type_text) for p in fns["g"].params] == [("_", "int"), ("_", "string")]


def test_named_func_values_and_subtests_are_units():
    src = """package p
var handler = func(w int) { use(w) }
func TestX(t *testing.T) {
    check := func(x int) bool { return x > 0 }
    t.Run("positive", func(t *testing.T) {
        if !check(1) { t.Fatal("no") }
    })
    for _, tc := range cases {
        t.Run(tc.name, func(t *testing.T) { use(tc) })
    }
    defer func() { recover() }()
}
"""
    module = parse_source(src.encode(), "x_test.go")
    units = {f.qualname: f.kind for f in module.all_functions()}
    assert units == {
        "handler": "function",
        "TestX": "function",
        "TestX.check": "function",
        "TestX.positive": "test",
        "TestX.Run": "test",  # the subtest name is not a literal
    }
    m = metrics(src)
    # the subtest's if is its own (TestX keeps only its loop)
    assert m["TestX"].cyclomatic == 2
    assert m["TestX.positive"].cyclomatic == 2


def test_test_closure_calls_are_configurable():
    src = 'package p\nfunc TestX(t *testing.T) { t.Run("a", func(t *testing.T) {}) }\n'
    module = parse_source(src.encode(), "x.go", go_config(test_closure_calls=[]))
    assert [f.qualname for f in module.all_functions()] == ["TestX"]


# --- golden metrics (SonarSource cognitive-complexity examples) ------------------------

SONAR = """package p

func sumOfPrimes(max int) int {
	total := 0
OUT:
	for i := 1; i <= max; i++ {
		for j := 2; j < i; j++ {
			if i%j == 0 {
				continue OUT
			}
		}
		total += i
	}
	return total
}

func getWords(n int) string {
	switch n {
	case 1:
		return "one"
	case 2:
		return "a couple"
	case 3:
		return "a few"
	default:
		return "lots"
	}
}

func seq(a, b, c, d, e, f bool) bool {
	return a && b && c || d || e && f
}
"""


def test_sonar_examples():
    assert triples(SONAR) == {
        "sumOfPrimes": (4, 7, 3),
        "getWords": (4, 1, 1),
        "seq": (6, 3, 0),
    }


def test_go_constructs():
    src = """package p

func elseIf(a int) int {
	if a > 1 {
		return 1
	} else if a > 0 {
		return 2
	} else {
		return 3
	}
}

func kinds(v any) string {
	switch x := v.(type) {
	case int, int64:
		return "int"
	case string:
		return x
	}
	return ""
}

func pump(in <-chan int, quit chan bool) {
	for {
		select {
		case v := <-in:
			use(v)
		case <-quit:
			return
		default:
		}
	}
}

func spawn(xs []int) {
	go func() {
		for range xs {
		}
	}()
}

func jump(n int) {
loop:
	for i := 0; i < n; i++ {
		if i > 3 {
			break loop
		}
		goto end
	}
end:
}

func cond(xs []int, ok bool) {
	for i := 0; i < len(xs) && ok; i++ {
	}
}
"""
    assert triples(src) == {
        "elseIf": (3, 3, 1),
        "kinds": (3, 1, 1),  # a case listing two types is one path
        "pump": (4, 3, 2),  # for +1, select +2, two non-default cases
        "spawn": (2, 2, 1),  # the loop sits in a closure (+1 nesting)
        "jump": (3, 5, 2),  # for +1, if +2, `break loop` +1, goto +1
        "cond": (3, 2, 1),
    }
    bools = metrics(src)["cond"].bool_exprs
    assert [b.is_condition for b in bools] == [True]  # a for_clause condition


def test_recursion_through_the_receiver_only():
    src = """package p
func fact(n int) int { if n <= 1 { return 1 }; return n * fact(n-1) }
func (t *Tree) Walk(n int) { t.Walk(n - 1) }
func (w *Wrapper) Write(p []byte) { w.inner.Write(p); other.Write(p) }
"""
    m = triples(src)
    assert m["fact"] == (2, 2, 1)
    assert m["Tree.Walk"] == (1, 1, 0)
    assert m["Wrapper.Write"] == (1, 0, 0)  # delegating to another writer


# --- error checks ---------------------------------------------------------------------

ERRORS = """package p

func load(p string) ([]byte, error) {
	data, err := read(p)
	if err != nil {
		return nil, fmt.Errorf("load: %w", err)
	}
	if err := check(data); err != nil {
		return nil, err
	}
	if parseErr != nil {
		return nil, parseErr
	}
	return data, nil
}

func handled(p string) error {
	if err := run(p); err != nil {
		log.Print(err)
		return err
	}
	if err != nil {
		return err
	} else {
		cleanup()
	}
	if err == nil {
		return nil
	}
	return nil
}
"""


def test_error_propagation_adds_no_complexity():
    """`if err != nil { return .. }` is Go's spelled-out `?`: no branch."""
    m = triples(ERRORS)
    assert m["load"] == (1, 0, 0)
    # handled, logged, with an else, or `== nil`: real branches
    assert m["handled"] == (4, 4, 1)


def test_error_checks_counted_on_request():
    m = triples(ERRORS, go_config(count_error_checks=True))
    assert m["load"] == (4, 3, 1)


# --- types and cohesion ---------------------------------------------------------------


def test_base_types():
    check_base_types(
        "x.go",
        """
int => int
*string => string
[]Order => Order
[4]byte => byte
map[string]int => string int
map[string]*pkg.User => string User
*pkg.Config => Config
...string => string
<-chan Event => Event
chan<- int => int
Set[Key] => Key
func(int) error => -
interface{ Read() } => -
any => -
struct{} => -
""",
    )


def test_primitive_obsession_applies():
    adapter = adapter_for("x.go")
    assert adapter.is_primitive("[]string") is True
    assert adapter.is_primitive("context.Context") is False
    assert adapter.is_primitive("bool") is None


def test_cohesion_by_receiver_fields():
    src = """package p
type S struct{ a, b, c, d int }
func (s *S) A() int { return s.a }
func (s *S) A2() int { return s.a + s.A() }
func (x *S) C() int { return x.c }
func (x *S) D() int { return x.d }
func (S) E() int { return 1 }
"""
    module = parse_source(src.encode(), "x.go")
    cls = module.classes[0]
    refs = adapter_for("x.go").member_refs(cls)
    assert refs["S.A2"] == {"a", "A"}
    assert refs["S.E"] == set()
    assert lcom4(cls, adapter_for("x.go")) == 3  # {A, A2}, {C}, {D}


def test_generated_files_are_not_scored():
    src = b"// Code generated by protoc-gen-go. DO NOT EDIT.\npackage p\nfunc f() {}\n"
    assert is_generated(src)


def test_go_section_in_config_file(tmp_path):
    (tmp_path / ".aihealth.toml").write_text(
        '[go]\ncount_error_checks = true\ntest_closure_calls = ["Run", "Describe"]\n'
    )
    adapter = adapter_for("x.go", load_config(tmp_path))
    assert adapter.count_error_checks is True
    assert adapter.test_calls == frozenset({"Run", "Describe"})
