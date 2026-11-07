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

Versions are pinned to the tested minor in `pyproject.toml`
(`tree-sitter>=0.26,<0.27`, `tree-sitter-language-pack>=1.25,<1.26`): a
grammar update renames or reshapes nodes, and adapters then degrade
silently (unknown node types get no role, so complexity drops). Widen a
range only after the spike tests, a before/after snapshot comparison
(`scripts/snapshot.py`) and calibration pass on the new version. The
weekly CI run catches patch releases inside the ranges.

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

## Rust (M14)

- Grammar `rust` (tree-sitter-language-pack). Parses Rust 2024 syntax with
  zero errors in the fixture, including let-chains
  (`if let Some(x) = y && x > 0`), `let … else`, async fns, labeled loops
  and `unsafe` blocks.
- Units: `function_item` (body field `body` = `block`). Methods sit in
  `impl_item` / `trait_item` > `declaration_list`; trait methods without a
  body are `function_signature_item`. `mod_item` with a body nests items.
  Parameters: `parameter` (fields `pattern`, `type`), `self_parameter`,
  `variadic_parameter`.
- **Else-if is wrapped:** `if_expression` field `alternative` is an
  `else_clause` holding either an `if_expression` or a `block`. The walker
  expects the alternative child itself to be the IF node, so adapters can
  now return the inner node from `else_if(node)` (default: none; the walker
  falls back to the child's own role, as before).
- `let_declaration` has an `alternative` block for `let … else`; the
  adapter keeps it out of `is_alternative`, so it scores like Swift
  `guard` (one IF, its block is the body).
- Statements are mostly `expression_statement` wrappers around
  if/loops/match; `unwrap_loop` looks through one for Bumpy Road.
- `binary_expression` has an `operator` field; only `&&`/`||` make it a
  BOOL_EXPR. `let_chain` is a BOOL_EXPR whose `&&` tokens count.
  Precedence is correct in this grammar.
- `break` / `continue` take a `label` child (`'outer`).
- Comments: `line_comment` / `block_comment`; doc comments are the same
  nodes with a `doc_comment` child, so they never count as code.
- Macro invocations keep their contents as a `token_tree`: code inside
  `quote! { }` or `tokio::select! { }` is not parsed into functions.
- Coverage: 4,845 files (the 165 crates in `~/.cargo/registry` plus
  terminal_pad, 1.31M LoC), 0 crashes, 5.9 ms per file, 0.6 % of files
  with parse errors. 56,262 of 56,267 function bodies extracted
  (99.99 %); the 5 misses are `impl` blocks inside `const _: () = { … }`.

## TypeScript (M15)

- Grammars `typescript` (`.ts`, `.mts`, `.cts`) and `tsx` (`.tsx`) from
  tree-sitter-language-pack: two adapters, `TsxAdapter` subclasses
  `TypeScriptAdapter` and adds JSX depth. Zero parse errors in the
  fixtures (decorators, private `#fields`, parameter properties,
  generators, `satisfies`-era syntax, optional chaining).
- Function shapes: `function_declaration`, `generator_function_declaration`,
  `method_definition` (in `class_body` or an `object`), `arrow_function`,
  `function_expression`, `generator_function`. Overload signatures and
  abstract methods have no `body` and are not units.
- **Else-if is wrapped** as in Rust: `if_statement` field `alternative`
  is an `else_clause` holding an `if_statement` or a statement;
  `else_if()` unwraps it.
- `switch_case` / `switch_default`; `try_statement` with `handler`
  (`catch_clause`) and `finalizer` (`finally_clause`); labeled loops are
  `labeled_statement` (field `body`), and `break`/`continue` have a
  `label` field.
- `binary_expression` has an `operator` field: `&&`, `||` are boolean;
  `??` is not. Precedence is correct.
- Parameters: `required_parameter` / `optional_parameter` (fields
  `pattern`, `type` = `type_annotation`, `value`), `rest_pattern` for
  `...rest`; a `this:` parameter is dropped. A single unparenthesised
  arrow parameter is the `parameter` field.
- JSX: `jsx_element` (with `jsx_opening_element` / `jsx_closing_element`),
  `jsx_self_closing_element`, `jsx_expression` for `{...}`.
- Coverage: 12,667 files (zod, TanStack Query, tRPC, date-fns, Vite, ky,
  TypeORM, Excalidraw, Nest; 1.22M LoC), 0 crashes, 3.3 ms per file,
  0.3 % of files with parse errors. All 18,402 function declarations and
  class methods extracted. Of 104k function expressions, 48.6k are named
  units (bound to a name, or test closures) and 55.4k inline callbacks.

## JavaScript (M16)

- Grammar `javascript` (`.js`, `.mjs`, `.cjs`, `.jsx`; JSX is part of the
  grammar). Node names match the TypeScript grammar, so `JavaScriptAdapter`
  subclasses `TsxAdapter`. Differences, handled in `lang/typescript.py`
  where they can't occur in TypeScript (so TypeScript results don't change):
  parameters are bare patterns (`identifier`, `assignment_pattern`,
  `rest_pattern`, `object_pattern`, `array_pattern`) instead of
  `required_parameter`; class fields are `field_definition` (field
  `property`) instead of `public_field_definition`.
