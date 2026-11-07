"""Shell script adapter: bash (`*.sh`, `*.bash`, sh/bash/dash/ksh shebangs)
with the `bash` grammar, zsh (`*.zsh`, `*.zsh-theme`, zsh shebangs,
`#compdef` / `#autoload` functions) with the `zsh` grammar, guarded and
compared against bash (see `shell_zsh.py`).

Units, mirroring the CMake adapter:
- ``function``: `f() { ... }` / `function f { ... }`, wherever it is
  defined (also inside top-level `if` blocks or other functions). Shell
  functions are global, so they are named plainly (`f`, a redefinition
  `f#2`);
- ``conditional`` / ``loop`` / ``case``: a top-level `if`, `for`, `while`,
  `until` or `case` block (zsh also `repeat`, `select`, `for x (a b)`),
  named by its first line. Plain top-level commands are not scored, like
  CMake's;
- zsh's anonymous functions (`() { ... }`, run where defined) are
  ``function`` units named `(anonymous)`.

Grammar notes (see cards/parser-choice.md): `elif_clause` / `else_clause`
are children of `if_statement` without a field; `until` is a
`while_statement`; a `case_item` whose pattern is `*` is the default arm;
`a && b || c` is a `list`, `[[ a && b ]]` a `binary_expression`. The bash
roles are shared with the Makefile adapter (recipes are parsed as bash).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

from tree_sitter import Node

from aihealth.lang.base import LanguageAdapter, text, walk_units
from aihealth.lang.shell_zsh import ZSH_EXTENSIONS, ZshParser, is_zsh_head, prepare_zsh
from aihealth.metrics import Role
from aihealth.model import Function, Module

BASH_ROLES: dict[str, Role] = {
    "if_statement": Role.IF,
    "elif_clause": Role.IF,  # reached as an alternative -> else-if
    "for_statement": Role.LOOP,
    "c_style_for_statement": Role.LOOP,
    "while_statement": Role.LOOP,  # also `until`
    "case_statement": Role.SWITCH,
    "case_item": Role.CASE,  # except `*)`, see bash_role
    "ternary_expression": Role.TERNARY,
    "function_definition": Role.STOP,
    "list": Role.BOOL_EXPR,  # `a && b || c`
    "binary_expression": Role.BOOL_EXPR,  # `[[ a && b ]]` (no ops in arithmetic)
}
# zsh-only node types (the zsh grammar keeps bash's names for the rest)
SHELL_ROLES: dict[str, Role] = BASH_ROLES | {
    "terse_for_statement": Role.LOOP,  # `for x (a b) { ... }`
    "repeat_statement": Role.LOOP,
    "select_statement": Role.LOOP,
}
BASH_ALTERNATIVES = frozenset({"elif_clause", "else_clause"})
BASH_BLOCKS = frozenset({"do_group", "compound_statement"})
BASH_BOOL_TRANSPARENT = frozenset({"negated_command", "subshell"})


def bash_role(node: Node, roles: dict[str, Role]) -> Role | None:
    """``roles`` for ``node``, except that the `*)` case arm is the default."""
    if node.type == "case_item":
        value = node.child_by_field_name("value")
        return None if value is not None and text(value).strip() == "*" else Role.CASE
    return roles.get(node.type)


def bash_bool_token(node: Node) -> str | None:
    if node.type in ("&&", "||") and node.parent is not None:
        return "and" if node.type == "&&" else "or"
    return None


_BLOCKS = {
    "if_statement": "conditional",
    "for_statement": "loop",
    "c_style_for_statement": "loop",
    "while_statement": "loop",
    "case_statement": "case",
    "terse_for_statement": "loop",
    "repeat_statement": "loop",
    "select_statement": "loop",
}
ANONYMOUS = "(anonymous)"
_NAME_LIMIT = 60
# A quoted segment right after a glob in a word (`--only=*' '*)`): the grammar
# rejects it in a case pattern and loses the rest of the file (nvm's 4,000-line
# `nvm()`). The pattern's text doesn't matter for metrics.
_GLOB_QUOTE = re.compile(rb"(?<=[\w=-]\*)('[^'\n]*'|\"[^\"\n]*\")(?=[*)|])")

_HINTS = {
    "large_method": "Split into smaller functions, or move a self-contained part into its "
    "own script.",
    "complex_method": "Extract the branches into functions; replace option chains with a "
    "`case` that calls one function per option.",
    "cognitive_complexity": "Return or exit early instead of nesting, and move nested "
    "branches into functions.",
    "deep_nesting": "Flatten with early `return`/`continue`/`exit`, or move the inner loop "
    "into a function.",
    "code_duplication": "Move the repeated commands into a function and call it with the "
    "parts that differ as arguments.",
}


def _is_multilevel_jump(command: Node) -> bool:
    """`continue 2` / `break 2`: bash's labeled jump (+1 cognitive)."""
    name = text(command.child_by_field_name("name"))
    return name in ("break", "continue") and command.child_by_field_name("argument") is not None


