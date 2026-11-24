"""Kotlin adapter: extraction, golden metrics, Kotlin-specific constructs."""

from pathlib import Path

import pytest
from helpers import check_base_types, findings_of, metric_table

from aihealth.config import Config
from aihealth.lang import adapter_for, is_supported, parse_file, parse_source
from aihealth.lang.kotlin import DEFAULT_TEST_CALLS
from aihealth.metrics import function_metrics
from aihealth.smells import _effective_args, lcom4

FIXTURE = Path(__file__).parent / "fixtures" / "sample.kt"


def metrics(src: str) -> dict:
    module = parse_source(src.encode(), "X.kt")
    assert module.error_count == 0
    adapter = adapter_for("X.kt")
    return {f.qualname: function_metrics(f, adapter) for f in module.all_functions()}


def triples(src: str) -> dict:
    """qualname -> (cyclomatic, cognitive, max nesting)."""
    return {q: (m.cyclomatic, m.cognitive, m.max_nesting) for q, m in metrics(src).items()}


def units(src: str) -> list[tuple[str, str]]:
    module = parse_source(src.encode(), "X.kt")
    assert module.error_count == 0
    return [(f.qualname, f.kind) for f in module.all_functions()]


def test_registered_for_kotlin_files():
    assert is_supported("app/src/main/kotlin/App.kt")
    assert adapter_for("App.kt").language == "kotlin"
    assert not is_supported("build.gradle.kts")  # Gradle scripts: not scored yet


# --- extraction -----------------------------------------------------------------------

FIXTURE_UNITS = """
Status.OPEN.next method
Status.CLOSED.next method
Priced.discounted method
OrderRepository.size getter
OrderRepository.name= setter
OrderRepository.init constructor
OrderRepository.OrderRepository constructor
OrderRepository.Companion.create method
OrderRepository.find method
OrderRepository.classify method
OrderRepository.load method
OrderRepository.fact method
String.shout function
onOrder function
ticker.Runnable.run method
logged function
template function
OrderScreen function
OrderSpec.test("adds up") test
"""


def test_fixture_units_and_kinds():
    module = parse_file(FIXTURE)
    assert module.error_count == 0  # Kotlin 2.x: guards, context parameters, `$$"..."`
    expected = [tuple(line.rsplit(" ", 1)) for line in FIXTURE_UNITS.strip().splitlines()]
    assert [(f.qualname, f.kind) for f in module.all_functions()] == expected


FIXTURE_CLASSES = """
Shape interface
Shape.Circle class
Shape.Empty class
Status class
OrderId class
Priced interface
OrderRepository class
OrderRepository.Companion class
OrderSpec class
"""


def test_fixture_classes():
    classes = [f"{c.name} {c.kind}" for c in parse_file(FIXTURE).classes]
    assert classes == FIXTURE_CLASSES.strip().splitlines()


def test_parameters_defaults_and_varargs():
    fns = {f.qualname: f for f in parse_file(FIXTURE).all_functions()}
    find = fns["OrderRepository.find"]
    assert [(p.name, p.type_text) for p in find.params] == [
        ("id", "String?"),
        ("tags", "String"),
        ("strict", "Boolean"),
    ]
    assert [p.variadic for p in find.params] == [False, True, False]
    assert [p.optional for p in find.params] == [False, False, True]
    screen = fns["OrderScreen"]
    assert [p.type_text for p in screen.params] == ["UiState", "Modifier", "() -> Unit"]


def test_parameters_with_defaults_count_half():
    """Callers usually skip a defaulted parameter or pass it by name, as
    Compose's `modifier: Modifier = Modifier` convention does."""
    src = """
fun six(a: Int, b: Int, c: Int, d: Int, e: Int, f: Int) = a
fun composable(a: Int, b: Int, c: Int, modifier: Modifier = Modifier, enabled: Boolean = true) = a
"""
    fns = {f.qualname: f for f in parse_source(src.encode(), "X.kt").all_functions()}
    assert _effective_args(fns["six"]) == 6
    assert _effective_args(fns["composable"]) == 4  # 3 + 2 x 0.5: no finding
    assert findings_of(src, "X.kt", "excess_arguments")[0].symbol == "six"


def test_extension_local_and_named_function_values():
    src = """
fun <T> List<T>.second(): T? = getOrNull(1)
val Order.total: Double get() = items.sumOf { it.price }
class Cart {
    fun checkout() {
        fun validate(x: Int) = x > 0
        val fmt = fun(x: Int): String { return "$x" }
        val log: (String) -> Unit = { println(it) }
    }
}
"""
    assert units(src) == [
        ("List.second", "function"),
        ("Order.total", "getter"),
        ("Cart.checkout", "method"),
        ("Cart.checkout.validate", "function"),
        ("Cart.checkout.fmt", "function"),
        ("Cart.checkout.log", "function"),
    ]


