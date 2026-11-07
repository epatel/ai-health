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

## Risks / verify in M1

- The Dart grammar must handle Dart 3 syntax (records, patterns, sealed /
  base / final classes, switch expressions). If the language pack's grammar
  lags, build `tree-sitter-dart` from source and pin the commit.
- Grammar node-type names change between grammar versions — pin versions
  in `pyproject.toml` and keep golden tests per adapter.

## When to revisit

Tree-sitter grammar quality blocks a language, or a metric genuinely needs
type information (e.g. Primitive Obsession on inferred types).