def _underscores(match: re.Match[bytes]) -> bytes:
    return b"_" * len(match.group(0))


def _line(node: Node) -> int:
    return node.start_point[0] + 1


def _end_line(node: Node) -> int:
    return node.end_point[0] + 1


def _block(node: Node) -> Node | None:
    """A top-level block, also when redirected (`while read l; do ...; done < f`)."""
    if node.type == "redirected_statement":
        node = node.child_by_field_name("body") or node
    return node if node.type in _BLOCKS else None


def _block_name(block: Node) -> str:
    """The block's first line without `; then` / `; do`, shortened."""
    first = text(block).split("\n", 1)[0].strip()
    for tail in ("; then", ";then", "; do", ";do"):
        first = first.removesuffix(tail)
    first = " ".join(first.split())
    return first if len(first) <= _NAME_LIMIT else first[: _NAME_LIMIT - 3] + "..."


class ShellAdapter(LanguageAdapter):
    language = "bash"
    grammars = ("zsh",)
    extensions = (".sh", ".bash", *ZSH_EXTENSIONS)
    roles: ClassVar[dict[str, Role]] = SHELL_ROLES
    # the corpora are working scripts: their parse errors (2 % of files) are
    # grammar gaps, such as `$` inside backticks, not syntax errors
    reports_parse_errors = False
    block_types = BASH_BLOCKS | {"compound_statement_no_always"}
    bool_transparent = BASH_BOOL_TRANSPARENT
    literal_types = frozenset({"string", "raw_string", "ansi_c_string", "number", "heredoc_body"})

    def __init__(self) -> None:
        super().__init__()
        self._parsers["zsh"] = ZshParser()

    def _grammar_for(self, source: bytes, path: str) -> str:
        zsh = Path(path).suffix in ZSH_EXTENSIONS or is_zsh_head(source)
        return "zsh" if zsh else "bash"

    def _prepare(self, source: bytes, language: str) -> bytes:
        if b"*" in source:
            source = _GLOB_QUOTE.sub(_underscores, source)
        return prepare_zsh(source) if language == "zsh" else source

    # --- metric hooks ------------------------------------------------------------

    def role(self, node: Node) -> Role | None:
        if node.type == "command" and _is_multilevel_jump(node):
            return Role.JUMP_LABEL
        return bash_role(node, self.roles)

    def is_alternative(self, node: Node, index: int) -> bool:
        return node.children[index].type in BASH_ALTERNATIVES

    def bool_token(self, node: Node) -> str | None:
        return bash_bool_token(node)

    def is_recursive_call(self, node: Node, fn_name: str) -> bool:
        if node.type != "command":
            return False
        return text(node.child_by_field_name("name")) == fn_name

    def hint(self, smell: str) -> str | None:
        return _HINTS.get(smell)

    # --- extraction ---------------------------------------------------------------------

    def _extract(self, module: Module) -> None:
        for node in module.root.named_children:
            block = _block(node)
            if block is not None:
                self._add_block(module, block)
            elif node.type == "function_definition":
                self._add_function(module, node)
            else:  # `cond && f() { ...; }`, functions in a top-level list
                walk_units(node, lambda n: self._add_nested_function(module, n))

    def _add_block(self, module: Module, block: Node) -> None:
        name = _block_name(block)
        module.functions.append(
            Function(
                name=name,
                qualname=name,
                kind=_BLOCKS[block.type],
                start_line=_line(block),
                end_line=_end_line(block),
                params=[],
                body=block,
            )
        )
        walk_units(block, lambda n: self._add_nested_function(module, n))

    def _add_nested_function(self, module: Module, node: Node) -> bool:
        if node.type != "function_definition":
            return False
        self._add_function(module, node)
        return True

    def _add_function(self, module: Module, node: Node) -> None:
        body = node.child_by_field_name("body")
        if body is None:
            return
        name = text(node.child_by_field_name("name")) or ANONYMOUS
        module.functions.append(
            Function(
                name=name,
                qualname=name,
                kind="function",
                start_line=_line(node),
                end_line=_end_line(node),
                params=[],
                body=body,
            )
        )
        walk_units(body, lambda n: self._add_nested_function(module, n))
