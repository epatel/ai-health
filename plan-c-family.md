# Plan: C, C++ and Objective-C (M18)

Written 2026-11-07, before a context reset. Everything needed to start is
here or in the files it points to. Follow README "Adding a language" for the
general method. This plan covers what is specific to the C family.

## Recommendation: one combined adapter

Combine the three languages in **one module, `lang/cfamily.py`, with one
adapter class that holds three parsers** (`c`, `cpp`, `objc` from
tree-sitter-language-pack) and picks one per file. Each module still reports
its own language (`module.language` = `c`, `cpp` or `objc`).

Why combined, rather than three adapters like TypeScript/TSX/JavaScript:

- **`.h` can't be routed by extension.** A header may be C, C++ or
  Objective-C, and the registry picks an adapter class per extension. One
  adapter that chooses the grammar per file solves this without a registry
  change.
- **The grammars share their statement nodes.** All three use C's
  `if_statement` (with `else_clause` wrapping the else-if, like TypeScript and
  Rust), `switch_statement`/`case_statement`, `for`/`while`/`do`,
  `labeled_statement`, `compound_statement`, `function_definition` and
  `parameter_list`. C++ and Objective-C only *add* node types (classes,
  namespaces, lambdas, try/catch; `@interface`, methods, blocks, message
  sends). A single role table over the union of node types works, because
  the metrics only look at node types.
- One config section, one set of excludes, one set of tests for the shared
  part.

Shape: `CFamilyAdapter(LanguageAdapter)` with `language = "c"` (the config
key and base parser), `extensions = (".c", ".h", ".cpp", ".cc", ".cxx",
".hpp", ".hh", ".hxx", ".m")`, an overridden `parse()` that picks the grammar
and sets `module.language`, and helper functions per language for extraction.
If the module grows past ~600 lines, split the extraction into
`lang/cfamily_cpp.py` / `lang/cfamily_objc.py`, keeping one adapter class.
`SUPPORTED_LANGUAGES` in `lang/__init__.py` gains "C, C++, Objective-C".

## Measured facts (this machine)

Grammar choice per file type, share of files with any parse error:

| Corpus (400-file samples) | `c` grammar | `cpp` grammar | `objc` grammar |
|---|---:|---:|---:|
| C: pico-sdk + TinyUSB | 49 % | 53 % | 44 % |
| C: flipperzero-firmware | 27 % | 26 % | 25 % |
| C++: OrcaSlicer `src/` | 97 % | **26 %** | 97 % |
| Objective-C: Xcode checkouts `.m` | 97 % | 97 % | **23 %** |
| Objective-C++: `.mm` (114 files) | 100 % | 95 % | 88 % |

- **C errors are local and macro-driven.** On 600 C files: 192 have errors,
  but error nodes cover only 3.7 % of lines, and only 2.1 % of 6,216
  `function_definition`s sit inside an ERROR node. Causes: attribute macros
  the parser can't expand (`PLACE_IN_SECTION("x") ALIGN(4) static ...`,
  `__weak`, `PRIVILEGED_DATA`), `#if`/`#endif` splitting a construct,
  table macros (`{ MBEDTLS_..., &info },`) and token pasting (`##`).
- **Headers** (sample of 645 `.h`): 438 plain C, 174 with C++ markers, 33
  with Objective-C markers.
- **No grammar parses Objective-C++ (`.mm`)** acceptably.

Corpora on disk (all read-only):

| Language | Where | Size |
|---|---|---|
| C | `~/Development/pico-sdk` | 686 files, 301k lines |
| C | `~/Development/flipper/flipperzero-firmware` | 6,194 files, 2.7M lines |
| C++ | `~/Development/claude/orcaslicer/OrcaSlicer/src` | 1,217 files, 626k lines |
| C++ | `~/Development/Flutter/flutter/engine` | 1,679 files, 464k lines |
| C++ | leveldb in `~/Library/Developer/Xcode/DerivedData/*/SourcePackages/checkouts` | 75 files |
| Objective-C | same checkouts (firebase-ios-sdk, Sparkle, GoogleUtilities, ...) | 1,142 `.m`, 254k lines |