- Functions assigned to a name (`assignment_expression` field `right`) are
  units in JavaScript only (`assigned_units`), named by the `left` text with
  `.prototype.` dropped.
- Coverage: 22,741 files (Express, axios, lodash, Preact, webpack, ESLint,
  three.js incl. tests; 1.46M LoC), 0 crashes, 2.8 ms per file. All 26,484
  declarations and class methods extracted. The 350 files with parse errors
  (1.5 %) are all test fixtures: webpack hot-update cases with `---`
  separators and ESLint's deliberately broken inputs.

## CMake (M17)

- Grammar `cmake` (tree-sitter-language-pack): `CMakeLists.txt` (matched by
  name) and `*.cmake`. Commands are case-insensitive in the grammar too.
- Definitions: `function_def` / `macro_def` = `function_command` (first
  argument is the name, the rest are parameters) + `body` + end command.
  Blocks: `if_condition`, `foreach_loop`, `while_loop`, `block_def`.
- **`if` is flat:** `if_command`, `body`, `elseif_command`, `body`,
  `else_command`, `body`, `endif_command` are siblings in `if_condition`.
  `is_alternative` marks the elseif/else commands; `elseif_command` has the
  IF role, so the walker scores it as an else-if, and each following `body`
  nests one level inside the if, as it should.
- Conditions are `argument_list`s of the if/elseif/while command: `AND` /
  `OR` are plain `unquoted_argument`s, counted as words. The command nodes
  are `bool_transparent`, so a condition is recognised as the block's.
- Comments: `line_comment`, `bracket_comment` (`#[[ ... ]]`, any number of
  `=`).
- Coverage: 956 hand-written files (OrcaSlicer incl. its deps, Pico SDK,
  Flutter, OpenSCAD, Xcode package checkouts), 0 crashes, about 5 ms per
  file, all 639 function/macro definitions extracted. Two files fail to
  parse: a `configure_file()` template (`*.h.cmake`, a C header; now
  excluded by default) and OrcaSlicer's `FindOpenVDB.cmake`, which the
  grammar rejects as a whole (cause not isolated; every single command in
  it parses).

## C, C++ and Objective-C (M18)

- **One adapter, three grammars** (`c`, `cpp`, `objc`), picked per file in
  `lang/cfamily.py`: `.c` → c, `.cpp/.cc/.cxx/.hpp/.hh/.hxx` → cpp, `.m` →
  objc, and `.h` by content. Objective-C if a line starts with
  `@interface`/`@protocol`/`@implementation`/`@end`/`#import`; C++ if it
  has `namespace`, `template <`, `class X {`/`class X :`, `struct X :`,
  an access specifier or `\w::\w`; otherwise C. (`struct X {` is not a
  marker: C has it too.) The registry still routes by extension; base
  `LanguageAdapter` grew `grammars`, `_grammar_for()` and `_prepare()`
  for this, and the chosen grammar is `module.language`.
