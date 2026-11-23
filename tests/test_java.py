"""Java adapter: extraction, golden metrics, Java-specific constructs."""

from pathlib import Path

from helpers import check_base_types, metric_table

from aihealth.lang import adapter_for, is_supported, parse_file, parse_source
from aihealth.lang.java import prepare
from aihealth.metrics import function_metrics
from aihealth.smells import lcom4

FIXTURE = Path(__file__).parent / "fixtures" / "sample.java"


def metrics(src: str) -> dict:
    module = parse_source(src.encode(), "X.java")
    assert module.error_count == 0
    adapter = adapter_for("X.java")
    return {f.qualname: function_metrics(f, adapter) for f in module.all_functions()}


def triples(src: str) -> dict:
    """qualname -> (cyclomatic, cognitive, max nesting)."""
    return {q: (m.cyclomatic, m.cognitive, m.max_nesting) for q, m in metrics(src).items()}


def test_registered_for_java_files():
    assert is_supported("src/main/java/App.java")
    assert adapter_for("App.java").language == "java"


# --- extraction -----------------------------------------------------------------------

FIXTURE_UNITS = """
OrderService.static method
OrderService.OrderService constructor
OrderService.find method
OrderService.total method
OrderService.describe method
OrderService.legacy method
OrderService.process method
OrderService.process.r function
OrderService.process.twice function
OrderService.process.Comparator.compare method
OrderService.Order.Order constructor
OrderService.Order.doubled method
OrderService.Status.OPEN.label method
OrderService.Status.label method
OrderService.Priced.discounted method
OrderService.max method
"""


def test_fixture_units_and_kinds():
    module = parse_file(FIXTURE)
    assert module.error_count == 0
    expected = [tuple(line.split()) for line in FIXTURE_UNITS.strip().splitlines()]
    assert [(f.qualname, f.kind) for f in module.all_functions()] == expected


FIXTURE_CLASSES = """
OrderService class
OrderService.Order record
OrderService.Shape interface
OrderService.Circle record
OrderService.Square record
OrderService.Status enum
OrderService.Priced interface
"""


def test_fixture_classes():
    classes = [f"{c.name} {c.kind}" for c in parse_file(FIXTURE).classes]
    assert classes == FIXTURE_CLASSES.strip().splitlines()


def test_parameters():
    fns = {f.qualname: f for f in parse_file(FIXTURE).all_functions()}
    params = [f"{p.name}: {p.type_text}" for p in fns["OrderService.total"].params]
    assert params == ["items: List<Order>", "vip: boolean", "codes: String..."]
    assert [p.variadic for p in fns["OrderService.total"].params] == [False, False, True]
    assert [p.name for p in fns["OrderService.process.twice"].params] == ["x"]


def test_initializers_top_level_methods_and_local_classes():
    src = """
class A {
    { setUp(); }
    static { load(); }
    void run() {
        record Point(int x, int y) { int sum() { return x + y; } }
    }
}
void main() { IO.println("compact source file"); }
"""
    module = parse_source(src.encode(), "A.java")
    assert module.error_count == 0
    units = [(f.qualname, f.kind) for f in module.all_functions()]
    assert units == [
        ("A.init", "method"),
        ("A.static", "method"),
        ("A.run", "method"),
        ("A.run.Point.sum", "method"),
        ("main", "function"),
    ]


# --- golden metrics ---------------------------------------------------------------------

SONAR = """
class Sonar {
    int sumOfPrimes(int max) {
        int total = 0;
        OUT: for (int i = 1; i <= max; ++i) {
            for (int j = 2; j < i; ++j) {
                if (i % j == 0) {
                    continue OUT;
                }
            }
            total += i;
        }
        return total;
    }

    String getWords(int number) {
        switch (number) {
            case 1:
                return "one";
            case 2:
                return "a couple";
            case 3:
                return "a few";
            default:
                return "lots";
        }
    }

    boolean seq(boolean a, boolean b, boolean c, boolean d, boolean e, boolean f) {
        return a && b && c || d || e && f;
    }
}
"""


def test_sonar_examples():
    assert triples(SONAR) == metric_table("""
        Sonar.sumOfPrimes 4 7 3
        Sonar.getWords 4 1 1
        Sonar.seq 6 3 0
    """)


def test_java_constructs():
    src = """
class C {
    int elseIf(int a) {
        if (a > 1) { return 1; } else if (a > 0) { return 2; } else { return 3; }
    }

    String arrows(Object o) {
        return switch (o) {
            case Integer i when i > 0 -> "positive";
            case String s -> s;
            case null, default -> "other";
        };
    }

    void handlers() {
        try (var in = open()) {
            work(in);
        } catch (IllegalStateException | IllegalArgumentException e) {
            recover(e);
        } finally {
            done();
        }
    }

    int loops(int[] xs) {
        int n = 0;
        do { n++; } while (n < 3);
        while (n > 0) { n--; }
        for (int x : xs) { n += x > 0 ? x : -x; }
        return n;
    }

    void callbacks(List<Integer> xs) {
        xs.forEach(x -> { if (x > 0) { use(x); } });
    }

    int fact(int n) {
        return n <= 1 ? 1 : n * this.fact(n - 1);
    }
}
"""
    assert triples(src) == metric_table("""
        C.elseIf 3 3 1
        C.arrows 4 1 1     # switch +1; two non-default arms (`case null, default` is default)
        C.handlers 2 1 1   # catch +1; try and finally add nothing
        C.loops 5 5 1      # do, while, for +1 each, the ternary in the for +2
        C.callbacks 2 2 1  # the if sits in an inline lambda (+1 nesting)
        C.fact 2 2 0       # ternary +1, recursion through `this` +1
    """)