For calibration, also shallow-clone well-regarded code into the scratchpad
(like the TypeScript corpora): C libraries such as lua, cJSON, libuv,
redis (complex) and curl (complex); C++ such as fmt, leveldb, abseil
(complex); Objective-C such as SDWebImage and AFNetworking. Avoid
amalgamations over 1 MB (sqlite3.c), which scans skip anyway.

## Decisions

Settled with the user on 2026-11-07 ("use your recommendations"):
parse errors not reported, `.mm` left out of the first version, vendored
folders not excluded by default, and `goto` counts +1. The rest are
recommendations from the plan, open to change if measurements disagree.


1. **Header routing** (recommended): Objective-C if the file has
   `@interface`/`@protocol`/`@implementation`/`@end` or `#import` at a line
   start; C++ if it has `namespace`, `template <`, `class X {`/`class X :`,
   `public:`/`private:` or `::`; otherwise C. Optional refinement if
   measurements show misroutes: look at sibling files in the same
   directory (`.m` → objc, `.cpp`/`.cc` → cpp).
2. **Parse errors** (settled: `reports_parse_errors = False`, as for
   Makefiles). A third of C files would otherwise show "syntax errors" that
   are only unexpanded macros. Record the measured rates in the parser card.
3. **Objective-C++ `.mm`** (settled: not supported in the first version,
   so not in `extensions`). Measure function coverage with the
   `objc` grammar before deciding. If most methods are still extracted,
   add `.mm` with errors not reported.
4. **Preprocessor** (recommended): `#if`/`#ifdef` add no complexity (they
   are compile-time). Functions inside `preproc_if`/`preproc_ifdef`/
   `preproc_else` blocks at file level must still be extracted, so the
   extraction walks into those nodes. Function-like macros (`#define F(x)
   ...`) are not units.
5. **C units**: every `function_definition`; name from the innermost
   declarator identifier (through `pointer_declarator` and
   `function_declarator`). `static` functions are ordinary units.
6. **C++ units**: free functions, methods defined inside a class body
   (`ClassName.method`), out-of-line methods (`int A::set(...)`, named
   `A.set`, with `class_name = "A"`), constructors and destructors (`A.A`,
   `A.~A`), operators (`A.operator==`), and functions in namespaces (prefix
   `ns.`). Lambdas are closures (LAMBDA). `template_declaration` wraps the
   function or class it templates, so look through it.
7. **Objective-C units**: methods in `@implementation` blocks, named by
   class and selector (`Foo.bar:with:`; `+` class methods too). Selector
   parameters have labels, so they count half for Excess Arguments, like
   Swift. Blocks (`^{ }`) are closures. C functions in `.m` files are units
   as in C. `@interface` declarations have no bodies and are not units.
8. **Roles**: `if_statement` IF, else-if via `else_if()` unwrapping
   `else_clause` (exactly as TypeScript), `for_statement`,
   `for_range_loop` (C++), `while_statement`, `do_statement` LOOP,
   `switch_statement` SWITCH, `case_statement` CASE unless it is `default:`,
   `catch_clause` (C++) and `@catch` (Objective-C) CATCH,
   `conditional_expression` TERNARY, `lambda_expression` and block
   literals LAMBDA, nested function/class definitions STOP,
   `binary_expression` with `&&`/`||` BOOL_EXPR. **`goto`** (settled): +1
   cognitive, as SonarSource specifies, by giving `goto_statement` the
   `JUMP_LABEL` role.
9. **Cohesion (LCOM4)**: C has no classes (Low Cohesion never applies).
   C++ members are often used without `this->`, and out-of-line methods
   live in another file than the class. Recommended: only judge classes
   whose methods are defined inside the class body, using the field names
   declared in that body plus `this->x`; return `{}` for the rest (the
   smell is then simply off). Objective-C: `self.x`, `_x` ivars and
   `[self method]`.
10. **Primitive Obsession**: types as written in `parameter_declaration`.
    Strip `const`, `volatile`, `&`, `&&`, `*` and `std::`. Primitive: `int`,
    `char`, `short`, `long`, `float`, `double`, `size_t`, `uint8_t` and
    other `<stdint.h>` types, `string`, `string_view`, `vector`, `map`,
    `unordered_map`, `set`, `array`; Objective-C `NSString`, `NSNumber`,
    `NSInteger`, `NSUInteger`, `CGFloat`, `NSArray`, `NSDictionary`, `id`.
    Neutral: `bool`, `_Bool`, `BOOL`. Generics judged by their arguments
    (`std::vector<Order>` is a domain type). Function pointers and
    templates parameters are unknown.