- **Router measurement** (9,463 headers from Pico SDK, Flipper, OrcaSlicer,
  the Flutter engine and Xcode package checkouts): 6,476 → c, 1,850 → cpp,
  1,137 → objc. Error lines under the router 3.6 % vs 2.9 % if every
  non-ObjC header used `cpp`. The headers C++ parses better are
  macro-heavy C (CMSIS `cmsis_gcc.h`, mlib), not misroutes; kept the router
  so C headers report as C.
- **Prepared source** (`cfamily.prepare`, same byte offsets, so node text
  and lines refer to the file):
  1. `#if/#ifdef/#ifndef/#elif/#else/#endif` lines (with continuations)
     become spaces. The grammars parse a conditional block only when each
     branch is complete; a branch that opens a brace or splits a statement
     turned the whole block, often the whole file, into one ERROR
     (lwIP `sockets.c`: 4,165 of 4,206 lines). Both branches now parse as
     one sequence. C files with errors 47 % → 22 %, error lines 7.0 % →
     2.0 % (14k files, Pico SDK + Flipper). A branch that duplicates an
     opening brace still misnests what follows: accepted. Side effect: the
     blanked directive lines don't count as code lines (LoC).
  2. `typedef NS_ENUM(T, Name)` (also `NS_OPTIONS`, `NS_CLOSED_ENUM`,
     `NS_ERROR_ENUM`, `CF_*`) becomes `enum Name`, without `typedef`
     (which would need a name after the body). Objective-C files with
     errors 36 % → 15 %, error lines 7.1 % → 1.4 %.
  3. A line holding only an ALL_CAPS identifier (`Q_OBJECT`,
     `ABSL_NAMESPACE_BEGIN`, `NS_ASSUME_NONNULL_BEGIN`,
     `PACK_STRUCT_BEGIN`) becomes spaces; the grammars read it as the
     start of a declaration and lost the class around it. C++ files with
     errors 26.5 % → 20.8 %, Objective-C error lines 1.3 % → 0.7 %. At worst
     it drops a last enumerator written without a comma.
- **Parse errors are not reported** (`reports_parse_errors = False`): after
  preparation, files with any error are still C 24 %, C++ 21 %,
  Objective-C 9.5 %, covering 1.8 / 1.8 / 0.7 % of lines. They are local:
  attribute macros (`PLACE_IN_SECTION("x") ALIGN(4) static ...`),
  table and token-pasting macros, inline asm.
- **Shared statement nodes**: `if_statement` with `alternative:
  else_clause` (unwrapped by `else_if()`), `case_statement` (`default:` has
  no `value` field), `goto_statement` (JUMP_LABEL: +1 cognitive),
  `labeled_statement`, `conditional_expression`, `compound_statement`.
  C++ wraps conditions in `condition_clause` (bool-transparent), adds
  `for_range_loop`, `try_statement > catch_clause`, `lambda_expression`;
  Objective-C adds `@try` as `try_statement > catch_clause /
  finally_clause` and blocks as `block_literal`.
- **Declarators** (`lang/cfamily_decl.py`): name and parameters sit in the
  *innermost* `function_declarator`, under pointer / reference /
  parenthesized declarators; `int (*pick(int w))(int)` has an outer
  declarator holding the return type's parameters, and `int (f)(int)`
  parenthesizes the name. In-class method names are `field_identifier`,
  out-of-line ones `qualified_identifier` (`A::B::~C` → `A.B.~C`, template
  arguments dropped, `operator<` kept).
- **Objective-C methods have no fields** (`lang/cfamily_objc.py`): the
  `-`/`+` marker is an anonymous token, selector pieces are the direct
  `identifier` children, parameters are `method_parameter` children, the
  body is a `compound_statement`. Categories are `class_implementation`
  with a `category` field (kind `extension`).
- **Misparses recovered by extraction**: a `function_definition` without a
  function declarator is a loop macro (`NETIF_FOREACH(n) { ... }`): its
  body is code of the enclosing function, not a unit. At file level the
  same shape is `class EXPORT_MACRO C { ... }`: its body is scanned for
  members (named `C.f`; the first member after `public:` is folded into a
  labeled statement and lost). A stray top-level `compound_statement`, a
  struct body in a definition's type field, and `friend` definitions are
  scanned too.
