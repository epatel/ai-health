# code-health-model

Decision: how aihealth turns source code into a 1–10 health score, and why.

## The choice

A CodeScene-inspired model: detect explainable code smells per function and
per file, convert each to a weighted penalty, compute the file score as
`clamp(10 − Σ penalties, 1, 10)`, and aggregate to project level as an
average weighted by lines of code.

## Inspiration (CodeScene Code Health)

- Scale 10 (easy to understand and evolve) → 1 (severe quality issues).
- Built from 25+ "biomarkers": e.g. Brain Method, Bumpy Road, Nested
  Complexity, Complex Method, Large Method, Excess Function Arguments,
  Primitive Obsession, DRY violations, Low Cohesion, Large File.
- Project score is LoC-weighted: files of 2.0 (5000 LoC), 10.0 (100), 10.0
  (10) average to 2.18, not 7.33 — big unhealthy files dominate, as they
  should.
- Rules are tunable per repo (weights 0.0 = off; thresholds overridable).

We copy the model, not the calibration — CodeScene's thresholds and weights
are not public, so ours are set in calibration (milestone M11).

## Smells (v1)

Starting thresholds; all overridable in `.aihealth.toml`.

| Smell | Level | Trigger (default) | Hint given to agent |
|---|---|---|---|
| Large Method | function | > 70 LoC (Dart `build()`: > 120) | split into named steps |
| Complex Method | function | cyclomatic > 9 | extract branches / use lookup tables |
| High Cognitive Complexity | function | cognitive > 15 | flatten, early-return |
| Deep Nesting | function | max nesting ≥ 4 | guard clauses, extract inner block |
| Bumpy Road | function | ≥ 2 separate blocks of nesting ≥ 2 | extract each bump into a function |
| Brain Method | function | Large + Complex + Deep Nesting together | decompose; highest weight |
| Excess Arguments | function | > 4 params (Python excludes `self`/`cls`; Dart named params and Swift labelled params count 0.5, `this.x` 0) | introduce parameter object |
| Complex Conditional | function | ≥ 2 boolean operators in one expression | extract to named predicate |
| Primitive Obsession | file | > 60% of *typed* params are primitive, min 10 typed params | introduce domain types |
| Code Duplication | function | clone ≥ 50 tokens / 6 lines, intra- or inter-file | extract shared function |
| Large File | file | > 1000 LoC | split by responsibility |
| Low Cohesion | class | LCOM4 ≥ 3 | split class |
| Missing .PHONY | Makefile file | ≥ 1 phony-looking target not in `.PHONY` (weight 0.5) | declare it in `.PHONY` |
| Deep Widget Tree | Dart / Swift function | widget / view constructor nesting > 8 | extract sub-widgets / `View` structs |

## Metric definitions (implemented in M3)

- **Cyclomatic:** 1 + if/elif/else-if + loops + non-default case arms +
  catch handlers + ternaries + comprehension for/if (Python) + each `and` /
  `or` / `&&` / `||`. A `switch` itself adds 0, and `else` adds 0.
- **Cognitive (SonarSource spec):** +1 + nesting level for if, loop, switch,
  catch and ternary. Else-if and else add a flat +1. +1 per run of like
  boolean operators (`a && b || c` = 2). +1 for a labeled break/continue,
  +1 for a direct recursive call. Closures/lambdas raise the nesting level
  with no increment of their own. Verified against the white paper's
  examples (sumOfPrimes = 7, getWords = 1).
- **Max nesting:** depth of if/loop/switch/catch. Else-if stays at its if's
  depth. Ternaries and closures do not deepen it, so Flutter callbacks don't
  inflate it.
- **Bumps:** top-level body statements whose own nesting depth is ≥ 2. If
  the body is a single loop (optionally labeled), its statements are used
  instead.
- **Condition operators:** per boolean expression root, the count of
  and/or operators. Parentheses and `not` don't split a root.
- **Widget depth (Dart, Swift):** nesting of capitalised constructor calls
  (`Foo(`, `Foo.named(`, `const Foo(`; Swift `VStack { }`, `Text(...)`).
  Value objects like `EdgeInsets` count at the leaves. Modifier chains
  (`.padding()`) don't deepen it.
- Nested named functions are scored on their own and excluded from their
  parent. Closures are part of the enclosing function.

### Baseline distributions (M3 run, for calibration)

| Metric | Python site-packages (9k fns) p90 / p99 | Flutter SDK (60k fns) p90 / p99 |
|---|---|---|
| LoC | 25 / 73 | 21 / 282 |
| cyclomatic | 7 / 20 | 3 / 14 |
| cognitive | 9 / 33 | 2 / 18 |
| max nesting | 2 / 4 | 1 / 3 |
| args | 3 / 8 | 2 / 7 |
| Dart `build()` widget depth | n/a | 6 / 11 |
| Dart `build()` LoC | n/a | 69 / 219 |

