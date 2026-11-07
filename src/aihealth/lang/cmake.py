"""CMake adapter (tree-sitter `cmake` grammar): CMakeLists.txt and *.cmake.

Units, mirroring the Makefile adapter:
- ``function`` / ``macro``: `function(name args...)` ... `endfunction()`,
  wherever they are defined (also inside top-level `if` blocks);
- ``conditional`` / ``loop``: a top-level `if`, `foreach` or `while` block,
  named by its header (`if(WIN32 AND NOT MINGW)`). Plain top-level commands
  (`set`, `add_library`) are not scored, like Make assignments.

Grammar notes (see cards/parser-choice.md): an `if_condition` is flat: the
`if_command`, then `body`, `elseif_command`, `body`, `else_command`, `body`
and `endif_command` are siblings. Conditions are plain argument lists, so
`AND` / `OR` are counted as words.
"""

from __future__ import annotations

from typing import ClassVar

from tree_sitter import Node

from aihealth.lang.base import LanguageAdapter, text, walk_units
from aihealth.metrics import Role
from aihealth.model import Function, Module, Param

_DEFINITIONS = {"function_def": "function", "macro_def": "macro"}
_BLOCKS = {"if_condition": "conditional", "foreach_loop": "loop", "while_loop": "loop"}
_CONDITION_COMMANDS = ("if_command", "elseif_command", "while_command")
_ALTERNATIVES = ("elseif_command", "else_command")
_BOOL_WORDS = {"AND": "and", "OR": "or"}
_NAME_LIMIT = 60

_HINTS = {
    "code_duplication": "Move the repeated commands into a function() and call it per target.",
    "large_method": "Split into smaller function()s, or move parts into include()d .cmake "
    "files or add_subdirectory() projects.",
    "complex_method": "Replace platform/option chains with per-platform .cmake files or "
    "generator expressions ($<...>).",
}


def _line(node: Node) -> int:
    return node.start_point[0] + 1


def _end_line(node: Node) -> int:
    return node.end_point[0] + 1


def _header(node: Node) -> Node | None:
    """The opening command of a definition or block (`function(...)`)."""
    return node.named_children[0] if node.named_child_count else None


def _arguments(command: Node | None) -> list[str]:
    args = next((c for c in command.named_children if c.type == "argument_list"), None)
    return [text(a) for a in args.named_children if a.type == "argument"] if args else []


def _block_name(block: Node) -> str:
    """`if(WIN32 AND NOT MINGW)`, shortened to a readable length."""
    header = _header(block)
    name = " ".join(text(header).split()) if header is not None else block.type
    return name if len(name) <= _NAME_LIMIT else name[: _NAME_LIMIT - 4] + "...)"


def _in_condition(node: Node) -> bool:
    """An argument of an if/elseif/while command."""
    args = node.parent.parent if node.parent is not None else None
    owner = args.parent if args is not None else None
    return owner is not None and owner.type in _CONDITION_COMMANDS


def _body(node: Node) -> Node | None:
    return next((c for c in node.named_children if c.type == "body"), None)


class CMakeAdapter(LanguageAdapter):
    language = "cmake"
    extensions = (".cmake",)
    filenames = ("CMakeLists.txt",)
    comment_types = frozenset({"line_comment", "bracket_comment"})
    block_types = frozenset({"body"})
    roles: ClassVar[dict[str, Role]] = {
        "if_condition": Role.IF,
        "elseif_command": Role.IF,  # only reached as an if's alternative
        "foreach_loop": Role.LOOP,
        "while_loop": Role.LOOP,
        "function_def": Role.STOP,
        "macro_def": Role.STOP,
    }
    # an argument list's command sits between it and the if/while block
    bool_transparent = frozenset(_CONDITION_COMMANDS)
    literal_types = frozenset({"quoted_argument", "bracket_argument"})

    def role(self, node: Node) -> Role | None:
        if node.type == "argument_list" and node.parent is not None:
            return Role.BOOL_EXPR if node.parent.type in _CONDITION_COMMANDS else None
        return self.roles.get(node.type)

    def is_alternative(self, node: Node, index: int) -> bool:
        return node.type == "if_condition" and node.children[index].type in _ALTERNATIVES

    def bool_token(self, node: Node) -> str | None:
        if node.type != "unquoted_argument" or not _in_condition(node):
            return None
        return _BOOL_WORDS.get(text(node))

    def is_recursive_call(self, node: Node, fn_name: str) -> bool:
        if node.type != "normal_command":
            return False
        return text(node.named_children[0]).lower() == fn_name.lower()

    def unwrap_loop(self, stmt: Node) -> Node | None:
        return _body(stmt) if self.role(stmt) is Role.LOOP else None

    def hint(self, smell: str) -> str | None:
        return _HINTS.get(smell)

    # --- extraction ---------------------------------------------------------------------

    def _extract(self, module: Module) -> None:
        for node in module.root.named_children:
            if node.type in _BLOCKS:
                self._add_block(module, node)
            elif node.type in _DEFINITIONS:
                self._add_definition(module, node)

    def _add_block(self, module: Module, block: Node) -> None:
        module.functions.append(
            Function(
                name=_block_name(block),
                qualname=_block_name(block),
                kind=_BLOCKS[block.type],
                start_line=_line(block),
                end_line=_end_line(block),
                params=[],
                body=block,
            )
        )
        walk_units(block, lambda n: self._add_nested_definition(module, n))

    def _add_nested_definition(self, module: Module, node: Node) -> bool:
        if node.type not in _DEFINITIONS:
            return False
        self._add_definition(module, node)
        return True

    def _add_definition(self, module: Module, node: Node) -> None:
        args = _arguments(_header(node))
        body = _body(node)
        if not args or body is None:
            return
        module.functions.append(
            Function(
                name=args[0],
                qualname=args[0],
                kind=_DEFINITIONS[node.type],
                start_line=_line(node),
                end_line=_end_line(node),
                params=[Param(name=a) for a in args[1:]],
                body=body,
            )
        )
