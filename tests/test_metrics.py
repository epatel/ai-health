"""Golden tests for function metrics.

Cognitive-complexity references come from the SonarSource white paper
("Cognitive Complexity", G. Ann Campbell): sumOfPrimes = 7, getWords = 1,
`a && b && c || d || e && f` = +3 for the boolean sequences.
"""

from pathlib import Path

import pytest

from aihealth.lang import adapter_for, parse_file, parse_source
from aihealth.metrics import function_metrics

FIXTURES = Path(__file__).parent / "fixtures"


def metrics(src: str, path: str) -> dict:
    module = parse_source(src.encode(), path)
    assert module.error_count == 0
    adapter = adapter_for(path)
    return {f.qualname: function_metrics(f, adapter) for f in module.all_functions()}


DART = """
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
    case 1: return "one";
    case 2: return "a couple";
    case 3: return "a few";
    default: return "lots";
  }
}
bool seq(a, b, c, d, e, f) {
  if (a && b && c || d || e && f) { return true; }
  return false;
}
int fact(int n) => n <= 1 ? 1 : n * fact(n - 1);
void elseIf(int a) { if (a > 1) { } else if (a < 0) { } else { } }
void handlers() { try { g(); } on FormatException { } catch (e) { if (e) {} } }
String label(int a) => switch (a) { 1 => 'one', 2 => 'two', _ => 'many' };
void closures(List<int> xs) {
  xs.forEach((x) {
    if (x > 0) { print(x); }
  });
}
void doLoop(int a) { do { a--; } while (a > 0 && a != 5); }
"""

PY = """
def sum_of_primes(mx):
    total = 0
    for i in range(1, mx + 1):
        for j in range(2, i):
            if i % j == 0:
                break
        total += i
    return total

def get_words(n):
    match n:
        case 1:
            return "one"
        case 2:
            return "a couple"
        case _:
            return "lots"

def seq(a, b, c, d, e, f):
    if a and b and c or d or e and f:
        return True

def fact(n):
    return 1 if n <= 1 else n * fact(n - 1)

def else_if(a):
    if a > 1:
        pass
    elif a < 0:
        pass
    else:
        pass

def handlers():
    try:
        g()
    except ValueError:
        if x:
            pass
    except Exception:
        pass
    finally:
        pass

def nested_def(xs):
    def helper(x):
        if x:
            if x > 1:
                return x
    return [helper(x) for x in xs if x]

def with_lambda(xs):
    return sorted(xs, key=lambda x: 1 if x else 0)
"""

# (qualname, cyclomatic, cognitive, max_nesting)
DART_EXPECTED = [
    ("sumOfPrimes", 4, 7, 3),
    ("getWords", 4, 1, 1),
    ("seq", 7, 4, 1),
    ("fact", 2, 2, 0),
    ("elseIf", 3, 3, 1),
    ("handlers", 4, 4, 2),
    ("label", 3, 1, 1),
    ("closures", 2, 2, 1),  # closure raises cognitive nesting, not depth
    ("doLoop", 3, 2, 1),
]

PY_EXPECTED = [
    ("sum_of_primes", 4, 6, 3),
    ("get_words", 3, 1, 1),
    ("seq", 7, 4, 1),
    ("fact", 2, 2, 0),
    ("else_if", 3, 3, 1),
    ("handlers", 4, 4, 2),
    ("nested_def", 3, 0, 0),  # helper is scored separately; comprehension for+if
    ("nested_def.helper", 3, 3, 2),
    ("with_lambda", 2, 2, 0),  # ternary inside lambda: +1 +1 nesting
]


@pytest.mark.parametrize(("qualname", "cyc", "cog", "nest"), DART_EXPECTED)
def test_dart_complexity(qualname, cyc, cog, nest):
    m = metrics(DART, "x.dart")[qualname]
    assert (m.cyclomatic, m.cognitive, m.max_nesting) == (cyc, cog, nest)


@pytest.mark.parametrize(("qualname", "cyc", "cog", "nest"), PY_EXPECTED)
def test_python_complexity(qualname, cyc, cog, nest):
    m = metrics(PY, "x.py")[qualname]
    assert (m.cyclomatic, m.cognitive, m.max_nesting) == (cyc, cog, nest)


def test_bool_expressions_recorded():
    for src, path, line in ((DART, "x.dart", 23), (PY, "x.py", 21)):
        m = metrics(src, path)["seq"]
        assert [(b.line, b.operators) for b in m.bool_exprs] == [(line, 5)]
        assert m.max_condition_ops == 5


def test_dart_precedence_quirk_does_not_matter():
    # grammar nests `a && b || c` oddly; operator order is what counts
    m = metrics("bool f(a, b, c) => a && b || c;", "x.dart")["f"]
    assert m.cognitive == 2 and m.cyclomatic == 3


BUMPY = """
def bumpy(xs):
    for x in xs:
        if x:
            if x > 1:
                pass
        if not x:
            while x:
                pass

def flat(xs):
    if xs:
        pass
    for x in xs:
        if x:
            pass
    return xs

def one_bump(x):
    if x:
        for y in x:
            pass
    return x
"""


def test_bumpy_road():
    m = metrics(BUMPY, "x.py")
    # single loop is looked through: two separate nested chunks inside it
    assert [(b.start_line, b.end_line, b.depth) for b in m["bumpy"].bumps] == [
        (4, 6, 2),
        (7, 9, 2),
    ]
    assert m["flat"].bumps[0].start_line == 14 and len(m["flat"].bumps) == 1
    assert len(m["one_bump"].bumps) == 1


def test_dart_bumps_through_labeled_loop():
    src = """
void f(List<int> xs) {
  outer:
  for (final x in xs) {
    if (x > 0) { if (x > 1) {} }
    if (x < 0) { while (x < 0) {} }
  }
}
"""
    assert len(metrics(src, "x.dart")["f"].bumps) == 2


def test_args_and_named_args():
    m = metrics(DART, "x.dart")["seq"]
    assert (m.args, m.named_args) == (6, 0)
    m = metrics("void f(int a, {int? b, required String c}) {}", "x.dart")["f"]
    assert (m.args, m.named_args) == (3, 2)


def test_flutter_widget_depth():
    module = parse_file(FIXTURES / "flutter_widget.dart")
    adapter = adapter_for("x.dart")
    by_name = {f.qualname: function_metrics(f, adapter) for f in module.all_functions()}
    # Scaffold > Center > Column > Padding > Text
    assert by_name["_CounterPageState.build"].widget_depth == 5
    assert by_name["Counter.bump"].widget_depth == 0
    # lowercase calls and method calls on instances are not widgets
    m = metrics("void f(x) { x.add(foo(bar(1))); }", "x.dart")["f"]
    assert m.widget_depth == 0
    m = metrics("Widget f() => Padding(padding: EdgeInsets.all(8), child: _Box());", "x.dart")
    assert m["f"].widget_depth == 2


def test_python_has_no_widget_depth():
    assert all(m.widget_depth == 0 for m in metrics(PY, "x.py").values())


def test_nesting_line_points_at_deepest_construct():
    m = metrics(PY, "x.py")["sum_of_primes"]
    assert m.nesting_line == 6  # `if i % j == 0`


def test_deep_expression_does_not_recurse():
    # 5000-term chain would blow Python's recursion limit if walked recursively
    expr = " + ".join("x" for _ in range(5000))
    m = metrics(f"def f(x):\n    return {expr}\n", "x.py")["f"]
    assert m.cyclomatic == 1