Bumpy Road (≥ 2 bumps): 3.7 % of Python and 0.7 % of Dart functions.
Dart outliers were test-file `main()` functions whose `group`/`test`
closures made up thousands of lines. Resolved in M4 (next section).

## Units of scoring (decided in M4)

- A function's LoC counts **its own body lines only** (M11: from the body
  start, so decorators and the parameter list are not counted; a long
  signature is Excess Arguments' concern). Lines inside nested scored units
  (Python local defs, Dart local functions, Dart test closures) belong to
  those units.
- **Dart test closures are separate units** (decision (a), 2026-11-07):
  closures passed to `group`, `test`, `testWidgets`, `testGoldens`,
  `blocTest`, `setUp`, `setUpAll`, `tearDown` and `tearDownAll` become
  functions of kind `test`, named like `main.group("Counter").test("starts at
  zero")`. Configure the list with `[dart] test_closure_calls`. Rationale:
  test code health still matters to agents editing tests, and the
  alternative (excluding test dirs) hides it.
- Qualnames are unique per module. Repeats get `#2`, `#3` in source order,
  so delta can pair functions by name.

## Penalties and score (implemented in M4, `smells.py` + `scoring.py`)

- A smell triggers when `value > threshold`. **Severity** = 1.0 at the
  first triggering value, +1 per `threshold` units beyond it, capped at 3.
  So `large_method` 71 → 1.0 and 141 → 2.0; nesting 4 → 1.0 and 5 → 1.33.
  Ratio thresholds such as primitive obsession use `value / threshold`.
- **Penalty** = `weight × severity × scale`. File-level smells use scale = 1.
  Function/class smells use scale = `0.5 + 0.5 × min(1, 4 × share)`, where
  `share` is the symbol's LoC divided by the file's LoC: a smelly function
  covering ≥ 25 % of the file counts fully, and a tiny one counts half.
- **File penalty P** (M11): penalties are summed per symbol and each
  symbol's sum is capped at 4 (`symbol_cap`). The function/class part is
  divided by `sqrt(loc / 300)` for files over 300 LoC (`density_loc`), so
  big files are judged by the density of problems. File-level smells
  (Large File, Primitive Obsession) are added undamped.
- **File score** = `10 − 9 × (1 − exp(−P / 15))` (`decay`). It is smooth,
  never below 1, and each extra smell costs a little less (P = 1 → 9.42,
  2 → 8.87, 5 → 7.45, 10 → 5.62, 20 → 3.37). This replaced the planned
  `clamp(10 − P)`, which hit 1.0 for any file with one Brain Method. All
  three parameters can be set under `[score]`.
- **Project score** = Σ(score × LoC) / Σ LoC.
- **Bands:** Healthy ≥ 9, Warning 4 to < 9, Alert < 4.
- Default weights: brain_method 2.0, large_file 1.5; large_method,
  cognitive_complexity, deep_nesting, bumpy_road, low_cohesion and
  code_duplication 1.0; complex_method, deep_widget_tree and
  primitive_obsession 0.75; excess_arguments and complex_conditional 0.5.
  Brain Method is reported *in addition to* its three component smells.
- Smell details:
  - Excess Arguments ignores Dart `this.x` / `super.x` field initializers,
    so Flutter widget constructors aren't flagged. Named parameters count
    half: Dart `{…}` params, (M11) Python keyword-only params after `*`
    / `*args` plus `**kwargs`, and (M13) Swift params with a call-site
    label (`_ x` counts fully). All are named at the call site.
  - Primitive Obsession counts only typed parameters. Untyped Python is
    "unknown", not primitive. Dart callbacks (`Function`) and records are
    unknown too, as are Swift closures and tuples. Swift `[T]` / `[K: V]`
    are judged by their element types, like generics.
  - LCOM4 is computed over non-constructor methods that touch at least one
    member, and needs ≥ 4 such methods.
  - Duplication: literals normalised, identifiers kept, ≥ 50 tokens and
    ≥ 6 lines, never crossing a function boundary. Matches are found within
    a file and, in scan mode, across files (`[duplication] cross_file`).
    The finding is per function, with severity 1.0 at 6 duplicated lines
    and +1 per 5 more.

### First full runs (M4, uncalibrated)

| Target | Project score | Healthy / Warning / Alert files |
|---|---|---|
| Flutter `material` (88k LoC) | 4.17 | 59 / 82 / 42 |
| Flutter `packages` (1.3M LoC, incl. tests) | 3.29 | 1055 / 1143 / 696 |
| this repo (2.5k LoC) | 7.18 | 14 / 6 / 1 |

These looked harsh for well-maintained code, which led to the M11
calibration below. Scan speed is about 12k LoC/s including cross-file
duplication.

## Calibration (M11)

Reproduce with `.venv/bin/python scripts/calibrate.py --cache <scratch dir>`.
It reads the corpora without writing to them, caches the findings, and
re-scores them under formula variants without rescanning.