def test_objects_companions_and_object_expressions():
    src = """
object Registry {
    fun add(x: Int) { println(x) }
}
class Repo(val listener: Listener = object : Listener { override fun on() {} }) {
    companion object Factory { fun create() = Repo() }
    fun start() {
        thread.start(object : Runnable { override fun run() { work() } })
    }
}
"""
    assert units(src) == [
        ("Registry.add", "method"),
        ("Repo.Listener.on", "method"),
        ("Repo.Factory.create", "method"),
        ("Repo.start", "method"),
        ("Repo.start.Runnable.run", "method"),
    ]


def test_getters_and_setters_on_their_own_lines():
    """A getter or setter on its own line is a sibling of its property in
    the syntax tree; one on the same line is its child."""
    src = """
class Box {
    var label: String = ""
        // trimmed on write
        set(value) { field = value.trim() }
    val upper: String
        get() = label.uppercase()
    val inline get() = 1
    var hidden = 0
        private set
}
"""
    assert units(src) == [
        ("Box.label=", "setter"),
        ("Box.upper", "getter"),
        ("Box.inline", "getter"),
    ]


def test_kotest_closures_are_units():
    src = """
class CartSpec : DescribeSpec({
    describe("a cart") {
        it("starts empty") { Cart().size shouldBe 0 }
        it("adds") { if (ok) add() }
    }
})
class CartTest {
    @Test fun addsItems() { assertEquals(1, Cart().apply { add() }.size) }
}
"""
    assert units(src) == [
        ('CartSpec.describe("a cart")', "test"),
        ('CartSpec.describe("a cart").it("starts empty")', "test"),
        ('CartSpec.describe("a cart").it("adds")', "test"),
        ("CartTest.addsItems", "method"),
    ]


def test_test_closure_calls_are_configurable():
    config = Config()
    config.data["kotlin"]["test_closure_calls"] = ["check"]
    adapter = adapter_for("X.kt", config)
    src = b'class S : Spec({ check("x") { run() }; test("y") { run() } })'
    module = adapter.parse(src, "X.kt")
    assert [f.qualname for f in module.all_functions()] == ['S.check("x")']


# --- golden metrics ---------------------------------------------------------------------

SONAR = """
fun sumOfPrimes(max: Int): Int {
    var total = 0
    outer@ for (i in 1..max) {
        for (j in 2 until i) {
            if (i % j == 0) {
                continue@outer
            }
        }
        total += i
    }
    return total
}

fun getWords(number: Int): String {
    when (number) {
        1 -> return "one"
        2 -> return "a couple"
        3 -> return "a few"
        else -> return "lots"
    }
}

fun seq(a: Boolean, b: Boolean, c: Boolean, d: Boolean, e: Boolean, f: Boolean) =
    a && b && c || d || e && f
"""


def test_sonar_examples():
    assert triples(SONAR) == metric_table("""
        sumOfPrimes 4 7 3
        getWords 4 1 1
        seq 6 3 0
    """)


def test_fixture_metrics():
    module = parse_file(FIXTURE)
    adapter = adapter_for(FIXTURE)
    got = {f.qualname: function_metrics(f, adapter) for f in module.all_functions()}
    triple = {q: (m.cyclomatic, m.cognitive, m.max_nesting) for q, m in got.items()}
    assert triple["OrderRepository.find"] == (10, 15, 3)
    assert triple["OrderRepository.classify"] == (4, 1, 1)  # guard `if` is part of its branch
    assert triple["OrderRepository.fact"] == (2, 3, 1)  # if +1, else +1, recursion +1
    assert got["OrderScreen"].widget_depth == 5  # Column > LazyColumn > Row > Button > Text


def test_when_branches_and_else_if():
    src = """
fun elseIf(a: Int): Int {
    if (a > 1) { return 1 } else if (a > 0) { return 2 } else { return 3 }
}

fun nestedElse(a: Int) {
    if (a > 1) { f() } else { if (a > 0) g() }
}

fun subjectless(a: Int, b: Int) = when {
    a > 0 && b > 0 -> 1
    a < 0 || b < 0 -> 2
    else -> 3
}

fun kinds(x: Any) = when (x) {
    0, 1 -> "small"
    in 2..9 -> "digit"
    is String -> "text"
    !is Number -> "other"
    else -> "big"
}

fun asExpression(a: Int) = if (a > 0) "pos" else "neg"
"""
    assert triples(src) == metric_table("""
        elseIf 3 3 1
        nestedElse 3 4 2        # the inner if sits in the else block: +2
        subjectless 5 3 1       # 2 branches, 1 operator each; when +1, one per condition
        kinds 5 1 1             # 4 non-else branches; `0, 1` is one branch
        asExpression 2 2 1
    """)


