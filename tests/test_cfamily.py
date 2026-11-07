"""C / C++ / Objective-C adapter: grammar routing, units, golden metrics, cohesion."""

import time
from pathlib import Path

import pytest
from helpers import check_base_types

from aihealth.config import DEFAULT_EXCLUDES
from aihealth.engine import is_excluded
from aihealth.lang import adapter_for, is_supported, parse_file, parse_source
from aihealth.lang.base import is_generated
from aihealth.lang.cfamily import _APPLE_ENUM, header_grammar, prepare
from aihealth.metrics import function_metrics
from aihealth.smells import lcom4

FIXTURES = Path(__file__).parent / "fixtures"


def module_of(src: str, path: str):
    return parse_source(src.encode(), path)


def metrics(src: str, path: str = "x.c") -> dict:
    module = module_of(src, path)
    assert not module.root.has_error
    adapter = adapter_for(path)
    return {f.qualname: function_metrics(f, adapter) for f in module.all_functions()}


def units(src: str, path: str = "x.c") -> list[str]:
    return [f.qualname for f in module_of(src, path).all_functions()]


# --- routing -----------------------------------------------------------------------------


EXTENSIONS = "a.c c, a.cpp cpp, a.cc cpp, a.cxx cpp, a.hpp cpp, a.hh cpp, a.hxx cpp, a.m objc, a.mm objcpp, a.h c"


@pytest.mark.parametrize("entry", EXTENSIONS.split(", "))
def test_registered_and_language_per_extension(entry):
    path, language = entry.split()
    assert is_supported(path)
    assert module_of("int x;\n", path).language == language


# header (`|` = newline) => grammar
HEADERS = """
#import <Foundation/Foundation.h> => objc
@interface Foo : NSObject|@end => objc
@protocol P|@end => objc
namespace a {|int f();|} => cpp
template <typename T>|T id(T x); => cpp
class Foo {|  int x;|}; => cpp
struct Foo : Base {|}; => cpp
struct S {|public:|  int x;|}; => cpp
std::string name(); => cpp
#import "Foo.h"|namespace a {|} => objcpp
#include <stdint.h>|struct point { int x; };|int f(void); => c
"""


@pytest.mark.parametrize("line", HEADERS.strip().splitlines())
def test_header_router(line):
    header, grammar = line.split(" => ")
    assert header_grammar(header.replace("|", "\n").encode()) == grammar


# --- source preparation ------------------------------------------------------------------


def test_conditionals_are_blanked_with_the_same_offsets():
    src = b"#if A\nint f(void) {\n#else\nint g(void) {\n#endif\n  return 1;\n}\n"
    prepared = prepare(src)
    assert len(prepared) == len(src)
    assert prepared.count(b"\n") == src.count(b"\n")
    assert b"#" not in prepared


def test_a_branch_that_splits_a_statement_does_not_lose_the_file():
    """Unblanked, the outer `#if` becomes one ERROR and both functions are lost."""
    src = """#if FEATURE
int f(int x) {
    int y = x
#if A
        + 1;
#else
        - 1;
#endif
    return y;
}

int g(int x) {
    return x;
}
#endif
"""
    assert units(src) == ["f", "g"]


def test_apple_enum_macros_parse():
    src = """typedef NS_ENUM(NSInteger, Kind) {
    KindA,
    KindB,
};
NS_ASSUME_NONNULL_BEGIN
@implementation Foo
- (Kind)kind {
    return KindA;
}
@end
NS_ASSUME_NONNULL_END
"""
    assert b"enum Kind" in prepare(src.encode())
    module = module_of(src, "x.m")
    assert not module.root.has_error
    assert units(src, "x.m") == ["Foo.kind"]


def test_bare_macro_lines_do_not_break_the_class_around_them():
    src = """namespace absl {
ABSL_NAMESPACE_BEGIN
class Window : public QWidget {
    Q_OBJECT
public:
    int width() const { return w; }
private:
    int w;
};
ABSL_NAMESPACE_END
}
"""
    module = module_of(src, "x.cpp")
    assert not module.root.has_error
    assert [c.name for c in module.classes] == ["absl.Window"]
    assert units(src, "x.cpp") == ["absl.Window.width"]


OBJCPP = """#import <Foundation/Foundation.h>
#include <vector>

namespace shop {
int Cart::total(const std::vector<int>& items) const {
  int sum = 0;
  for (int x : items) { sum += x; }
  return sum;
}
}  // namespace shop

@implementation Engine {
  std::unique_ptr<shop::Cart> _cart;
}

- (void)start {
  _cart = std::make_unique<shop::Cart>();
}

- (int)run:(const std::string&)name times:(int)n {
  if (n > 0 && !name.empty()) {
    return ::helper(n);
  }
  return 0;
}
@end
"""


