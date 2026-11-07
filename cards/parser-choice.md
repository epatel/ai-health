# parser-choice

Decision: parse every language with tree-sitter behind a per-language adapter.

## The choice

Use `tree-sitter` (Python bindings) with grammars from
`tree-sitter-language-pack`. Each language gets an adapter in
`src/aihealth/lang/` that maps grammar node types onto a uniform model
(`Module`, `Class`, `Function`) plus three node-type sets the metric code
consumes: **decision points** (if/elif/for/while/catch/case/`&&`/`||`/`?:`/
`??`), **nesting constructs**, and **function-like nodes** (incl. lambdas /
closures, which inherit nesting but are not scored as separate functions
unless named).

Metric computation is then language-neutral; adding a language means one
adapter file plus golden tests.

## Alternatives considered

- **Python `ast` + Dart analyzer (via a Dart subprocess):** precise, but two
  unrelated traversal models and a Dart SDK runtime dependency for the tool.
- **lizard:** multi-language complexity counts, but no nesting/bump detail,
  no uniform AST for smells like Bumpy Road, and Dart support is weak.
- **radon:** Python only.
- **DCM (Dart Code Metrics):** commercial, Dart only.

## Why this won

One parser, robust to syntax errors (tree-sitter yields partial trees, which
matters when an agent's edit is mid-way), fast enough for per-edit hooks,
and the same API for every future language.

## Verified in M1 (tree-sitter 0.26.0, tree-sitter-language-pack 1.25.0)

The language pack's Dart grammar parses Dart 3 (sealed/base/final classes,
records, record/object patterns, switch expressions) and Flutter widget
code with zero ERROR nodes. `tests/test_parser_spike.py` pins the node
names; a grammar upgrade that renames them fails there first.

Dart grammar quirks the adapter must handle:

- **Signature and body are siblings, not parent/child.** Top-level:
  `function_signature` then `function_body` under `program`. In a class:
  `method_signature` (wrapping `function_signature`, `getter_signature`, …)
  then `function_body` in `class_body`. Pair by adjacency.
- **No call-expression node.** `Text('x')` is `identifier` + `selector` →
  `argument_part` → `arguments`. Widget-tree depth = nesting depth of
  `arguments` nodes inside `build()`'s body.
- **Boolean operator precedence is not reliable in the tree shape**
  (`a && b || c` parses as `logical_and_expression` containing
  `logical_or_expression`). Count `logical_and_operator` /
  `logical_or_operator` tokens instead of trusting the tree shape.
- Named params live in `optional_formal_parameters` (`{…}`); positional
  optional in `[…]`. Closures are `function_expression`; collection-if is
  `if_element` (counts as a decision point).

Python grammar: functions `function_definition`, classes
`class_definition`, branches `if_statement`/`elif_clause`, boolean ops
`boolean_operator`, `match_statement` — no surprises.

More Dart shapes, verified in M2:

- Constructor signatures: `constructor_signature` (name is `Repo` or
  `Repo.named` from identifier + `.` children), `factory_constructor_signature`
  (no name field). A constructor with only an initializer list is a
  `declaration` with no following `function_body`, so it isn't scored, but
  closures in its initializers can still declare local functions.
- Abstract methods are `declaration > function_signature` with no body.
- Local functions: `local_function_declaration > lambda_expression >
  (function_signature, function_body)`. Closures are `function_expression`
  and are not scored on their own.
- `required` is the previous sibling token of a named `formal_parameter`;
  `this.x` / `super.x` params are `constructor_param` /
  `super_formal_parameter` (type comes from the field).

Python grammar: newer versions put a docstring as a bare `string` directly
in the block, not wrapped in `expression_statement`. Handle both.

Adapter coverage check (M2): on 2,912 Flutter SDK files (1.39M LoC) the
Dart adapter extracts every `function_body` (60,461), with zero parse
errors at about 60k LoC/s. Python runs at about 135k LoC/s on site-packages.

Pin grammar versions in `pyproject.toml` before M3 golden tests depend on
exact node shapes.

## When to revisit

Tree-sitter grammar quality blocks a language, or a metric genuinely needs
type information (e.g. Primitive Obsession on inferred types).

## Makefiles (added after M11)

- Grammar `make` (tree-sitter-language-pack). `rule` > `targets`,
  `prerequisites` (field `normal`), `recipe` > `recipe_line` > `shell_text`.
  Recipe shell is opaque text, so recipes are re-parsed with the `bash`
  grammar. The script is padded with newlines so bash rows equal Makefile
  rows. Before parsing, `$$` becomes `$`, and `$(...)`/`${...}` (balanced)
  and automatic variables (`$@ $< $^ ...`) become the word `MAKEVAR`. The
  `@`/`-`/`+` prefixes are blanked, not removed, to keep columns.
