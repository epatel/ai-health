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
| Excess Arguments | function | > 4 params (Python excludes `self`/`cls`; Dart named params count 0.5, `this.x` 0) | introduce parameter object |
| Complex Conditional | function | ≥ 2 boolean operators in one expression | extract to named predicate |
| Primitive Obsession | file | > 60% of *typed* params are primitive, min 10 typed params | introduce domain types |
| Code Duplication | function | clone ≥ 50 tokens / 6 lines, intra- or inter-file | extract shared function |
| Large File | file | > 1000 LoC | split by responsibility |
| Low Cohesion | class | LCOM4 ≥ 3 | split class |
| Deep Widget Tree | Dart function | widget constructor nesting > 8 in `build()` | extract sub-widgets |

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
- **Widget depth (Dart):** nesting of capitalised constructor calls
  (`Foo(`, `Foo.named(`, `const Foo(`). Value objects like `EdgeInsets` count
  at the leaves.
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

- A function's LoC counts **its own lines only**. Lines inside nested
  scored units (Python local defs, Dart local functions, Dart test closures)
  belong to those units.
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
- **File score** = `10 − 9 × (1 − exp(−P / 10))`, where P is the sum of
  penalties. It is smooth, never below 1, and each extra smell costs a
  little less (P = 1 → 9.14, 5 → 6.46, 10 → 4.31, 20 → 2.22). This replaced
  the planned `clamp(10 − P)`, which hit 1.0 for any file with one Brain
  Method.
- **Project score** = Σ(score × LoC) / Σ LoC.
- **Bands:** Healthy ≥ 9, Warning 4 to < 9, Alert < 4.
- Default weights: brain_method 2.0, large_file 1.5; large_method,
  cognitive_complexity, deep_nesting, bumpy_road, low_cohesion and
  code_duplication 1.0; complex_method, deep_widget_tree and
  primitive_obsession 0.75; excess_arguments and complex_conditional 0.5.
  Brain Method is reported *in addition to* its three component smells.
- Smell details:
  - Excess Arguments ignores Dart `this.x` / `super.x` field initializers,
    so Flutter widget constructors aren't flagged.
  - Primitive Obsession counts only typed parameters. Untyped Python is
    "unknown", not primitive. Dart callbacks (`Function`) and records are
    unknown too.
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

These look harsh for well-maintained code. Big files collect many
findings, and exponential decay still sums them all. Calibrate in M11.
Scan speed is about 12k LoC/s including cross-file duplication.

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

### Noise fixes from dogfooding (M5)

- Boolean expressions inside `assert` (Python `assert`, Dart `assert(...)`)
  don't count as Complex Conditional. They are checks, not branching logic.
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

- Calibration shows scores bunching (everything 9–10 or everything < 4).
- Agents game a single metric (e.g. splitting functions mechanically) while
  readability drops — add or reweight smells.
- A language is added whose idioms break a threshold (like Flutter `build`).