def test_objective_cpp_names_and_types_come_from_the_source():
    """`.mm` is parsed with the objc grammar after `a::b` -> `a__b`; names,
    class names and parameter types still read `::`."""
    module = module_of(OBJCPP, "x.mm")
    assert module.language == "objcpp"
    fns = {f.qualname: f for f in module.all_functions()}
    assert list(fns) == ["shop.Cart.total", "Engine.start", "Engine.run:times:"]
    assert fns["shop.Cart.total"].class_name == "shop.Cart"
    assert [p.type_text for p in fns["shop.Cart.total"].params] == ["const std::vector<int>&"]
    assert [p.type_text for p in fns["Engine.run:times:"].params] == ["const std::string&", "int"]
    m = function_metrics(fns["Engine.run:times:"], adapter_for("x.mm"))
    assert (m.cyclomatic, m.cognitive) == (3, 2)  # if +1, `&&` +1


def test_units_inside_error_nodes_are_recovered_with_their_class():
    """When an `@implementation` breaks, the grammar keeps its token and the
    intact methods as children of an ERROR node."""
    src = """@implementation Engine {
}
- (Desc)describe {
}
- (void)focus:(const Request*)request {
  }
}
@end
"""
    module = module_of(src, "x.mm")
    assert module.root.children[0].type == "ERROR"
    assert [(c.name, c.start_line, c.end_line) for c in module.classes] == [("Engine", 1, 8)]
    assert [(f.qualname, f.class_name) for f in module.all_functions()] == [
        ("Engine.describe", "Engine"),
        ("Engine.focus:", "Engine"),
    ]
    assert adapter_for("x.mm").member_refs(module.classes[0]) is not None


# --- units ---------------------------------------------------------------------------------

C_UNITS = """
clamp function 3
find function 3
color_name function 1
cleanup function 0
debug_dump function 1
debug_dump#2 function 1
pick function 1
"""

CPP_UNITS = """
shop.Cart.Cart constructor 0
shop.Cart.~Cart method 0
shop.Cart.add method 1
shop.Cart.total method 0
shop.Cart.operator== operator 1
shop.Cart.discount method 1
shop.largest function 1
main function 2
"""

OBJC_UNITS = """
Greeter.greeterWithName: method 1
Greeter.greet:times: method 2
Greeter.run method 0
helper function 1
"""


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [("sample.c", C_UNITS), ("sample.cpp", CPP_UNITS), ("sample.m", OBJC_UNITS)],
)
def test_fixture_units_kinds_and_params(fixture, expected):
    module = parse_file(FIXTURES / fixture)
    got = [f"{f.qualname} {f.kind} {len(f.params)}" for f in module.all_functions()]
    assert got == expected.strip().splitlines()


def test_classes_and_class_names():
    module = parse_file(FIXTURES / "sample.cpp")
    assert [(c.name, c.kind) for c in module.classes] == [("shop.Cart", "class")]
    fns = {f.qualname: f for f in module.all_functions()}
    assert fns["shop.Cart.discount"].class_name == "shop.Cart"  # out-of-line
    assert fns["shop.Cart.discount"] not in module.classes[0].methods
    objc = parse_file(FIXTURES / "sample.m")
    assert [(c.name, c.kind) for c in objc.classes] == [("Greeter", "class")]


def test_cpp_unit_shapes():
    src = """
namespace a::b {
template <class T> class Box {
public:
    T get() const { return v; }
    struct Inner { int f() { return 1; } };
    friend bool operator<(const Box &l, const Box &r) { return l.v < r.v; }
    T v;
};
}
template <class T> T a::b::Box<T>::twice() { return v + v; }
A::B::~B() {}
extern "C" {
int c_api(int x) { return x; }
}
namespace {
int hidden() { return 0; }
}
struct Plain { int x; struct Deep { void g() {} } d; };
"""
    assert units(src, "x.cpp") == [
        "a.b.Box.get",
        "a.b.Box.Inner.f",
        "a.b.Box.operator<",
        "a.b.Box.twice",
        "A.B.~B",
        "c_api",
        "hidden",
        "Plain.Deep.g",
    ]