- Conditionals: `conditional` holds `condition` (an
  `ifeq/ifneq/ifdef/ifndef_directive`), `consequence`, and then
  `elsif_directive` / `else_directive` as plain children, not an
  `alternative` field. Bash `if_statement` likewise has `elif_clause` and
  `else_clause` children. Adapters say which children are else branches via
  `is_alternative()`.
- Make functions: `function_call` with field `function` (`if`, `or`, `and`,
  `foreach`, `call`, ...); `$(shell ...)` is `shell_function`.
- Make nodes end at the start of the next line (they own their newline),
  so a unit's end line drops a final column-0 row.
- **Grammar gaps:** valid GNU make that tree-sitter-make rejects includes
  substitution references with `=` (`$(V:1=)`, `$(SO:=.dSYM)`),
  `obj-$(CONFIG) := x` and `VPATH += :dir`. This affects 17.8 % of 1,284
  hand-written Makefiles sampled. Recovery is line-local, so the adapter
  sets `reports_parse_errors = False` and never reports "syntax errors".
- `define` bodies: make syntax at a line start (directives, `X := ...`,
  `target:`) means the body is parsed as make; otherwise as bash, falling
  back to make on a bash error. Bash alone is too permissive to tell them
  apart: `ifeq (MAKEVAR,debug)` parses without error.
- Coverage: 2,190 Makefiles found on disk (906 generated and skipped), 0
  crashes, about 61k LoC/s. 99.2 % of recipe bodies parse as bash.

## Swift (M13)

- Grammar `swift` (tree-sitter-language-pack). Parses Swift 6 syntax with
  zero errors in the fixture: actors, `async`/`throws(E)`, `~Copyable`,
  `some`/`any`, if/switch expressions, macros (`#Preview`), `#if` blocks
  (flat `directive` siblings, so they never break a declaration).
- struct / class / enum / actor / extension are all `class_declaration`,
  told apart by the `declaration_kind` field. Protocols are
  `protocol_declaration` (requirements have no body, not units).
- Units: `function_declaration`, `init_declaration`, `deinit_declaration`
  (body field `body` = `function_body` > `statements`);
  `property_declaration` / `subscript_declaration` hold a
  `computed_property` (a bare getter: `statements` directly, or
  `computed_getter` / `computed_setter` / `computed_modify`) or a
  `willset_didset_block` (`willset_clause` / `didset_clause`).
- The `name` field labels both a parameter's name and its type: the type is
  the first named child after the `:`, skipping `parameter_modifiers`
  (`inout`, `@escaping`). `external_name` is the call-site label. Default
  values are `default_value` *siblings* after their `parameter`.
- if: no `alternative` field. The else branch is the child after the bare
  `else` token: an `if_statement` (else-if) or a `{` token followed by
  `statements`. Marking the `{` as the else branch counts it once, and the
  `statements` sibling still nests one level inside the if.
- `break outer` / `continue outer` / `return` / `throw` are all
  `control_transfer_statement`. A loop label is a `statement_label`
  *sibling* before the loop, so `statements()` drops it (for Bumpy Road).
- `switch_entry` with a `default_keyword` child is the default arm
  (`@unknown default` too).
- **Precedence is unreliable.** `n > 100 || n == 42` parses as
  `comparison(n, disjunction(100, …))`, `>=` is an `infix_expression` with
  a `custom_operator`, and `n * f(x)` is `call((n * f), x)`. So comparison,
  equality, infix and prefix nodes are `bool_transparent`, operator tokens
  (`&&`, `||`) are counted rather than nodes, and a callee is the rightmost
  `rhs` of the call's first child.
- `!(a || b)` parses as a *call* of `!` with `(a || b)` as its argument.
- **Grammar gaps:** stdlib-internal syntax (`@_lifetime`, `__owned`,
  `__consuming`) and a void argument `f(())` produce ERROR nodes. Files
  with errors: app code 0–2 % (IceCubesApp, SwiftLint, argument-parser,
  Kingfisher), firebase-ios-sdk 3 %, TCA/vapor 6–7 %, swift-nio 22 %,
  swift-collections 28 %. Recovery is local and the adapter scans into
  ERROR nodes for declarations, so `reports_parse_errors` stays on: a
  real syntax error in app code is worth telling an agent.
- Coverage: 5,646 files (firebase-ios-sdk, swift-nio, swift-collections,
  TCA, vapor, SwiftLint, Alamofire, Kingfisher, IceCubesApp, fastlane,
  Flutter iOS code), 0 crashes, about 8 ms per file. 46,160 of 46,179
  bodies extracted (99.96 %); the rest are protocol subscript requirements
  and local computed variables (scored as part of their function).