def test_branch_conditions_in_when_can_be_complex():
    src = """
fun pick(a: Int, b: Int, c: Int) = when {
    a > 0 && b > 0 && c > 0 -> 1
    else -> 0
}
fun guard(s: Shape) = when (s) {
    is Circle if s.r > 1 && s.r < 5 || s.big -> 1
    else -> 0
}
"""
    found = findings_of(src, "X.kt", "complex_conditional")
    assert [f.symbol for f in found] == ["pick", "guard"]


def test_null_safety_operators_add_nothing():
    """`?.`, `?:` and `!!` are not branches (as `??` in TypeScript and
    Swift); `x ?: return` early exits stay flat."""
    src = """
fun name(user: User?): String {
    val n = user?.profile?.name ?: return "anonymous"
    return n.trim()!!
}
"""
    assert triples(src) == {"name": (1, 0, 0)}


def test_loops_catch_and_labels():
    src = """
fun loops(xs: List<Int>): Int {
    var n = 0
    do { n++ } while (n < 3)
    while (n > 0) { n-- }
    for (x in xs) { n += x }
    return n
}

fun handlers() {
    try { work() } catch (e: IllegalStateException) { recover(e) } catch (e: Exception) { log(e) } finally { done() }
}

fun jumps(grid: List<List<Int>>) {
    outer@ for (row in grid) {
        for (v in row) {
            if (v < 0) break@outer
            if (v == 0) continue
        }
    }
    grid.forEach { if (it.isEmpty()) return@forEach }
}
"""
    assert triples(src) == metric_table("""
        loops 4 3 1
        handlers 3 2 1          # each catch +1
        jumps 6 12 3            # for +1, for +2, if +3 and +3, break@outer +1; lambda if +2
    """)


def test_scope_functions_and_lowercase_lambdas_keep_closure_nesting():
    """`x?.let { }` is a null check: its lambda keeps the closure penalty, as
    do `map`, `forEach` and `launch` (measured: scope functions are 0.7-3.5 %
    of cognitive points, see cards/code-health-model.md)."""
    src = """
fun scoped(user: User?) {
    user?.let { if (it.active) greet(it) }
    items.forEach { if (it > 0) use(it) }
    scope.launch { if (ready) start() }
}
"""
    assert triples(src) == {"scoped": (4, 6, 1)}  # each if +2


def test_compose_lambdas_add_no_nesting():
    """A lambda passed to a capitalised call is layout (Compose content,
    slots, `onClick =`), like SwiftUI view builders. Effects run code and
    keep the closure penalty."""
    src = """
@Composable
fun Screen(state: State) {
    Column {
        Card(onClick = { if (state.ok) go() }) {
            Row { if (state.loading) Spinner() else Text(state.title) }
        }
    }
    LaunchedEffect(state.id) { if (state.stale) refresh() }
}
"""
    m = metrics(src)["Screen"]
    assert (m.cyclomatic, m.cognitive, m.max_nesting) == (
        4,
        5,
        1,
    )  # ifs +1, +1 (+1 else), effect +2
    assert m.widget_depth == 4  # Column > Card > Row > Text; the effect is not layout


# --- types and cohesion -------------------------------------------------------------------


def test_base_types():
    check_base_types(
        "X.kt",
        """
Int => Int
String? => String
List<Order> => Order
Map<String, Int?> => String Int
kotlin.Long => Long
MutableList<out Shape> => Shape
List<*> => -
Array<String> => String
IntArray => IntArray
@Composable () -> Unit => -
suspend (Int) -> Unit => -
""",
    )


def test_primitive_obsession_applies():
    adapter = adapter_for("X.kt")
    assert adapter.is_primitive("String?") is True
    assert adapter.is_primitive("List<Int>") is True
    assert adapter.is_primitive("List<Order>") is False
    assert adapter.is_primitive("Boolean") is None  # flags are neutral, as in every language
    assert adapter.is_primitive("() -> Unit") is None


def cohesion(src: str) -> list[tuple[dict, int | None]]:
    """Per class: (member refs of methods that touch members, LCOM4)."""
    adapter = adapter_for("X.kt")
    out = []
    for cls in parse_source(src.encode(), "X.kt").classes:
        refs = {q.rsplit(".", 1)[-1]: r for q, r in adapter.member_refs(cls).items() if r}
        out.append((refs, lcom4(cls, adapter)))
    return out


