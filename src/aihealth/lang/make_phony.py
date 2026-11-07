"""Missing `.PHONY` detection for Makefiles.

A target that never creates a file of its own name (`clean`, `test`, `all`)
should be listed in `.PHONY`. Otherwise, once a file or directory with that
name exists, make treats the target as up to date and silently skips it.

The check is deliberately conservative, preferring a missed target to a
false alarm:
- a target "looks phony" only if its name has no `.`, `/`, `%` or `$` (files
  usually have an extension or a path), and no recipe for it writes `$@` or
  mentions the name (`build: ; mkdir -p build` builds a directory);
- an empty rule (`FORCE:`, no prerequisites or recipe) is the old
  force-target idiom and is left alone, as are special targets (`.DEFAULT`);
- `.PHONY` lines anywhere in the file count, and `$(VAR)`s in them are
  expanded from the file's own assignments. If one can't be expanded (it is
  defined in an included file), nothing is reported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from tree_sitter import Node

from aihealth.lang.base import LanguageSmell, iter_nodes, text

_REF = re.compile(r"\$[({]([^)}$:]+)[)}]")
_WRITES_TARGET = re.compile(r"\$[@]|\$\(@|\$\{@")
# a plain word: files have `.`/`/`, patterns `%`, variables `$`; anything with
# `=`, `#`, `+` or a leading `-` is a flag or assignment misparsed as a rule
_PHONY_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*")
MAX_LISTED = 5


@dataclass
class _Target:
    line: int
    writes_file: bool = False
    empty: bool = True  # every rule for it has no prerequisites and no recipe


@dataclass
class _Scan:
    targets: dict[str, _Target] = field(default_factory=dict)
    phony_text: list[str] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)


def missing_phony(root: Node) -> list[LanguageSmell]:
    scan = _scan(root)
    declared = _declared(scan)
    if declared is None:  # .PHONY uses variables we can't expand here
        return []
    missing = [
        (t.line, name)
        for name, t in scan.targets.items()
        if name not in declared and _looks_phony(name, t)
    ]
    if not missing:
        return []
    missing.sort()
    names = [name for _, name in missing]
    shown = ", ".join(names[:MAX_LISTED]) + (" ..." if len(names) > MAX_LISTED else "")
    noun = "target looks" if len(names) == 1 else "targets look"
    return [
        LanguageSmell(
            smell="missing_phony",
            level="file",
            line=missing[0][0],
            symbol=None,
            value=len(names),
            message=f"{len(names)} {noun} phony but not declared .PHONY: {shown}",
        )
    ]


def _scan(root: Node) -> _Scan:
    scan = _Scan()
    for node in iter_nodes(root):
        # rules inside grammar-error recovery are often assignments misparsed
        # as rules (see parser-choice card): their structure can't be trusted
        if node.type == "rule" and not node.has_error:
            _add_rule(scan, node)
        elif node.type == "variable_assignment":
            _add_variable(scan, node)
    return scan


def _child(node: Node, kind: str) -> Node | None:
    return next((c for c in node.named_children if c.type == kind), None)


def _add_rule(scan: _Scan, rule: Node) -> None:
    names = text(_child(rule, "targets")).split()
    prereqs = rule.child_by_field_name("normal")
    if names == [".PHONY"]:
        scan.phony_text.append(text(prereqs))
        return
    recipe = _child(rule, "recipe")
    view = _RuleView(rule.start_point[0] + 1, recipe is None and prereqs is None, text(recipe))
    for name in names:
        _record(scan, name, view)


@dataclass(frozen=True)
class _RuleView:
    line: int
    empty: bool  # no prerequisites and no recipe
    recipe: str


def _record(scan: _Scan, name: str, rule: _RuleView) -> None:
    """Merge one rule's view of a target (a target may have several rules)."""
    target = scan.targets.setdefault(name, _Target(rule.line))
    target.empty = target.empty and rule.empty
    target.writes_file = target.writes_file or _writes(rule.recipe, name)


def _writes(recipe: str, name: str) -> bool:
    if not recipe:
        return False
    return bool(
        _WRITES_TARGET.search(recipe) or re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", recipe)
    )


def _add_variable(scan: _Scan, node: Node) -> None:
    name = text(node.child_by_field_name("name")).strip()
    value = text(node.child_by_field_name("value"))
    op = next((text(c) for c in node.children if text(c) in ("+=", "=", ":=", "::=", "?=")), "=")
    scan.variables[name] = f"{scan.variables.get(name, '')} {value}" if op == "+=" else value


def _declared(scan: _Scan) -> set[str] | None:
    """Names listed in .PHONY, or None if a variable in them can't be expanded."""
    words: set[str] = set()
    for chunk in scan.phony_text:
        expanded = _expand(chunk, scan.variables)
        if expanded is None:
            return None
        words.update(expanded.split())
    return words


def _expand(value: str, variables: dict[str, str], depth: int = 5) -> str | None:
    for _ in range(depth):
        if "$" not in value:
            return value
        refs = _REF.findall(value)
        if not refs or any(r not in variables for r in refs):
            return None
        value = _REF.sub(lambda m: variables[m.group(1)], value)
    return None if "$" in value else value


def _looks_phony(name: str, target: _Target) -> bool:
    if target.empty or target.writes_file:
        return False
    return _PHONY_NAME.fullmatch(name) is not None