**Targets, set before looking at results:** (1) well-regarded library code
is mostly healthy (project ≈ 8+, median file ≥ 9); (2) large, inherently
complex code lands at ≈ 5–7; (3) only genuine god-files are in alert;
(4) more smells never raise a score, and one mild smell keeps a file
healthy.

**Corpora:** Python libraries from site-packages (click, httpx, starlette,
jinja2, fastapi, uvicorn, anyio; complex: pydantic, pip internals) and Dart
(pub-cache: provider, http, bloc, go_router, dio, args, shelf, collection;
complex: flutter_test, Flutter widgets/material/rendering, flutter_tools).
About 520k LoC in total.

| Corpus | M4 project score | M11 project score | M11 median file | M11 alert LoC % |
|---|---:|---:|---:|---:|
| dart provider / http / bloc / shelf | 9.3 / 9.1 / 9.8 / 8.6 | 9.7 / 9.3 / 9.9 / 9.1 | 10 | 0 |
| dart go_router / collection / args / dio | 7.7 / 7.5 / 7.1 / 6.1 | 8.8 / 8.3 / 7.9 / 7.5 | 9.2–10 | 0–18 |
| py starlette / jinja2 / anyio | 6.7 / 4.8 / 5.5 | 8.0 / 7.0 / 7.1 | 8.9–10 | 0–23 |
| py click / httpx / uvicorn / fastapi | 4.6 / 4.8 / 4.5 / 2.6 | 6.3 / 6.1 / 6.3 / 5.4 | 7.9–10 | 6–34 |
| py pydantic / pip | 4.3 / 6.7 | 6.2 / 8.1 | 9.0–9.4 | 0–26 |
| flutter widgets / material / rendering | 5.0 / 4.4 / 3.9 | 7.2 / 6.8 / 6.3 | 8.4–9.6 | 6–22 |
| flutter_test / flutter_tools | 4.6 / 5.2 | 6.8 / 7.4 | 9.1–10 | 0 |

**What changed, and why:**
1. *Bug:* scanning an explicitly given directory that is itself gitignored
   (e.g. a dependency in `.venv`) returned 0 files. It now falls back to
   walking the directory.
2. *Large Method measures the body.* FastAPI's 30-parameter `Annotated[…,
   Doc()]` signatures had counted as "358-line methods".
3. *Python keyword-only params count half*, consistent with Dart named
   params.
4. *Aggregation:* per-symbol cap 4, density damping above 300 LoC, decay
   10 → 15. Variants compared: cap only, density 500/300, cap 3/4, decay
   10/15. Cap or density alone moved scores by under 1 point. The
   combination met targets 2–4 and most of target 1.
5. *Duplication was kept as is.* It is the top penalty in many Dart
   corpora, but 87 % of that penalty comes from clones of 10+ lines, and
   the samples are real copy-paste (`ElevatedButton.styleFrom` ≈
   `FilledButton.styleFrom`, `_RenderInputPadding` copied across three
   files).

**Known gap, accepted:** several respected Python libraries stay in
*warning* (httpx 6.1, uvicorn 6.3, click 6.3, fastapi 5.4). Their findings
are real: sync/async client copy-paste in httpx, two near-identical
protocol implementations in uvicorn, a 9-group `Context` class in click.
Tuning further would fit the targets, not the code.

**Side effect, accepted:** density damping means adding clean code to a
big file with smells raises its score slightly. Delta still reports any
new or worsened smell, so this can't hide a regression.

## Makefiles (added after M11)

Units: `rule` (rules with a recipe; the body is the recipe parsed as bash),
`define`, `conditional` (top-level `ifeq/ifdef` blocks, named by their
condition) and `variable` (top-level assignments using `$(if/or/and/
foreach)`). Plain assignments and recipe-less rules are not scored, like
module-level constants elsewhere. Make `$(if)`/`$(or)`/`$(and)` count like
ternaries and `$(foreach)` like a loop. Bash `&&`/`||` count as boolean
operators, but only an if/while condition can be a Complex Conditional, so
`mkdir -p x && cd x` statements don't trigger it. Smell hints are
Makefile-specific (adapter `hint()`).

The generic thresholds fit without special cases. On 1,284 hand-written
Makefiles, 96 % are healthy, the median file scores 10, and recipe LoC
p90/p99 is 6/37 (limit 70). The common findings are copied recipes (553),
long recipes (140) and complex shell logic (41).

**Missing .PHONY** is the first language-specific smell. Adapters return
`LanguageSmell(smell, level, line, symbol, value, message)` from
`language_smells(module)`, and the detector applies the usual
threshold/weight/severity rules, so config, delta, report and MCP all work
unchanged. It is file-level (recipe-less targets like `all: app` are not
units), triggers at value > 0 (the number of undeclared targets), and has
weight 0.5 because it's hygiene, not complexity. On 1,270 hand-written
Makefiles only 305 use `.PHONY` at all, and 42 % are flagged. The most
flagged names are all, clean, flash, run, distclean, test, install, deploy
and help. Two guards were added after the first run surfaced junk names
(`-Os`, `+=`, `#`): rules inside grammar-error recovery are ignored, and
names must be plain words matching `[A-Za-z0-9_][A-Za-z0-9_-]*`.

