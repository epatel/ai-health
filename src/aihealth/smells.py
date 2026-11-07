"""Smell detectors: metrics + config thresholds -> findings with hints.

A smell triggers when ``value > threshold``. Its severity grows from 1.0
at the first triggering value by one step per ``threshold`` units beyond it,
capped at 3.0 (``_severity``). Scoring turns severities into penalties.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aihealth.config import Config
    from aihealth.duplication import Clone, CloneSide
    from aihealth.lang.base import LanguageAdapter
    from aihealth.metrics import FunctionMetrics
    from aihealth.model import Class, Function, Module

MAX_SEVERITY = 3.0

HINTS = {
    "brain_method": "Large, complex and deeply nested at once: split it into a few "
    "well-named functions, one per responsibility, before adding anything.",
    "large_method": "Split into smaller named steps; extract cohesive blocks into helpers.",
    "complex_method": "Too many branches: extract branches into functions, replace "
    "conditional chains with a lookup table or polymorphism.",
    "cognitive_complexity": "Hard to follow: flatten with early returns/guard clauses and "
    "extract nested blocks.",
    "deep_nesting": "Use guard clauses / early returns and extract the inner block into "
    "a function.",
    "bumpy_road": "Several separate chunks of nested logic: extract each chunk into its "
    "own function.",
    "excess_arguments": "Group related parameters into an object/record, or split the function.",
    "complex_conditional": "Extract the condition into a well-named boolean function or variable.",
    "deep_widget_tree": "Extract subtrees into small widgets (classes, not helper methods).",
    "large_file": "Split the file by responsibility into separate modules.",
    "low_cohesion": "Methods form independent groups that share no state: split the class "
    "along those groups.",
    "primitive_obsession": "Introduce small domain types (value objects, enums) instead of "
    "passing raw strings/ints/maps around.",
    "code_duplication": "Extract the duplicated logic into a shared function.",
    "missing_phony": "Declare targets that don't create a file of their name in .PHONY "
    "(e.g. `.PHONY: clean test`); otherwise a file or directory with that name makes "
    "`make <target>` silently do nothing.",
}

LABELS = {
    "brain_method": "Brain Method",
    "large_method": "Large Method",
    "complex_method": "Complex Method",
    "cognitive_complexity": "High Cognitive Complexity",
    "deep_nesting": "Deep Nesting",
    "bumpy_road": "Bumpy Road",
    "excess_arguments": "Excess Arguments",
    "complex_conditional": "Complex Conditional",
    "deep_widget_tree": "Deep Widget Tree",
    "large_file": "Large File",
    "low_cohesion": "Low Cohesion",
    "primitive_obsession": "Primitive Obsession",
    "code_duplication": "Code Duplication",
    "missing_phony": "Missing .PHONY",
}


# What each smell measures and why it matters (served by MCP explain_smell).
EXPLANATIONS = {
    "brain_method": "A function that is at the same time a Large Method, a Complex Method and "
    "deeply nested. It typically centralises a module's behaviour, so every change touches it "
    "and every change is risky.",
    "large_method": "Lines of code in the function (blank lines, comments and docstrings "
    "excluded; nested functions counted separately). Long functions mix responsibilities and "
    "are hard to name, test and review. Dart `build()` methods have a higher limit.",
    "complex_method": "Cyclomatic complexity: 1 + every branch point (if/elif, loops, case arms, "
    "catch, ternary, and/or). It approximates the number of paths a test suite must cover.",
    "cognitive_complexity": "SonarSource cognitive complexity: like cyclomatic, but each "
    "structure costs more the deeper it is nested, and runs of boolean operators count once. "
    "It approximates how hard the code is to read top to bottom.",
    "deep_nesting": "Maximum depth of nested if/loop/switch/try blocks. Each level adds "
    "state the reader must keep in mind; nested conditionals are a strong predictor of defects.",
    "bumpy_road": "Several separate chunks of nested logic in one function (a 'bump' is a "
    "top-level statement nesting 2+ deep). Each bump is usually a missing abstraction that "
    "wants to be its own function.",
    "excess_arguments": "Number of parameters (named or labelled parameters count half: Dart "
    "`{}`, Python keyword-only, Swift labels; Dart `this.x` field initialisers are free). Long parameter lists hide a missing concept and make call "
    "sites error-prone.",
    "complex_conditional": "Boolean expressions with 2+ and/or operators (asserts excluded). "
    "They are easy to get wrong and hard to read; a named predicate documents the intent.",
    "deep_widget_tree": "Flutter / SwiftUI: nesting depth of widget or view constructor calls "
    "in one function. Deep trees in one build method or `body` are hard to read and rebuild "
    "more than needed; small widget / view types fix both.",
    "large_file": "Code lines in the file. Large files accumulate unrelated responsibilities "
    "and become merge-conflict and knowledge bottlenecks.",
    "low_cohesion": "LCOM4: groups of methods that share no fields and don't call each other. "
    "Two or more independent groups mean the class does unrelated jobs.",
    "primitive_obsession": "Share of typed parameters in the file that are built-in primitives "
    "(str, int, dict, List, Map, ...; bool and untyped excluded). Domain concepts passed as raw "
    "primitives lose validation and meaning.",
    "missing_phony": "Makefiles: targets that look phony (no `.`, `/`, `%` or `$` in the name, "
    "and a recipe that never writes `$@` or mentions the name) but are not listed in .PHONY. "
    "If a file or directory with that name ever exists, make considers the target up to date "
    "and skips it. Empty `FORCE:` rules are not flagged, and nothing is reported when .PHONY "
    "uses variables defined outside the file.",
    "code_duplication": "Blocks of 50+ tokens / 6+ lines repeated within the file or across the "
    "project (literals may differ). Duplicated logic must be changed in several places, and one "
    "copy is eventually forgotten.",
}


@dataclass
class Finding:
    smell: str
    level: str  # function | class | file
    path: str
    line: int
    symbol: str | None  # function qualname or class name
    value: float
    threshold: float
    severity: float
    message: str
    hint: str
    weight: float = 0.0
    share: float = 1.0  # fraction of the file's LoC the symbol covers
    penalty: float = 0.0  # filled by scoring

    @property
    def label(self) -> str:
        return LABELS[self.smell]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["label"] = self.label
        return d


def _severity(value: float, threshold: float, integer: bool = True) -> float:
    step = max(threshold, 1.0) if integer else max(threshold, 1e-9)
    first = threshold + 1 if integer else threshold
    return round(min(1.0 + max(0.0, value - first) / step, MAX_SEVERITY), 3)


@dataclass(frozen=True)
class _Hit:
    """A threshold crossing, before the weight, hint and path are added."""

    smell: str
    level: str
    line: int
    symbol: str | None
    value: float
    threshold: float
    message: str
    share: float = 1.0
    integer: bool = True  # severity steps for whole-number metrics
    severity: float | None = None  # computed from value/threshold if None

    def computed_severity(self) -> float:
        if self.severity is not None:
            return self.severity
        return _severity(self.value, self.threshold, self.integer)


class Detector:
    """Finds the smells of one module: ``Detector(...).detect(metrics, clones)``."""

    def __init__(self, module: Module, adapter: LanguageAdapter, config: Config) -> None:
        self.module = module
        self.adapter = adapter
        self.config = config
        self.findings: list[Finding] = []
        self.file_loc = max(module.loc, 1)

    def detect(
        self, metrics: list[FunctionMetrics], clones: list[Clone] | None = None
    ) -> list[Finding]:
        by_q = {m.qualname: m for m in metrics}
        funcs = self.module.all_functions()
        for fn in funcs:
            self.function(fn, by_q[fn.qualname])
        for cls in self.module.classes:
            self.cohesion(cls)
        self.file()
        if clones:
            self.duplication(clones, {f.qualname: f.loc for f in funcs})
        disabled = disabled_smells(self.module, self.adapter)
        return [f for f in self.findings if f.smell not in disabled]

    def add(self, hit: _Hit) -> None:
        weight = self.config.weight(hit.smell)
        if weight <= 0:
            return
        self.findings.append(
            Finding(
                smell=hit.smell,
                level=hit.level,
                path=self.module.path,
                line=hit.line,
                symbol=hit.symbol,
                value=hit.value,
                threshold=hit.threshold,
                severity=hit.computed_severity(),
                message=hit.message,
                hint=self.adapter.hint(hit.smell) or HINTS[hit.smell],
                weight=weight,
                share=round(min(hit.share, 1.0), 4),
            )
        )

    # -- function level -----------------------------------------------------

    def function(self, fn: Function, m: FunctionMetrics) -> None:
        large_limit = self._large_limit(fn)
        q, line = fn.qualname, fn.start_line
        self._brain_method(fn, m, large_limit)
        # (smell, value, line, limit or None for the configured one, message)
        simple = [
            (
                "large_method",
                fn.loc,
                line,
                large_limit,
                f"{q} has {fn.loc} lines of code (limit {large_limit:g})",
            ),
            (
                "complex_method",
                m.cyclomatic,
                line,
                None,
                f"{q} has cyclomatic complexity {m.cyclomatic}",
            ),
            (
                "cognitive_complexity",
                m.cognitive,
                line,
                None,
                f"{q} has cognitive complexity {m.cognitive}",
            ),
            (
                "deep_nesting",
                m.max_nesting,
                m.nesting_line or line,
                None,
                f"{q} nests {m.max_nesting} levels deep at line {m.nesting_line}",
            ),
            (
                "deep_widget_tree",
                m.widget_depth,
                line,
                None,
                f"{q} builds a widget tree {m.widget_depth} levels deep",
            ),
        ]
        for smell, value, at, limit, message in simple:
            limit = self.config.threshold(smell) if limit is None else limit
            if value > limit:
                self.add(_Hit(smell, "function", at, q, value, limit, message, self._share(fn)))
        self._bumpy_road(fn, m)
        self._excess_arguments(fn)
        self._complex_conditional(fn, m)

    def _share(self, fn: Function) -> float:
        return fn.loc / self.file_loc

    def _large_limit(self, fn: Function) -> float:
        flutter_build = self.adapter.language == "dart" and _is_build_method(fn)
        return self.config.threshold("large_method_build" if flutter_build else "large_method")

    def _brain_method(self, fn: Function, m: FunctionMetrics, large_limit: float) -> None:
        cfg = self.config
        parts = [
            (fn.loc, large_limit),
            (m.cyclomatic, cfg.threshold("complex_method")),
            (m.max_nesting, cfg.threshold("deep_nesting")),
        ]
        if not all(value > limit for value, limit in parts):
            return
        sev = sum(_severity(value, limit) for value, limit in parts) / len(parts)
        message = (
            f"{fn.qualname} is large ({fn.loc} lines), complex (cyclomatic {m.cyclomatic}) "
            f"and deeply nested (depth {m.max_nesting})"
        )
        self.add(
            _Hit(
                "brain_method",
                "function",
                fn.start_line,
                fn.qualname,
                fn.loc,
                large_limit,
                message,
                self._share(fn),
                severity=round(sev, 3),
            )
        )

    def _bumpy_road(self, fn: Function, m: FunctionMetrics) -> None:
        limit = self.config.threshold("bumpy_road")
        if len(m.bumps) <= limit:
            return
        spans = ", ".join(f"L{b.start_line}-{b.end_line}" for b in m.bumps)
        message = f"{fn.qualname} has {len(m.bumps)} separate nested chunks ({spans})"
        self.add(
            _Hit(
                "bumpy_road",
                "function",
                m.bumps[0].start_line,
                fn.qualname,
                len(m.bumps),
                limit,
                message,
                self._share(fn),
            )
        )

    def _excess_arguments(self, fn: Function) -> None:
        args, limit = _effective_args(fn), self.config.threshold("excess_arguments")
        if args <= limit:
            return
        message = f"{fn.qualname} takes {len(fn.params)} parameters (weighted {args:g})"
        self.add(
            _Hit(
                "excess_arguments",
                "function",
                fn.start_line,
                fn.qualname,
                args,
                limit,
                message,
                self._share(fn),
                integer=False,
            )
        )

    def _complex_conditional(self, fn: Function, m: FunctionMetrics) -> None:
        limit = self.config.threshold("complex_conditional")
        hits = [b for b in m.bool_exprs if b.is_condition and b.operators > limit]
        if not hits:
            return
        worst = max(hits, key=lambda b: b.operators)
        where = ", ".join(f"L{b.line}" for b in hits)
        message = (
            f"{fn.qualname} has {len(hits)} complex boolean expression(s) "
            f"(up to {worst.operators} operators: {where})"
        )
        self.add(
            _Hit(
                "complex_conditional",
                "function",
                worst.line,
                fn.qualname,
                worst.operators,
                limit,
                message,
                self._share(fn),
            )
        )

    # -- class level ----------------------------------------------------------

    def cohesion(self, cls: Class) -> None:
        if self.config.weight("low_cohesion") <= 0:
            return
        components = lcom4(cls, self.adapter)
        if components is None:
            return
        threshold = self.config.threshold("low_cohesion")
        if components > threshold:
            message = (
                f"class {cls.name} splits into {components} groups of methods sharing no state"
            )
            self.add(
                _Hit(
                    "low_cohesion",
                    "class",
                    cls.start_line,
                    cls.name,
                    components,
                    threshold,
                    message,
                    cls.loc / self.file_loc,
                )
            )

    # -- file level -------------------------------------------------------------

    def file(self) -> None:
        cfg = self.config
        if self.module.loc > cfg.threshold("large_file") and cfg.weight("large_file") > 0:
            message = f"file has {self.module.loc} lines of code"
            self.add(
                _Hit(
                    "large_file",
                    "file",
                    1,
                    None,
                    self.module.loc,
                    cfg.threshold("large_file"),
                    message,
                )
            )
        self.primitive_obsession()
        self.language_smells()

    def language_smells(self) -> None:
        """Smells only one language has (e.g. Makefile `missing_phony`); same
        weights, thresholds and severity rules as every other smell."""
        for hit in self.adapter.language_smells(self.module):
            threshold = self.config.threshold(hit.smell)
            if hit.value > threshold:
                self.add(
                    _Hit(
                        hit.smell,
                        hit.level,
                        hit.line,
                        hit.symbol,
                        hit.value,
                        threshold,
                        hit.message,
                    )
                )

    def primitive_obsession(self) -> None:
        cfg = self.config
        if cfg.weight("primitive_obsession") <= 0:
            return
        known = primitive = 0
        for fn in self.module.all_functions():
            for p in fn.params:
                flag = self.adapter.is_primitive(p.type_text)
                if flag is None:
                    continue
                known += 1
                primitive += flag
        if known < cfg.section("primitive_obsession")["min_params"]:
            return
        ratio = primitive / known
        threshold = cfg.threshold("primitive_obsession")
        if ratio > threshold:
            message = f"{primitive} of {known} typed parameters are primitives ({ratio:.0%})"
            self.add(
                _Hit(
                    "primitive_obsession",
                    "file",
                    1,
                    None,
                    round(ratio, 3),
                    threshold,
                    message,
                    integer=False,
                )
            )

    def duplication(self, clones: list[Clone], fn_loc: dict[str, int]) -> None:
        """One finding per function that has duplicated lines."""
        if self.config.weight("code_duplication") <= 0:
            return
        sites: dict[str, _DuplicateSite] = {}
        for side, other in _sides_in(clones, self.module.path):
            site = sites.setdefault(side.qualname, _DuplicateSite(side.start_line))
            site.add(side, other)
        min_lines = self.config.section("duplication")["min_lines"]
        for q, site in sorted(sites.items()):
            count = len(site.lines)
            message = f"{q} shares {count} duplicated lines with {site.partner_list()}"
            self.add(
                _Hit(
                    "code_duplication",
                    "function",
                    site.first,
                    q,
                    count,
                    min_lines,
                    message,
                    fn_loc.get(q, count) / self.file_loc,
                    severity=_duplication_severity(count, min_lines),
                )
            )


def _sides_in(clones: list[Clone], path: str) -> Iterator[tuple[CloneSide, CloneSide]]:
    """(this file's side, the other side) of every clone touching ``path``."""
    for c in clones:
        for side, other in ((c.a, c.b), (c.b, c.a)):
            if side.path == path:
                yield side, other


@dataclass
class _DuplicateSite:
    """The duplicated lines of one function and what they duplicate."""

    first: int
    lines: set[int] = field(default_factory=set)
    partners: list[str] = field(default_factory=list)

    def add(self, side: CloneSide, other: CloneSide) -> None:
        self.lines.update(range(side.start_line, side.end_line + 1))
        self.first = min(self.first, side.start_line)
        where = _partner_name(side, other)
        if where not in self.partners:
            self.partners.append(where)

    def partner_list(self) -> str:
        more = len(self.partners) - 3
        return ", ".join(self.partners[:3]) + (f" (+{more} more)" if more > 0 else "")


def _partner_name(side: CloneSide, other: CloneSide) -> str:
    if other.path != side.path:
        return f"{other.path}:{other.qualname}"
    if other.qualname == side.qualname:
        return "itself"  # repeated block inside one function
    return other.qualname


def _duplication_severity(lines: int, min_lines: int) -> float:
    """1.0 at the minimum clone size, +1 per further (min_lines - 1) lines."""
    return _severity(lines, min_lines - 1, integer=True) if min_lines > 1 else 1.0


def _is_build_method(fn: Function) -> bool:
    return fn.kind == "method" and fn.name == "build"


def _effective_args(fn: Function) -> float:
    """Positional params count 1; named ones (Dart `{}`, Python keyword-only
    and **kwargs, Swift labels) 0.5; Dart `this.x`/`super.x` field initializers don't count."""
    total = 0.0
    for p in fn.params:
        if p.type_text == "<field>":
            continue
        total += 0.5 if p.named else 1.0
    return total


def lcom4(cls: Class, adapter: LanguageAdapter) -> int | None:
    """Connected components among non-constructor methods that touch the
    class's members (shared field, or one calls the other). None when there
    is too little to judge (< 4 such methods)."""
    refs = adapter.member_refs(cls)
    methods = [m for m in cls.methods if _touches_members(m, refs)]
    if len(methods) < 4:
        return None
    return _components(methods, refs)


def _touches_members(method: Function, refs: dict[str, set[str]]) -> bool:
    return method.kind != "constructor" and bool(refs.get(method.qualname))


def _components(methods: list[Function], refs: dict[str, set[str]]) -> int:
    by_name = {m.name.rstrip("="): m.qualname for m in methods}
    groups = _DisjointSets([m.qualname for m in methods])
    first_user: dict[str, str] = {}  # field -> the first method touching it
    for m in methods:
        for name in refs[m.qualname]:
            # a call / access of another method, or a field shared with an earlier one
            groups.union(m.qualname, by_name.get(name) or first_user.setdefault(name, m.qualname))
    return groups.count()


class _DisjointSets:
    """Union-find over method qualnames."""

    def __init__(self, items: list[str]) -> None:
        self.parent = {x: x for x in items}

    def find(self, x: str) -> str:
        parent = self.parent
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        self.parent[self.find(a)] = self.find(b)

    def count(self) -> int:
        return len({self.find(x) for x in self.parent})


_DISABLE = re.compile(r"aihealth:[ \t]*disable=([\w, \t]+)")


def disabled_smells(module: Module, adapter: LanguageAdapter) -> set[str]:
    """Smells switched off for a file by a *comment* such as
    `# aihealth: disable=primitive_obsession, excess_arguments` (strings and
    docstrings don't count). Meant for boundaries where the smell is imposed,
    e.g. MCP/CLI/JSON API functions that must take primitives."""
    found: set[str] = set()
    stack = [module.root]
    while stack:
        node = stack.pop()
        if node.type in adapter.comment_types:
            for match in _DISABLE.finditer(node.text.decode("utf-8", "replace")):
                found.update(x.strip() for x in match.group(1).split(","))
            continue
        stack.extend(node.children)
    return found & set(LABELS)