def test_labeled_jumps_and_lambdas_bound_to_names():
    src = """
class L {
    Runnable task = () -> { if (ready) { run(); } };

    void scan(int[][] grid) {
        outer:
        for (int[] row : grid) {
            for (int v : row) {
                if (v < 0) break outer;
                if (v == 0) continue;
            }
        }
        Comparator<String> byLength = (a, b) -> a.length() - b.length();
    }
}
"""
    assert triples(src) == metric_table("""
        L.task 2 1 1
        L.scan 5 10 3        # for +1, for +2, if +3 and +3, `break outer` +1
        L.scan.byLength 1 0 0
    """)


# --- newer syntax the grammar lacks -------------------------------------------------------

NEWER_SYNTAX = """import module java.base;

class Point extends Base {
    Point(int x, Object @Nullable ... rest) {
        if (x < 0) throw new IllegalArgumentException();
        super(x);
    }

    Point() {
        int y = 1;
        this(y);
    }

    void log(String fmt, int @Nullable [] values) {
        String s = \"\"\"
            this(x) and super(y) stay in text blocks
            \"\"\";
    }
}
"""


def test_java_25_syntax_and_annotated_varargs_parse():
    src = NEWER_SYNTAX.encode()
    prepared = prepare(src)
    assert len(prepared) == len(src)
    assert prepared.count(b"\n") == src.count(b"\n")
    assert b"this(x) and super(y)" in prepared
    m = triples(NEWER_SYNTAX)  # asserts no parse errors
    assert m["Point.Point"] == (2, 1, 1)
    fns = {f.qualname: f for f in parse_source(src, "Point.java").all_functions()}
    assert [(p.name, p.variadic) for p in fns["Point.Point"].params] == [
        ("x", False),
        ("rest", True),
    ]


# --- types and cohesion -------------------------------------------------------------------


def test_base_types():
    check_base_types(
        "X.java",
        """
int => int
int[] => int
String... => String
List<Order> => Order
Map<String, Integer> => String Integer
java.util.Date => Date
Map<String, List<User>> => String User
List<? extends Shape> => Shape
@NonNull String => String
Optional<@Nullable Order> => Order
""",
    )


def test_primitive_obsession_applies():
    adapter = adapter_for("X.java")
    assert adapter.is_primitive("String") is True
    assert adapter.is_primitive("List<Integer>") is True
    assert adapter.is_primitive("List<Order>") is False
    assert adapter.is_primitive("boolean") is None


def test_cohesion_by_bare_and_this_fields():
    src = """
class S {
    private int a, b, c, d;
    private static int shared;
    S(int a) { this.a = a; }
    int a1() { return a * 2; }
    int a2() { return this.a + a1(); }
    int c1() { return c + shared; }
    int d1(S other) { return d + other.a; }
}
interface Api { default int x() { return y(); } int y(); }
"""
    module = parse_source(src.encode(), "S.java")
    adapter = adapter_for("S.java")
    cls = module.classes[0]
    refs = adapter.member_refs(cls)
    assert refs["S.a2"] == {"a", "a1"}
    assert refs["S.c1"] == {"c"}  # static fields are not instance state
    assert refs["S.d1"] == {"d"}  # `other.a` is another object's
    assert lcom4(cls, adapter) == 3  # {a1, a2}, {c1}, {d1}
    assert adapter.member_refs(module.classes[1]) == {}  # interfaces have no state


def test_record_components_are_fields():
    src = """
record R(int x, int y) {
    int sx() { return x * 2; }
    int sy() { return -y; }
}
"""
    module = parse_source(src.encode(), "R.java")
    refs = adapter_for("R.java").member_refs(module.classes[0])
    assert refs == {"R.sx": {"x"}, "R.sy": {"y"}}


def test_accessors_are_not_judged_for_cohesion():
    """Getters, setters and fluent setters each touch one field by design:
    a JavaBean would otherwise split into one group per field."""
    src = """
class Bean {
    private String a, b, c, d;
    String getA() { return a; }
    String getB() { return this.b; }
    void setC(String c) { this.c = c; }
    Bean d(String d) { this.d = d; return this; }
    String ab() { return a + b; }
}
"""
    module = parse_source(src.encode(), "Bean.java")
    refs = adapter_for("Bean.java").member_refs(module.classes[0])
    assert {q for q, names in refs.items() if names} == {"Bean.ab"}
    assert lcom4(module.classes[0], adapter_for("Bean.java")) is None