### Refinements made while adding Makefiles

- Generated files are skipped by scan and delta if the first 1 KB has a
  marker (automake, CMake, qmake, "DO NOT EDIT/MODIFY", "@generated",
  "auto-generated"). This matched 27/1,766 site-packages and 23/2,912
  Flutter files, all genuinely generated.
- LCOM4 counts only instance state (Python: attributes assigned as
  `self.x = ...`; Dart: non-static fields) plus method calls. Class
  constants read through `self` no longer tie methods together, which had
  flagged stateless hook classes such as `LanguageAdapter`.
- Condition detection respects a grammar's `condition` field: in bash, an
  if's body statements are also its direct children.
- Calibration rerun after these changes: every corpus is within ±0.1.

## Swift (M13)

Units and grammar notes are in the parser card. The generic thresholds fit
without a Swift exception.

**SwiftUI.** On IceCubesApp, TCA and Kingfisher, 699 `body` getters have
LoC p50 / p95 / p97 / p99 of 18 / 80 / 91 / 145, and 6 % exceed the
70-line limit. Their widget depth is p50 / p95 / p99 2 / 5 / 7, and one
exceeds 8. SwiftUI code already splits views into small structs and
properties, so unlike Flutter `build()` (p97 = 120) no higher `body` limit
was added. A 145-line `body` is worth a finding.

**View-builder closures add no cognitive nesting.** A closure passed to a
capitalised call (`VStack { }`, `ForEach(xs) { }`, `Button { }`) is
layout, like a Flutter widget argument, so it doesn't raise the nesting
level. Ifs and loops inside still count. Closures passed to `Task`,
`AsyncStream`, `AsyncThrowingStream`, `Result` and to lowercase calls
(`.map { }`) keep the Sonar closure penalty. Found by scanning a SwiftUI
app after calibration: `body` getters reached cognitive 85–101, and
5 levels of `VStack`/`HStack` made an `if` cost +6. Functions over the
cognitive limit (15), before → after:

| Codebase | `body` getters | other functions |
|---|---:|---:|
| IceCubesApp | 38 → 17 of 212 | 33 → 19 of 1,891 (view helpers) |
| Claude-Usage-Tracker | 28 → 4 of 112 | 27 → 25 of 1,268 |

Non-UI corpora are unchanged. IceCubesApp went from 8.7 to 8.8, and its
Cognitive Complexity share of the penalty from 19 % to 10 %.

**Excludes added:** `.build/`, `Pods/`, `DerivedData/`, `*.generated.swift`,
SwiftGen headers, and `*.docc/*`. DocC tutorial step files are
near-copies of each other by design: they made up 94 % of TCA's penalty
before the exclude.

**Calibration** (`AIHEALTH_SWIFT_CORPORA=<dir of shallow clones>
scripts/calibrate.py --only swift:`):

| Corpus | kind | kLoC | project | median file | healthy % | alert LoC % |
|---|---|---:|---:|---:|---:|---:|
| Alamofire | lib | 9.4 | 8.5 | 10 | 70 | 0 |
| swift-argument-parser | lib | 9.9 | 7.7 | 10 | 65 | 10 |
| swift-collections | lib | 52.6 | 8.8 | 10 | 77 | 0 |
| Kingfisher | lib | 12.3 | 8.5 | 10 | 77 | 0 |
| vapor | lib | 14.2 | 9.4 | 10 | 88 | 0 |
| TCA (Sources) | lib | 14.4 | 6.2 | 10 | 69 | 31 |
| IceCubesApp | app | 40.1 | 8.8 | 10 | 78 | 0 |
| swift-nio | complex | 72.5 | 7.1 | 9.7 | 57 | 18 |
| SwiftLint | complex | 61.9 | 9.7 | 10 | 91 | 0 |

Code Duplication is the top penalty in every Swift corpus (29–78 %). The
samples are real copy-paste: Alamofire's `DataRequest` and
`DownloadRequest` response handlers, swift-nio's `NIOFS` and
`_NIOFileSystem` copies, and TCA's overload families repeated across
`Binding` / `Bindable` / `UIBindable` plus a 3,669-line
`Deprecations.swift`. As with Dart, duplication is kept as is, and TCA's
6.2 is accepted like httpx's.

