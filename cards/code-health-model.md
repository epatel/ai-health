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
| Excess Arguments | function | > 4 params (Python excludes `self`/`cls`; Dart named params count 0.5) | introduce parameter object |
| Complex Conditional | function | ≥ 2 boolean operators in one condition | extract to named predicate |
| Primitive Obsession | file | > 60% of params across file are untyped/primitive, min 10 params | introduce domain types |
| Code Duplication | file | clone ≥ 50 tokens / 6 lines, intra- or inter-file | extract shared function |
| Large File | file | > 1000 LoC | split by responsibility |
| Low Cohesion | class | LCOM4 ≥ 3 | split class |
| Deep Widget Tree | Dart function | widget constructor nesting > 8 in `build()` | extract sub-widgets |

## Penalties and score

- Each finding has a severity `s = min(value / threshold, 3)` (how far over).
- Penalty = `weight(smell) × s`, scaled by the share of the file's LoC the
  function covers for function-level smells — a 300-line bad function hurts a
  file more than a 10-line one.
- File score = `clamp(10 − Σ penalties, 1, 10)`, rounded to 2 decimals.
- Project score = Σ(score × LoC) / Σ LoC over scored files.
- Bands: **Healthy** ≥ 9, **Warning** 4 – < 9, **Alert** < 4.
- Files with no functions and < 20 LoC score 10 and are excluded from
  project weighting noise only by their small LoC.

## Delta classification

Functions are paired by qualified name (`Class.method`); renamed functions
appear as removed + added.

- **new** smell: present after, absent before in that function.
- **worsened**: present in both, severity increased.
- **fixed**: present before, absent after.
- Verdict `degraded` if any touched file score drops by more than
  `delta.tolerance` (default 0.0) or any new/worsened smell exists; exit 1.

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