def test_objc_categories_class_methods_and_variadic_selectors():
    src = """@implementation NSString (Shout)
+ (instancetype)shout:(NSString *)s, ... {
    return s;
}
- (void)run {
}
@end
"""
    module = module_of(src, "x.m")
    assert [(c.name, c.kind) for c in module.classes] == [("NSString", "extension")]
    shout = module.classes[0].methods[0]
    assert shout.name == "shout:"
    assert [(p.name, p.named, p.variadic) for p in shout.params] == [
        ("s", True, False),
        ("...", False, True),
    ]


def test_loop_macros_are_code_not_units():
    src = """int count(struct list *l) {
    int n = 0;
    LIST_FOREACH(l, item) {
        if (item) n++;
    }
    return n;
}
"""
    module = module_of(src, "x.c")
    assert [f.qualname for f in module.all_functions()] == ["count"]
    m = function_metrics(module.all_functions()[0], adapter_for("x.c"))
    assert m.cyclomatic == 2  # the `if` inside the macro block is count's


def test_class_behind_an_export_macro_keeps_its_methods():
    """`class EXPORT C` parses as a function `C` whose body holds the
    members; the grammar folds the first member after `public:` into a
    labeled statement, the rest are recovered."""
    src = """class EXPORT_API Widget
{
public:
    Widget() { n = 0; }
    int size() const { return n; }
    void grow(int by) { n += by; }
private:
    int n;
};
"""
    assert units(src, "x.cpp") == ["Widget.size", "Widget.grow"]


def test_parameters():
    src = """void none(void) {}
void many(const char *fmt, ...) {}
void cb(int (*fn)(int), unsigned n) {}
int kr(a, b) int a; int b; { return a + b; }
"""
    fns = {f.qualname: f for f in module_of(src, "x.c").all_functions()}
    assert fns["none"].params == []
    assert [(p.name, p.type_text, p.variadic) for p in fns["many"].params] == [
        ("fmt", "const char *", False),
        ("...", None, True),
    ]
    assert [(p.name, p.type_text) for p in fns["cb"].params] == [
        ("", None),
        ("n", "unsigned"),
    ]
    assert [p.name for p in fns["kr"].params] == ["a", "b"]
    cpp = module_of('void f(int x = 1, const std::string &s = "") {}\n', "x.cpp")
    assert [(p.name, p.type_text, p.optional) for p in cpp.all_functions()[0].params] == [
        ("x", "int", True),
        ("s", "const std::string &", True),
    ]


# --- golden metrics --------------------------------------------------------------------------

C_SONAR = """int sumOfPrimes(int max) {
    int total = 0;
    for (int i = 1; i <= max; ++i) {
        for (int j = 2; j < i; ++j) {
            if (i % j == 0) {
                goto next;
            }
        }
        total += i;
    next:;
    }
    return total;
}

const char *getWords(int n) {
    switch (n) {
    case 1: return "one";
    case 2: return "a couple";
    case 3: return "a few";
    default: return "lots";
    }
}

int seq(int a, int b, int c, int d, int e, int f) {
    return a && b && c || d || e && f;
}

int fact(int n) {
    if (n <= 1) return 1;
    return n * fact(n - 1);
}

int elseIf(int a) {
    if (a > 1) {
        return 1;
    } else if (a > 0) {
        return 2;
    } else {
        return 3;
    }
}

int loops(int n) {
    int i = 0;
    while (i < n) {
        do {
            i++;
        } while (i < 10);
    }
    return i > 5 ? 1 : 0;
}
"""

C_EXPECTED = [
    ("sumOfPrimes", 4, 7, 3),  # for +1, for +2, if +3, goto +1
    ("getWords", 4, 1, 1),  # `default` is not a path of its own
    ("seq", 6, 3, 0),
    ("fact", 2, 2, 1),  # recursion +1
    ("elseIf", 3, 3, 1),  # else-if unwrapped from else_clause
    ("loops", 4, 4, 2),  # while +1, do +2, ternary +1
]


@pytest.mark.parametrize(("qualname", "cyc", "cog", "nest"), C_EXPECTED)
def test_c_complexity(qualname, cyc, cog, nest):
    m = metrics(C_SONAR)[qualname]
    assert (m.cyclomatic, m.cognitive, m.max_nesting) == (cyc, cog, nest)


def test_cpp_complexity():
    src = """int Calc::run(const std::vector<int> &xs) {
    int sum = 0;
    try {
        for (int x : xs) {
            auto f = [&](int v) {
                if (v > 0 && x > 0) { return v; }
                return 0;
            };
            sum += f(x);
        }
    } catch (const std::exception &e) {
        return -1;
    } catch (...) {
        return -2;
    }
    return this->run(xs);
}
"""
    m = metrics(src, "x.cpp")["Calc.run"]
    # for +1, lambda nests, if +3 (level 2), `&&` +1, two catches +1 each,
    # `this->run` recursion +1
    assert (m.cyclomatic, m.cognitive, m.max_nesting) == (6, 8, 2)