**Enum lookup tables: measured, not changed.** Several computed properties
switching over the same enum cases (`label`, `iconName`, …) can look like
a clone, because literals are normalised and the case names match. Of
2,968 clones in the 9 Swift corpora plus a SwiftUI app, 172 lie mostly
(≥ 80 % of lines on both sides) inside all-enum-case `switch` statements.
156 of those have the same literal values on both sides: real copies,
such as swift-nio's `BufferedStream` state machine and
swift-argument-parser's `parseValue` branches. 16 (0.5 %) have different
values. In apps these are the lookup tables (IceCubesApp
`NotificationType.label` / `.menuTitle`, `StatusAction.displayName` /
`.iconName`). In swift-nio they are copied state machines with different
error messages, so a "values differ" filter would also hide real copies.
Dropping all 16 moves no project score by more than 0.03. Not worth a
special case: use the per-file disable comment where it matters. Related:
functions that are one big enum `switch` trip Complex Method (18 in
IceCubesApp, 8 in Claude-Usage-Tracker). That rule is shared by all
languages (each `case` is a branch), so changing it is a separate,
cross-language decision.

## Rust (M14)

Units and grammar notes are in the parser card. Three Rust idioms needed
measured, targeted rules. None changes other languages.

**Unit tests in the same file.** Rust puts unit tests in the file under
test (`#[cfg(test)] mod tests`). Test code is repetitive by nature, so it
lowered production files' scores: in ratatui-widgets, 192 of 199
duplication findings were in test modules. By default these modules are
left out entirely (no units, no lines, no duplication tokens), which
judges a file the way separate test files are judged elsewhere.
`[rust] score_test_modules = true` restores them. Integration tests in
`tests/` are separate files and stay scored. Project score with test
modules → without: ratatui-widgets 6.3 → 7.9, hashbrown 6.1 → 7.7,
crossterm 7.8 → 8.2, regex-syntax alert LoC 50 % → 0 %; crates without
inline tests (serde, syn) unchanged.

**Builder setters and traits are not judged for cohesion.** A builder
setter (`fn title(mut self, t: String) -> Self`) touches one field by
design, so a builder type split into "12 groups" (ratatui `BarChart`,
`Block`, `Dataset`). Methods taking `self` by value and returning `Self`
are excluded from LCOM4. Traits have no fields, so their default
methods aren't judged (itertools' `Itertools`: 12 groups). In
ratatui-widgets all 13 Low Cohesion findings (27 % of its penalty) were
builders; none remain.

**Duplication kept as is.** It is the top penalty in most crates. The
largest clones are real: serde's owned vs borrowed deserializers, nom's
`complete` vs `streaming` modules (76-line copies), hashbrown's
hand-specialised probe loops and memchr's per-architecture SIMD copies.

**Calibration** (`scripts/calibrate.py --only rust:`; crates from
`~/.cargo/registry`, newest version):

| Corpus | kind | kLoC | project | median file | healthy % | alert LoC % |
|---|---|---:|---:|---:|---:|---:|
| anyhow | lib | 2.2 | 9.5 | 10 | 92 | 0 |
| itertools | lib | 8.4 | 8.8 | 10 | 78 | 0 |
| crossterm | lib | 5.4 | 8.2 | 10 | 77 | 13 |
| ratatui-widgets | lib | 12.5 | 8.2 | 10 | 74 | 0 |
| memchr | lib | 7.6 | 7.1 | 9.4 | 60 | 9 |
| serde_json | lib | 12.5 | 7.0 | 10 | 76 | 26 |
| serde | lib | 11.5 | 5.7 | 10 | 75 | 26 |
| nom | lib | 8.0 | 5.4 | 8.6 | 43 | 43 |
| hashbrown | complex | 8.4 | 7.7 | 10 | 79 | 0 |
| termwiz | complex | 18.7 | 7.1 | 8.5 | 45 | 0 |
| regex-automata | complex | 24.6 | 6.8 | 8.8 | 48 | 13 |
| regex-syntax | complex | 9.2 | 6.3 | 7.3 | 37 | 0 |
| syn | complex | 19.7 | 6.2 | 10 | 62 | 41 |