def test_cohesion_by_bare_and_this_properties():
    """Constructor `val`s and body properties are state; `other.a` is
    another object's, a companion's members are not instance state."""
    (refs, groups), _companion, (api_refs, _) = cohesion("""
class S(private val a: Int, plain: Int) {
    private var c = 0
    private var d = 0
    fun a1() = a * 2 + plain()
    fun a2() = this.a + a1()
    fun c1() { c += 1 }
    fun d1(other: S) = d + other.a
    companion object { val shared = 1 }
}
interface Api { fun x() = y(); fun y(): Int }
""")
    assert refs == {"a1": {"a"}, "a2": {"a", "a1"}, "c1": {"c"}, "d1": {"d"}}
    assert groups == 3  # {a1, a2}, {c1}, {d1}
    assert api_refs == {}  # interfaces have no state


def test_accessors_are_not_judged_for_cohesion():
    """Property accessors and functions that only return or store one
    property (`fun type() = type`) touch one field by design."""
    [(refs, groups)] = cohesion("""
class MediaType(val type: String, val subtype: String, private val params: Array<String>) {
    private var cached: String? = null
    val full: String get() = "$type/$subtype"
    fun type() = type
    fun subtype(): String { return this.subtype }
    fun remember(value: String) { cached = value }
    fun charset() = params.firstOrNull() ?: subtype
}
""")
    assert refs == {"charset": {"params", "subtype"}}
    assert groups is None  # too few judged methods


# --- review regressions ------------------------------------------------------------------


def test_annotated_and_suspend_parameter_types():
    src = "fun f(content: @Composable () -> Unit, load: suspend () -> Unit, n: @Ann Int) {}"
    (fn,) = parse_source(src.encode(), "X.kt").all_functions()
    types = [p.type_text for p in fn.params]
    assert types == ["@Composable () -> Unit", "suspend () -> Unit", "@Ann Int"]
    adapter = adapter_for("X.kt")
    assert [adapter.is_primitive(t) for t in types] == [None, None, True]


def test_lambda_parameters_have_names_and_types():
    src = "val f = { a: Int, (k, v): Pair<String, Int> -> a }"
    (fn,) = parse_source(src.encode(), "X.kt").all_functions()
    assert [(p.name, p.type_text) for p in fn.params] == [("a", "Int"), ("(k, v)", None)]


def test_lambdas_bound_to_class_properties_are_methods():
    src = "class C { val onClick = { go() }; val f = fun() { go() } }"
    assert units(src) == [("C.f", "method"), ("C.onClick", "method")]  # same line: by name


def test_layout_rules_apply_only_in_composable_functions():
    """Outside `@Composable` functions a capitalised call is a constructor
    or SAM conversion (`Comparator { }`): its lambda keeps the closure
    penalty and it builds no widget tree."""
    src = """
fun sorter() = Comparator<Int> { a, b -> if (a > b) 1 else -1 }
fun nested() = A(B(C(D(E(F(G(H(I(1)))))))))
"""
    got = metrics(src)
    assert got["sorter"].cognitive == 3  # if +2 (in a lambda), else +1
    assert got["nested"].widget_depth == 0


@pytest.mark.parametrize("effect", ["LaunchedEffect", "DisposableEffect", "SideEffect"])
def test_compose_effects_keep_closure_nesting(effect):
    src = f"@Composable fun S(k: Int) {{ {effect}(k) {{ if (k > 0) run() }} }}"
    assert metrics(src)["S"].cognitive == 2


@pytest.mark.parametrize("call", sorted(DEFAULT_TEST_CALLS))
def test_default_test_closure_calls(call):
    src = f'class S : Spec({{ {call}("x") {{ run() }} }})'
    assert units(src) == [(f'S.{call}("x")', "test")]


def test_string_spec_styles_are_not_units():
    """Kotest's `"name" { }` and infix styles are not calls by name: the
    closures stay part of the spec's body (documented gap)."""
    assert units('class S : StringSpec({ "adds" { check() } })') == []


def test_accessor_detection_needs_a_property():
    """`fun id(p: Int) = p` returns a parameter, not a property: judged.
    `this.x = v` stores a property: an accessor."""
    [(refs, _)] = cohesion("""
class C(var x: Int, val y: Int) {
    fun setX(v: Int) { this.x = v }
    fun id(p: Int) = p
    fun sum() = x + y
}
""")
    assert refs == {"sum": {"x", "y"}}