def test_objc_complexity():
    src = """@implementation Worker
- (int)work:(int)n {
    @try {
        void (^b)(int) = ^(int v) {
            if (v || n) { NSLog(@"%d", v); }
        };
        b(n);
    } @catch (NSException *e) {
        return [self work:n - 1];
    }
    return 0;
}
@end
"""
    m = metrics(src, "x.m")["Worker.work:"]
    # if in a block +2, `||` +1, @catch +1, `[self work:]` recursion +1
    assert (m.cyclomatic, m.cognitive, m.max_nesting) == (4, 5, 1)


def test_fixture_metrics():
    path = FIXTURES / "sample.c"
    fns = {f.qualname: f for f in parse_file(path).all_functions()}
    cleanup = function_metrics(fns["cleanup"], adapter_for(path))
    # if +1, goto +1, do +1, while +1, ternary in while +2
    assert (cleanup.cyclomatic, cleanup.cognitive, cleanup.max_nesting) == (5, 6, 1)


# --- types and cohesion ---------------------------------------------------------------------

BASE_TYPES = """
int => int
unsigned => int
unsigned long long => long
const char * => char
size_t => size_t
uint8_t * => uint8_t
struct point => point
const std::string & => string
std::vector<Order> => Order
std::map<std::string, int> => string int
NSString * => NSString
const Cart & => Cart
auto => -
"""


def test_base_types():
    check_base_types("x.cpp", BASE_TYPES)
    adapter = adapter_for("x.c")
    assert adapter.is_primitive("const char *") is True
    assert adapter.is_primitive("BOOL") is None
    assert adapter.is_primitive("struct point") is False


def test_cpp_member_refs_and_lcom4():
    src = """class Mixed {
public:
    void addUser(int u) { users.push_back(u); }
    int countUsers() const { return users.size(); }
    void store(int k) { this->cache[k] = k; }
    int lookup(int k) { return cache[k]; }
    bool same(const Mixed &o) const { return o.users == o.cache; }
private:
    std::vector<int> users;
    std::map<int, int> cache;
};
"""
    cls = module_of(src, "x.cpp").classes[0]
    adapter = adapter_for("x.cpp")
    refs = adapter.member_refs(cls)
    assert refs["Mixed.store"] == {"cache"}
    assert refs["Mixed.same"] == set()  # `o.users` is another object's
    assert lcom4(cls, adapter) == 2  # `same` touches no member: not counted


def test_cpp_classes_with_out_of_line_methods_are_not_judged():
    src = """class Half {
public:
    int a() { return x; }
    int b();
private:
    int x, y;
};
"""
    cls = module_of(src, "x.cpp").classes[0]
    assert adapter_for("x.cpp").member_refs(cls) == {}


def test_objc_member_refs():
    module = parse_file(FIXTURES / "sample.m")
    refs = adapter_for("x.m").member_refs(module.classes[0])
    assert refs == {
        "Greeter.greeterWithName:": set(),  # the parameter `name` is not the property
        "Greeter.greet:times:": {"count", "name"},
        "Greeter.run": {"count", "greet:times:"},
    }


# --- generated files --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["src/a.pb.cc", "src/a.pb.h", "gen/moc_window.cpp", "qrc_res.cpp", "lex.yy.c", "p.tab.c"],
)
def test_generated_c_family_files_are_excluded(path):
    assert is_excluded(path, DEFAULT_EXCLUDES)


def test_generated_markers():
    assert is_generated(b"/* WARNING! All changes made in this file will be lost! */")
    assert is_generated(b"/* A Bison parser, made by GNU Bison 3.8.2.  */")
    assert not is_excluded("src/ui_helpers.h", DEFAULT_EXCLUDES)


def test_header_routing_and_enum_macros_are_linear():
    """`^\\s*` ran across blank lines from every line start: 80 KB of blank
    lines took 78 s; `NS_ENUM(` + spaces backtracked the same way."""
    start = time.perf_counter()
    assert header_grammar(b"\n" * 80000) == "c"
    assert _APPLE_ENUM.search(b"NS_ENUM(" + b" " * 80000) is None
    assert prepare(b"\n" * 80000, "objc") == b"\n" * 80000
    assert time.perf_counter() - start < 1
