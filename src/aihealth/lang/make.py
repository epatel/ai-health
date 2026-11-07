"""Makefile adapter (tree-sitter `make` grammar, recipes via `bash`).

Scored units (`Function.kind`):
- ``rule``: a rule with a recipe, named by its targets (`all`, `%.o`).
  Its body is the recipe parsed as **bash**, because recipe shell code is
  opaque text to the make grammar and is where Makefiles get complex.
- ``define``: a `define NAME ... endef` block, its body also parsed as bash
  (canned recipes are the common use).
- ``conditional``: a top-level `ifeq/ifneq/ifdef/ifndef` block, named by its
  condition line. Complexity comes from the make conditionals and make
  functions in it.
- ``variable``: a top-level assignment whose value uses make control
  functions (`$(if)`, `$(or)`, `$(and)`, `$(foreach)`).

Recipe scripts are line-aligned with the Makefile (padded with newlines), so
every line a metric reports is a real Makefile line. Make references
(`$(VAR)`, `$(fn ...)`, `$@`) become a placeholder word before bash parsing,
and `$$` becomes `$`. Rules without recipes and plain assignments have
nothing to score, like module-level constants elsewhere.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import ClassVar

from tree_sitter import Node
from tree_sitter_language_pack import get_parser

from aihealth.lang.base import LanguageAdapter, LanguageSmell, iter_nodes, text
from aihealth.lang.make_phony import missing_phony
from aihealth.lang.shell import (
    BASH_ALTERNATIVES,
    BASH_BLOCKS,
    BASH_BOOL_TRANSPARENT,
    BASH_ROLES,
    bash_bool_token,
    bash_role,
)
from aihealth.metrics import Role
from aihealth.model import Function, Module

_MAKE_CONTROL = {"if": Role.TERNARY, "or": Role.TERNARY, "and": Role.TERNARY}
_MAKE_LOOPS = {"foreach"}
_PLACEHOLDER = "MAKEVAR"
_AUTO_VARS = re.compile(r"\$[@<^*?+|%]")
_TOKEN = re.compile(r"\$\(|\w+|[^\s\w]")
_MAKE_LINE = re.compile(
    r"^\s*(?:(?:ifeq|ifneq|ifdef|ifndef|else|endif|include|-include|sinclude|override"
    r"|export|unexport|vpath)\b"  # directives
    r"|[\w.$()%/-]+\s*(?::=|::=|\+=|\?=|!=|=)\s"  # VAR := value
    r"|[\w.$()%/-]+\s*::?(?:\s|$))",  # target: prerequisites
    re.MULTILINE,
)

_HINTS = {
    "large_method": "Move the long recipe into a script file (e.g. scripts/<name>.sh) and call "
    "it, or split it into smaller targets that depend on each other.",
    "complex_method": "Move the shell logic into a script file where it can be read and "
    "tested; keep recipes as short command lists.",
    "cognitive_complexity": "Move the shell logic into a script file; for make conditionals, "
    "compute variables once at the top instead of nesting ifeq blocks.",
    "deep_nesting": "Flatten the nested if/for shell (or ifeq) blocks: early `exit`, a script "
    "file, or separate targets.",
    "bumpy_road": "Each nested shell chunk is a separate step: make it its own target or script.",
    "code_duplication": "Use a pattern rule (%.o: %.c), a `define` canned recipe called with "
    "$(call ...), or a shared variable instead of copying recipes.",
    "large_file": "Split the Makefile into included fragments by concern (include mk/*.mk).",
}


def _neutralise(script: str) -> str:
    """Make-isms to something bash parses: `$$` -> `$`, `$(...)`/`${...}`
    (balanced) and automatic variables -> a placeholder word."""
    out: list[str] = []
    i = 0
    while i < len(script):
        ch = script[i]
        if ch == "$" and script[i + 1 : i + 2] == "$":
            out.append("\0")  # a literal shell `$`, restored below
            i += 2
            continue
        if ch == "$" and script[i + 1 : i + 2] in ("(", "{"):
            i = _skip_balanced(script, i + 1)
            out.append(_PLACEHOLDER)
            continue
        out.append(ch)
        i += 1
    return _AUTO_VARS.sub(_PLACEHOLDER, "".join(out)).replace("\0", "$")


def _skip_balanced(s: str, open_at: int) -> int:
    """Index just after the bracket that closes the one at ``open_at``."""
    pair = {"(": ")", "{": "}"}[s[open_at]]
    depth = 0
    for j in range(open_at, len(s)):
        if s[j] == s[open_at]:
            depth += 1
        elif s[j] == pair:
            depth -= 1
            if depth == 0:
                return j + 1
    return len(s)


def _aligned(chunks: list[tuple[int, str]]) -> bytes:
    """Join (0-based row, text) chunks into a script whose line N is file line N."""
    parts: list[str] = []
    row = 0
    for start, chunk in chunks:
        parts.append("\n" * max(0, start - row))
        parts.append(chunk)
        row = max(row, start) + chunk.count("\n")
    return "".join(parts).encode()


def _child(node: Node, kind: str) -> Node | None:
    return next((c for c in node.named_children if c.type == kind), None)


def _words(node: Node | None) -> str:
    return " ".join(text(node).split()) if node is not None else ""


def _recipe_chunks(recipe: Node) -> list[tuple[int, str]]:
    """(row, text) per recipe line; the `@`/`-`/`+` prefixes are blanked
    (not removed) so columns stay where they were."""
    chunks = []
    for line in recipe.named_children:
        if line.type == "recipe_line":
            body = text(line)
            stripped = body.lstrip("@-+")
            chunks.append((line.start_point[0], " " * (len(body) - len(stripped)) + stripped))
    return chunks


def _unit(outer: Node, name: str, kind: str, body: Node) -> Function:
    """A scored unit spanning ``outer``. Make nodes end at the start of the
    next line (they own their newline); that line is not part of the unit."""
    end_row, end_col = outer.end_point
    return Function(
        name=name or f"<{kind}>",
        qualname=name or f"<{kind}>",
        kind=kind,
        start_line=outer.start_point[0] + 1,
        end_line=max(outer.start_point[0] + 1, end_row + (1 if end_col else 0)),
        params=[],
        body=body,
    )


def _looks_like_make(body: str) -> bool:
    """Does a define body contain make syntax (directives, assignments,
    rule lines) rather than only shell commands?"""
    return _MAKE_LINE.search(body) is not None


def _uses_control_function(node: Node) -> bool:
    for n in iter_nodes(node):
        if n.type == "function_call":
            name = text(n.child_by_field_name("function"))
            if name in _MAKE_CONTROL or name in _MAKE_LOOPS:
                return True
    return False


class MakeAdapter(LanguageAdapter):
    language = "make"
    extensions = (".mk", ".mak", ".make")
    filenames = ("Makefile", "makefile", "GNUmakefile", "BSDmakefile")
    # make and bash share these node names
    comment_types = frozenset({"comment"})
    block_types = BASH_BLOCKS
    bool_transparent = BASH_BOOL_TRANSPARENT
    roles: ClassVar[dict[str, Role]] = {
        # make
        "conditional": Role.IF,
        "elsif_directive": Role.IF,  # reached as an alternative -> else-if
        "rule": Role.STOP,  # rules inside a conditional block are units of their own
        "define_directive": Role.STOP,
        # bash (recipes and define bodies)
        **BASH_ROLES,
    }
    # tree-sitter-make rejects some valid GNU make (substitution references
    # with `=`, `obj-$(CONFIG) := ...`, `VPATH += :dir`): ~20 % of hand-written
    # Makefiles in a 2,190-file sample. Recovery keeps the damage to a line,
    # so don't alarm agents with "syntax errors"; make rejects real ones.
    reports_parse_errors = False
    _ALTERNATIVES = frozenset({"elsif_directive", "else_directive"}) | BASH_ALTERNATIVES

    def __init__(self) -> None:
        super().__init__()
        self._bash = get_parser("bash")

    # --- metric hooks ------------------------------------------------------------

    def role(self, node: Node) -> Role | None:
        if node.type == "function_call":
            name = text(node.child_by_field_name("function"))
            return Role.LOOP if name in _MAKE_LOOPS else _MAKE_CONTROL.get(name)
        return bash_role(node, self.roles)

    def is_alternative(self, node: Node, index: int) -> bool:
        return node.children[index].type in self._ALTERNATIVES or super().is_alternative(
            node, index
        )

    def bool_token(self, node: Node) -> str | None:
        return bash_bool_token(node)

    def statements(self, body: Node) -> list[Node]:
        if body.type == "program" or body.type in self.block_types:
            return [c for c in body.named_children if c.type not in self.comment_types]
        return []  # make conditionals/variables: no bumpy-road chunks

    def hint(self, smell: str) -> str | None:
        return _HINTS.get(smell)

    def language_smells(self, module: Module) -> list[LanguageSmell]:
        return missing_phony(module.root)

    def tokens(self, root: Node) -> Iterator[tuple[str, int]]:
        """Words and symbols of the raw text (recipes are opaque to the make
        tree), comments removed, numbers collapsed."""
        for row, line in enumerate(text(root).split("\n")):
            for tok in _TOKEN.findall(line.split("#", 1)[0]):
                yield ("NUM" if tok.isdigit() else f"t:{tok}"), row + 1

    # --- extraction ------------------------------------------------------------------

    def _extract(self, module: Module) -> None:
        self._trees = []  # bash trees must outlive this call: nodes reference them
        module.make_trees = self._trees  # type: ignore[attr-defined]
        for node in module.root.named_children:
            self._top_level(module, node)

    def _top_level(self, module: Module, node: Node) -> None:
        if node.type == "conditional":
            name = _words(node.child_by_field_name("condition")) or "<conditional>"
            module.functions.append(_unit(node, name, "conditional", node))
            for inner in iter_nodes(node):
                if inner.type in ("rule", "define_directive") and inner is not node:
                    self._nested(module, inner)
        elif node.type == "variable_assignment" and _uses_control_function(node):
            name = text(node.child_by_field_name("name"))
            module.functions.append(_unit(node, name, "variable", node))
        else:
            self._nested(module, node)

    def _nested(self, module: Module, node: Node) -> None:
        if node.type == "rule":
            self._add_rule(module, node)
        elif node.type == "define_directive":
            self._add_define(module, node)

    def _add_rule(self, module: Module, rule: Node) -> None:
        recipe = _child(rule, "recipe")
        if recipe is not None:
            body = self._bash_body(_recipe_chunks(recipe))
            module.functions.append(_unit(rule, _words(_child(rule, "targets")), "rule", body))

    def _add_define(self, module: Module, define: Node) -> None:
        value = define.child_by_field_name("value")
        if value is None:
            return
        chunk = [(value.start_point[0], text(value))]
        # make templates (expanded with $(eval ...)) vs canned shell recipes;
        # bash is too permissive to tell them apart by parse errors alone
        body = None if _looks_like_make(chunk[0][1]) else self._bash_body(chunk)
        if body is None or body.has_error:
            body = self._make_body(chunk)
        name = text(define.child_by_field_name("name"))
        module.functions.append(_unit(define, name, "define", body))

    def _make_body(self, chunks: list[tuple[int, str]]) -> Node:
        tree = self._parser.parse(_aligned(chunks))
        self._trees.append(tree)
        return tree.root_node

    def _bash_body(self, chunks: list[tuple[int, str]]) -> Node:
        tree = self._bash.parse(_aligned([(r, _neutralise(t)) for r, t in chunks]))
        self._trees.append(tree)
        return tree.root_node
