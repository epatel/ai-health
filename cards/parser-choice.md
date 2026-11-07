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

Pin grammar versions in `pyproject.toml` before M3 golden tests depend on
exact node shapes.

## When to revisit

Tree-sitter grammar quality blocks a language, or a metric genuinely needs
type information (e.g. Primitive Obsession on inferred types).