- **Coverage** (24,900 files, 4.2M LoC: Pico SDK, Flipper, OrcaSlicer, the
  Flutter engine, Xcode checkouts and the calibration clones), 0 crashes,
  about 10 ms per file with duplication. Definitions outside ERROR nodes
  extracted: C 96,652 of 96,652, C++ 94,603 of 94,682 (the rest sit in
  misparsed labeled statements), Objective-C 13,476 of 13,479. Inside
  ERROR nodes, and lost: C 1.0 %, C++ 0.7 %, Objective-C 0.2 % of
  definitions.
- **ERROR nodes are scanned** for units (`_add_error`): the grammars keep
  intact definitions as ERROR children. An `@implementation` token there
  opens a recovered class (up to `@end`), so methods keep `Foo.` and
  cohesion (`objc.member_refs` accepts the ERROR node). Recovered: C 993
  of 993 definitions inside ERROR nodes, C++ 663 of 675, Objective-C 26 of
  26.

## Objective-C++ (added after M18)

- **No grammar of its own.** On 352 `.mm` files (Flutter engine,
  OrcaSlicer, Xcode checkouts; 113k lines) after `prepare()`: the `objc`
  grammar leaves 24 % of lines in ERROR nodes, `cpp` 73 %. So `.mm`, and
  `.h` files with both Objective-C and C++ markers, are language `objcpp`,
  parsed by the `objc` parser (`_parsers["objcpp"]`).
- **`a::b` → `a__b`** (same length, one identifier; also a leading `::x`,
  not `foo::)` in selectors): error lines 24 % → 14 %. Names, class names
  and parameter types are read from `module.source`
  (`cfamily_decl.source_text`), so `int Cart::total()` is `Cart.total`
  with type `const std::vector<int>&`.
- **Tried and dropped** (measured, no gain or worse): blanking
  `namespace X` (its block becomes a stray compound statement), blanking
  template argument lists, and rewriting lambda introducers `[x](` to
  block carets `^(` (lost lines 8.4 % → 8.9 %).
- **Remaining errors**: lambdas, templates, `auto`, braced initializer
  lists in message arguments (`newWithView:{...}`), designated
  initializers; a few test files fail as a whole. With ERROR scanning,
  4,863 units are found for 4,878 recognised definitions, but 8–10 % of
  `.mm` code lines (Flutter/OrcaSlicer/firebase 8.4 %, Texture and
  ComponentKit 9.6 %) sit in ERROR nodes outside any unit and are not
  scored. C++, for comparison: 0.3 %; Objective-C 0.1 %.

## Shell scripts (M19)

- Grammar `bash` (tree-sitter-language-pack), already used for Makefile
  recipes; `lang/shell.py` owns the bash role table and `make.py` imports
  it. Files: `*.sh`, `*.bash`, and extensionless files whose first line is
  a `sh`/`bash`/`dash`/`ksh` shebang (also `env -S bash -e`). The registry
  gets the file's first bytes as `head`; paths alone can't tell, so
  `is_supported(path)` without a head says no for `bin/deploy`.
- Shebang scripts matter: on this machine's corpora there were 2,200
  extensionless bash/sh scripts next to 3,100 `.sh`/`.bash` files, and they
  hold the main code of rbenv, pyenv, dokku, n and dehydrated.
- zsh: see the next section (the bash grammar alone fails on 55 % of the
  lines of `.zsh` files).
- Quirks: `until` is a `while_statement` (first child token `until`);
  `elif_clause` / `else_clause` are `if_statement` children without a field;
  a `case_item` with pattern `*` is the default arm; `a && b || c` is a
  `list`, `[[ a && b ]]` a `binary_expression`; `continue 2` is a plain
  `command` with a number argument (shell-only JUMP_LABEL).
- **Case-pattern gap**: a quoted segment right after a glob inside a word
  (`--only=*' '*)`, `x*' 'y)`) is rejected and recovery lost the rest of
  nvm.sh, including its 4,000-line `nvm()`. `_prepare()` turns that segment
  into underscores when it is followed by `*`, `)` or `|` (18 places in
  5.5k files; error lines 8.4 % → 7.9 %). Matching every `*"...` instead
  made things worse (8.4 % → 9.8 %): it re-pairs quotes of ordinary strings.