The median file is healthy in every library. serde and nom stay low on
deliberate duplication, accepted like httpx and TCA. Other generic
thresholds fit without Rust exceptions. `?` adds no complexity
(error propagation, like Go's `if err != nil` question in the README).

## TypeScript (M15)

Units and grammar notes are in the parser card. **What counts as a
function** follows the README guidance: declarations, class members,
functions bound to a name (`const f = () => {}`, class fields, methods of
a named object literal, `export default`) and test closures (`describe`,
`it`, `test`, `beforeEach`, ...; `[typescript] test_closure_calls`) are
units. Inline callbacks stay part of their function, as closures do
elsewhere. Nested named functions (a React component's `const onClick =
() => {...}`) are scored on their own, so a component's size isn't
inflated by its handlers. No TypeScript-specific threshold was needed.

**JSX** nesting is the Deep Widget Tree depth. On Excalidraw and React
Query devtools, 325 functions with JSX have depth p50 / p90 / p95 / p99
of 2 / 4 / 5 / 7; only Excalidraw's 410-line `App.render` (12) exceeds 8.
`&&` in conditional rendering counts as a boolean operator; `??` and `?.`
don't (as SonarSource specifies for null-coalescing).

**Calibration corpora exclude test files.** These repos keep tests inside
`src/` (`__tests__/`, `*.test.ts`): 81–91 % of the lines in TanStack
Query, 57–61 % in zod and Nest, and nearly all duplication findings were
in them, which put react-query at 4.2. Calibration targets library code
(the Python and Dart corpora hold few tests), so `calibrate.py` excludes
test files for `ts:` corpora. Real scans score test files in every
language.

**Calibration** (`AIHEALTH_TS_CORPORA=<dir of shallow clones>
scripts/calibrate.py --only ts:`; test files excluded):

| Corpus | kind | kLoC | project | median file | healthy % | alert LoC % |
|---|---|---:|---:|---:|---:|---:|
| react-query | lib | 1.9 | 9.4 | 10 | 78 | 0 |
| tRPC server | lib | 8.4 | 8.9 | 10 | 82 | 0 |
| query-core | lib | 5.8 | 8.7 | 9.1 | 52 | 0 |
| date-fns | lib | 77.3 | 8.7 | 10 | 79 | 6 |
| ky | lib | 2.4 | 7.1 | 10 | 84 | 0 |
| zod | lib | 31.1 | 6.6 | 7.9 | 40 | 21 |
| Nest core | complex | 16.8 | 8.5 | 10 | 83 | 0 |
| Excalidraw | complex | 79.8 | 7.3 | 10 | 72 | 14 |
| Vite | complex | 38.6 | 6.7 | 8.9 | 50 | 0 |
| TypeORM | complex | 75.4 | 5.0 | 10 | 75 | 53 |

The low scores are real complexity: zod's locale files are one giant
nested `switch` per language (cyclomatic 46–54) and its JSON-schema
converter has a 336-line function; ky's `deepMergeInternal` has
cyclomatic 61; TypeORM's `PostgresQueryRunner.changeColumn` is 1,035
lines, and its database drivers copy each other.

## JavaScript (M16)

Same unit rules as TypeScript, plus **assigned functions as units**:
CommonJS and pre-class code defines functions as `module.exports = {...}`,
`app.handle = function handle() {...}` or `Foo.prototype.bar = function`.
Without this, Express-style code would be inline callbacks of top-level
statements and not scored at all. Parameters are untyped, so Primitive
Obsession never applies. No JavaScript-specific threshold was needed.
Calibration corpora exclude tests, `dist/`, `vendor/`, `perf/` and
`benchmarks/` (`calibrate.py --only js:`, `AIHEALTH_JS_CORPORA`).

| Corpus | kind | kLoC | project | median file | healthy % | alert LoC % |
|---|---|---:|---:|---:|---:|---:|
| Express | lib | 1.1 | 8.7 | 10 | 83 | 0 |
| Preact | lib | 6.1 | 8.4 | 10 | 88 | 0 |
| axios | lib | 6.6 | 8.1 | 10 | 76 | 0 |
| lodash | lib | 7.2 | 4.1 | 10 | 89 | 83 |
| ESLint | complex | 63.6 | 7.6 | 9.1 | 54 | 2 |
| three.js | complex | 78.5 | 7.3 | 10 | 73 | 14 |
| webpack | complex | 217.4 | 4.6 | 9.4 | 55 | 45 |

lodash's score is one monolith: `lodash.js` is a single 5,990-line file
with 135 findings (89 % of the corpus's files are healthy, but this one
holds 83 % of the lines). webpack's `lib/` contains hand-ported parsers
and printers (from terser and SWC) of 11–22k lines with functions up to
2,344 lines; they are real code, not generated.

## CMake (M17)

Units follow the Makefile adapter: `function()` / `macro()` definitions,
plus top-level `if` / `foreach` / `while` blocks named by their header;
plain commands are not scored. `AND` / `OR` in if/elseif/while conditions
are boolean operators. Fix hints are CMake-specific (adapter `hint()`).

The generic thresholds fit without CMake exceptions. On 566 hand-written
files from 7 projects (OrcaSlicer, Pico SDK, Flutter, OpenSCAD,
firebase-ios-sdk, leveldb, nanopb), 90 % are healthy, the median file
scores 10 and no file is in alert. Unit LoC p50 / p90 / p99 is
6 / 27 / 117 (1,380 units). Project scores: Flutter 9.90,
firebase-ios-sdk 9.86, Pico SDK 8.95, OrcaSlicer 8.94, OpenSCAD 8.07. The
most common findings are cognitive complexity (63), complex method (57)
and complex conditionals (42): nested platform and option logic, such as
CGAL's 289-line `if(CGAL_BRANCH_BUILD)` block (cognitive 123) and
OrcaSlicer's `FindGLEW.cmake`.

## C, C++ and Objective-C (M18)

Generic thresholds, no C-family exceptions. Units are function definitions,
C++ methods (in-class and out-of-line) and Objective-C methods; lambdas and
blocks are closures. `goto` is +1 cognitive (JUMP_LABEL), `#if` adds
nothing. Objective-C selector parameters count half for Excess Arguments.
Primitive Obsession: `int`/`char`/`long`/... , `<stdint.h>` types,
`std::string`/`string_view`, bare containers (`vector`, `map`, ...) and
Foundation value types (`NSString`, `NSNumber`, `NSInteger`, `NSArray`,
`id`, ...) are primitive; `bool`/`BOOL`/`void` are neutral; `const`, `*`,
`&` and `std::` are stripped. Low Cohesion: C has no classes; a C++
class/struct/union is a `Class` only with a method in its body, and it is
judged only when no method is merely declared there (members are used
without `this->`, and out-of-line methods live in another file);
Objective-C uses ivars, properties of a same-file `@interface`, `self.x`
and `[self m]`.

Calibration corpora exclude tests, examples, fuzzers and benchmarks,
including XCTest files kept next to the sources (`*Test.mm`, `*Tests.m`):
with them, Flutter's darwin code scored 6.4 with 76 % of the penalty from
duplicated test methods (`calibrate.py --only c` covers C and C++,
`--only objc` covers Objective-C and Objective-C++; `AIHEALTH_C_CORPORA`).
Objective-C++ (`.mm`) uses the same rules as Objective-C plus C++ out-of-line
methods; no exceptions.

| Corpus | kind | kLoC | project | median file | healthy % | alert LoC % |
|---|---|---:|---:|---:|---:|---:|
| lua | lib | 22.5 | 7.7 | 10 | 76 | 0 |
| cJSON | lib | 4.3 | 4.3 | 9.7 | 62 | 84 |
| libuv | lib | 38.6 | 5.8 | 8.7 | 48 | 23 |
| redis | complex | 153.4 | 3.9 | 8.5 | 47 | 66 |
| curl | complex | 120.7 | 5.0 | 10 | 61 | 45 |
| fmt | lib | 14.9 | 6.5 | 9.6 | 57 | 24 |
| leveldb | lib | 14.3 | 7.6 | 10 | 76 | 7 |
| googletest | lib | 14.0 | 7.5 | 10 | 81 | 0 |
| nlohmann/json | complex | 38.4 | 5.8 | 9.4 | 55 | 23 |
| abseil | complex | 99.0 | 8.3 | 10 | 84 | 4 |
| SDWebImage | lib | 16.2 | 7.6 | 10 | 87 | 8 |
| AFNetworking | lib | 3.6 | 7.1 | 10 | 64 | 0 |
| fmdb | lib | 2.4 | 6.7 | 10 | 69 | 0 |
| Mantle | lib | 2.8 | 7.2 | 10 | 86 | 0 |
| KVOController | lib | 0.5 | 9.0 | 10 | 80 | 0 |
| Texture (Obj-C++) | lib | 48.7 | 7.9 | 10 | 87 | 6 |
| ComponentKit (Obj-C++) | lib | 20.6 | 9.5 | 10 | 95 | 0 |
| Flutter engine darwin (Obj-C/Obj-C++) | complex | 29.6 | 8.4 | 10 | 90 | 0 |

C++ and Objective-C land where Python and Dart libraries did. C sits lower,
and the samples behind it are real: libuv's `uv__tty_write_bufs` (cognitive
340) and a `uv__io_poll` copied across four platform files (79–117
duplicated lines each); redis's 9,630-line `redis-cli.c`, 1,360-line
`rdbLoadObject`, and unit tests compiled into production files under
`#ifdef REDIS_TEST` (`quicklistTest`, 1,281 lines; the preprocessor
blanking can't tell them apart); cJSON is two dense files with
`parse_object` ≈ `parse_array`. As with httpx and click in M11, a C
exception would fit the targets, not the code. The C idiom of passing the
context first (`uv_fs_open(loop, req, path, flags, mode, cb)`) does count
for Excess Arguments; it is not a dominant penalty (≤ 11 % of any corpus).

## Shell scripts (M19)

Generic thresholds, no shell exceptions. Units are functions and
top-level `if`/`for`/`while`/`case` blocks (as in CMake); scripts are
mostly top-level commands, which aren't scored, and a long script with
no structure shows up as Large File rather than one giant unit. Functions
take no declared parameters, so Excess Arguments and Primitive Obsession
never apply, and there are no classes. `break 2` / `continue 2` count +1
like labeled jumps. `cmd || die` guards are counted: of 4,371 top-level
`&&`/`||` lists, 725 are single-operator guards ending in `exit`/`return`/
`die`/..., which is 3 % of all cognitive points, too little to justify an
exemption. Calibration excludes test directories (`calibrate.py --only
sh:`, `AIHEALTH_SH_CORPORA`).

| Corpus | kind | kLoC | project | median file | healthy % | alert LoC % |
|---|---|---:|---:|---:|---:|---:|
| bats-core | lib | 3.7 | 8.1 | 10 | 68 | 0 |
| rbenv | lib | 1.2 | 9.2 | 10 | 90 | 0 |
| pyenv | lib | 7.8 | 7.6 | 10 | 79 | 0 |
| n | lib | 1.2 | 5.8 | 10 | 67 | 0 |
| docker-install | lib | 1.0 | 7.9 | 10 | 50 | 0 |
| bash-it | lib | 17.6 | 8.6 | 10 | 88 | 3 |
| Homebrew install | complex | 1.5 | 8.4 | 9.7 | 50 | 0 |
| dehydrated | complex | 2.0 | 3.9 | 3.9 | 0 | 100 |
| nvm | complex | 7.0 | 4.0 | 10 | 70 | 86 |
| acme.sh | complex | 51.2 | 7.3 | 8.8 | 48 | 16 |
| dokku | complex | 18.9 | 8.7 | 10 | 85 | 0 |

The low scores are single big scripts: dehydrated is one 2,000-line file,
nvm's `nvm.sh` holds a 4,000-line `nvm()` dispatcher, and `n` is mostly
one `bin/n`. acme.sh's 300 DNS-API plugins copy each other's REST helpers
(duplication is 40 % of its penalty).

## Delta classification

Functions are paired by qualified name (`Class.method`); renamed functions
appear as removed + added.

Findings pair by `(level, symbol, smell)`. The before-content is scored
under the new path, so findings still pair across a git rename.

- **new** smell: present after, absent before in that function.
- **worsened** / **improved**: present in both, and severity went up / down.
  Text output shows the previous metric value ("was 12").
- **fixed**: present before, absent after.
- Per-file verdict (implemented in M5, refined from the plan):
  - `degraded` if the score dropped by more than `delta.tolerance`
    (default 0.0), **or** there are new/worsened smells *without a net score
    gain*. Trading one smell for another is allowed only if the file gets
    healthier overall.
  - Added files are judged on their own findings: any smell means
    degraded, and a clean new file is unchanged.
  - `improved` if the score rose or anything was fixed or reduced, and
    `unchanged` otherwise.
  - Deleted files are ignored, since removing code never degrades health.
- Overall: degraded if any file degraded (exit 1), else improved if any
  file improved.
- Duplication in delta mode is checked within each file only. Cross-file
  clones introduced by a change are not detected yet.
- The tolerance exists because a function can grow without its smells
  getting worse: its share of the file rises, so its penalty rises.

### File-level suppression (M7)

A comment `aihealth: disable=smell_a, smell_b` (keys as in `[weights]`)
removes those smells from that file's findings and score. Only real
comment nodes count, not strings or docstrings: an example in a docstring
once silenced `smells.py` itself. This is meant for boundaries where a
smell is imposed by an interface. For example, MCP tool parameters are a
JSON schema, so primitives and flat argument lists are required. Use it
sparingly and always with a reason in the comment.

### Complex Conditional = conditions only (M10)

Only boolean expressions that are the condition of an if/elif/while,
ternary, collection-if or comprehension/case guard count (parentheses and
`not` are looked through). Values such as `return a and b`, default chains
`x = a or b or c` and asserts don't count. Found while dogfooding the
hooks: Python's `cwd or env or getcwd()` fallback was flagged. This
supersedes the M5 assert-only exclusion. Cyclomatic and cognitive still
count every boolean operator.

### Noise fixes from dogfooding (M5)

- Boolean expressions inside `assert` (Python `assert`, Dart `assert(...)`)
  don't count as Complex Conditional. They are checks, not branching logic.
- Generic containers are judged by their type arguments (M9): `list[Order]`
  / `List<Order>` is a domain type, `dict[str, int]` / `Map<String,
  dynamic>` stays primitive, and a bare `list` is primitive. Callables are
  unknown.
- Delta `tolerance` defaults to 0.1 (M9). When a file shrinks, its
  unchanged smelly functions cover a bigger share and the score dips
  slightly with nothing actually worse. New or worsened smells still fail
  at any tolerance unless the file improves on net.
- `bool` parameters are neutral for Primitive Obsession (they are flags,
  not hidden domain concepts). Before this fix, CLI modules full of
  `verbose: bool` were flagged.

## Alternatives considered

- **Raw metrics only (radon, lizard):** numbers without a verdict; agents
  need "is this worse, and what to do" not a cyclomatic count.
- **Maintainability Index:** opaque formula (Halstead volume), poor at
  pointing to the function to fix.
- **Lint rule counts (ruff, dart analyze):** style-heavy, says little about
  structural maintainability; complements but doesn't replace.

## When to revisit

- Re-run `scripts/calibrate.py` after changing thresholds, weights or the
  aggregation. Scores bunching (everything 9–10 or everything < 4) means
  recalibrate.
- Agents game a single metric (e.g. splitting functions mechanically) while
  readability drops — add or reweight smells.
- A language is added whose idioms break a threshold (like Flutter `build`).