11. **Excludes and generated files**: `third_party/*`, `vendor/*` and
    `external/*` are not excluded by default (settled; consistent with the
    other languages, users add them under `[scan] exclude`). Default-exclude
    generated code: Qt `moc_*.cpp`, `ui_*.h`, `qrc_*.cpp`; protobuf
    `*.pb.cc`/`*.pb.h` (already matched by its "DO NOT EDIT" marker);
    flex/bison output (`lex.yy.c`, `*.tab.c`). Add the Qt uic marker
    ("All changes made in this file will be lost") to
    `base._GENERATED_MARKERS`.
12. **Widget depth**: none.
13. Out of scope: CUDA (`.cu`, grammar `cuda` exists), Objective-C++
    unless decision 3 says otherwise, C++ modules (`.cppm`, `.ixx`).

## Steps (each with its check)

1. **Spike** each grammar with a modern fixture (`tests/fixtures/sample.c`,
   `sample.cpp` with classes/templates/lambdas/namespaces/out-of-line
   methods, `sample.m` with interface/implementation/blocks/@try). Zero
   ERROR nodes. Pin node names in `tests/test_parser_spike.py`. Record
   quirks in `cards/parser-choice.md`.
2. **Baseline snapshots** of every existing language before touching shared
   code (they take ~15 minutes, run in the background):
   `scripts/snapshot.py --lang python,dart,swift,rust,typescript,tsx,javascript,cmake`
   over site-packages, the Flutter `packages`, `~/.cargo/registry/src` and
   the TypeScript/JavaScript/Swift clones if they still exist, plus
   `--lang make` over `~/Development`. The scratchpad clones are gone after
   a reset: re-clone or drop those roots.
3. **Adapter** (`lang/cfamily.py`) following the decisions above; register
   it in `ADAPTERS`; config section `[cfamily]` only if an option is
   needed; excludes in `config.py`.
4. **Golden tests** (`tests/test_cfamily.py`): SonarSource `sumOfPrimes`
   (= 7, with `continue OUT` written as a `goto` loop or a labeled-free
   variant), `getWords` (= 1), `a && b && c || d || e && f` (= +3), in C;
   plus else-if, `default:`, `goto`, C++ lambda nesting, try/catch,
   range-for, out-of-line methods, Objective-C selector names, blocks and
   `@try/@catch`, and the header router (one test per branch).
5. **Coverage** on the corpora above: 0 crashes, every
   `function_definition` / ObjC `method_definition` extracted except those
   inside ERROR nodes, and report the parse-error rate per language.
6. **Calibrate**: add `c:`, `cpp:`, `objc:` corpora to `scripts/calibrate.py`
   (shallow clones via an env var, like `AIHEALTH_TS_CORPORA`; exclude test
   files as for TypeScript). Check targets: libraries mostly healthy,
   complex code around 5–7. Look at the samples behind any dominant smell
   before changing anything. Record numbers in `cards/code-health-model.md`.
7. **Prove other languages unchanged**: rerun the snapshots and
   `--compare` (exit 0 = identical).
8. **Dogfood and document**: `aihealth delta` clean, full-repo scan
   (cross-file clones between the new tests and existing ones show up only
   there: share helpers via `tests/helpers.py`), README section, cards,
   CLAUDE.md, `AGENT-INSTALL.md`, `project-plan.md` (M18 done).

## Risks to watch

- **Macro-heavy embedded C** (Flipper, Pico SDK) may produce odd function
  names when a macro precedes a definition (`PLACE_IN_SECTION(...) void
  f()`): check extracted names on a sample.
- **K&R-style and old C** parameter lists, and `__attribute__((...))`
  everywhere.
- **Huge generated tables** in `.c`/`.h` (fonts, firmware blobs) will look
  like large files; they're usually under 1 MB and unmarked. Check the
  worst files in calibration and add patterns if needed.
- **Header routing errors** move a file to the wrong grammar, and it then
  fails to parse almost entirely (97 % error rate across grammars).
  Measure the router on the 645-header sample before trusting it.