- **Parse errors are not reported**: files with any error are 1.5 %
  (`.bash`), 2.8 % (`.sh`) and 0.7–1.5 % (shebang scripts), from grammar
  gaps such as `$` inside backticks (`for f in \`grep txt$\``).
- Generated scripts are skipped by marker: "Generated by GNU Autoconf"
  (`configure`: two copies of wxWidgets' 44k-line script were 89 % of all
  error lines), "Generated by configure", "generated from ltmain".
- Coverage: 5,500 scripts (the calibration clones plus `~/Development`),
  0 crashes, every `function_definition` extracted.

## zsh (added after M19)

- Same adapter (`ShellAdapter`, module language `zsh`), grammar `zsh`
  from tree-sitter-language-pack: a tree-sitter-bash fork with bash's node
  and field names plus zsh nodes (`repeat_statement`, `select_statement`,
  `terse_for_statement` for `for x (a b) {...}`, `always_clause`, anonymous
  `function_definition` without a name, expansion flags and glob
  qualifiers). Routed: `*.zsh`, `*.zsh-theme`, zsh shebangs, and
  extensionless files starting with `#compdef` / `#autoload` (completion
  and autoload functions, e.g. all of zsh-completions).
- **The zsh grammar can loop forever**, growing to gigabytes: `)` in the
  replacement of `${a/b/)}`, `(…)` after a nested expansion there
  (`${a/b/${c}(d)}`), and at least one more case in powerlevel10k
  (`${${${(q)a}/#(#b)${(q)H}(|\/*)/'~'$m[1]}//\%/%%}`) that wasn't narrowed
  down. 4 of 1,158 files hit it. py-tree-sitter 0.26's `progress_callback`
  (which could cancel) segfaults with these grammars, and there is no
  `timeout_micros` any more, so `ZshParser` first parses in a forked child
  limited to 1 CPU second (`RLIMIT_CPU`; about 2 ms per normal file, a
  hang costs 1 s and about 1 GB in the child) and 3 s of wall-clock time
  (`SIGALRM`, for a child that blocks without using CPU, e.g. on a lock
  held by another thread at fork time: the MCP server and the dashboard
  are multi-threaded). If the child doesn't finish,
  or there's no `fork` (Windows), the bash tree is used.
  `prepare_zsh` turns parens in `${name/pattern/replacement}` into `_`
  (removes 3 of the 4 hangs) and blanks extra `for k v in` variables (at
  most 8). It runs in linear time: `${`/`}` pairs are matched in one
  regex pass, the flag and subscript patterns are length-bounded, and
  nested parts are merged before rewriting (a 40 KB line of `${` took
  73 s with a scan per `${`).
- **Best of two trees**: neither grammar is good on zsh. Measured as lost
  lines (tokens directly in ERROR nodes; statements recovered inside an
  ERROR count as placed) on ohmyzsh and 8 zsh repos (163k lines): bash
  12.7 %, zsh 16.4 %, the better tree per file 6.4 %. The zsh grammar wins
  on zsh-completions (0.9 % vs 7.4 %), prezto, zsh-z; bash on
  powerlevel10k and zinit, whose quoting the zsh grammar mis-pairs. So
  `ZshParser` keeps the zsh tree when it loses no lines, else the one
  losing fewer. A file may switch trees between two versions; node types
  agree, so metrics barely move.
- Coverage through the adapter: 1,158 zsh files, 0 crashes, 3.1 % of lines
  lost, 3,680 of about 4,388 `function` definitions (by regex) found;
  powerlevel10k's 9,500-line `p10k.zsh` alone misses 277 (a quote
  mis-pairing early on leaves its function headers as loose tokens).
  Recovering units from ERROR nodes, as the C family does, was not done.
- Not understood by the zsh grammar: short forms `if [[ x ]] { ... }`,
  `while x { ... }`, `}` right after a command without `;` (`{ echo }`
  inside a function), `[[ $a == (x|y)* ]]`.
