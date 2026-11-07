"""Smell detectors: metrics + config thresholds -> findings with hints.

A smell triggers when ``value > threshold``. Its severity grows from 1.0
at the first triggering value by one step per ``threshold`` units beyond it,
capped at 3.0 (``_severity``). Scoring turns severities into penalties.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aihealth.config import Config
    from aihealth.duplication import Clone
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


class _Detector:
    def __init__(self, module: Module, adapter: LanguageAdapter, config: Config) -> None:
        self.module = module
        self.adapter = adapter
        self.config = config
        self.findings: list[Finding] = []
        self.file_loc = max(module.loc, 1)

    def add(
        self,
        smell: str,
        level: str,
        line: int,
        symbol: str | None,
        value: float,
        threshold: float,
        message: str,
        share: float = 1.0,
        integer: bool = True,
        severity: float | None = None,
    ) -> None:
        weight = self.config.weight(smell)
        if weight <= 0:
            return
        self.findings.append(
            Finding(
                smell=smell,
                level=level,
                path=self.module.path,
                line=line,
                symbol=symbol,
                value=value,
                threshold=threshold,
                severity=severity if severity is not None else _severity(value, threshold, integer),
                message=message,
                hint=HINTS[smell],
                weight=weight,
                share=round(min(share, 1.0), 4),
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
                self.add(smell, "function", at, q, value, limit, message, self._share(fn))
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
        self.add(
            "brain_method",
            "function",
            fn.start_line,
            fn.qualname,
            fn.loc,
            large_limit,
            f"{fn.qualname} is large ({fn.loc} lines), complex (cyclomatic {m.cyclomatic}) "
            f"and deeply nested (depth {m.max_nesting})",
            self._share(fn),
            severity=round(sev, 3),
        )

    def _bumpy_road(self, fn: Function, m: FunctionMetrics) -> None:
        limit = self.config.threshold("bumpy_road")
        if len(m.bumps) <= limit:
            return
        spans = ", ".join(f"L{b.start_line}-{b.end_line}" for b in m.bumps)
        self.add(
            "bumpy_road",
            "function",
            m.bumps[0].start_line,
            fn.qualname,
            len(m.bumps),
            limit,
            f"{fn.qualname} has {len(m.bumps)} separate nested chunks ({spans})",
            self._share(fn),
        )

    def _excess_arguments(self, fn: Function) -> None:
        args, limit = _effective_args(fn), self.config.threshold("excess_arguments")
        if args <= limit:
            return
        self.add(
            "excess_arguments",
            "function",
            fn.start_line,
            fn.qualname,
            args,
            limit,
            f"{fn.qualname} takes {len(fn.params)} parameters (weighted {args:g})",
            self._share(fn),
            integer=False,
        )

    def _complex_conditional(self, fn: Function, m: FunctionMetrics) -> None:
        limit = self.config.threshold("complex_conditional")
        hits = [b for b in m.bool_exprs if not b.in_assert and b.operators > limit]
        if not hits:
            return
        worst = max(hits, key=lambda b: b.operators)
        where = ", ".join(f"L{b.line}" for b in hits)
        self.add(
            "complex_conditional",
            "function",
            worst.line,
            fn.qualname,
            worst.operators,
            limit,
            f"{fn.qualname} has {len(hits)} complex boolean expression(s) "
            f"(up to {worst.operators} operators: {where})",
            self._share(fn),
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
            self.add(
                "low_cohesion",
                "class",
                cls.start_line,
                cls.name,
                components,
                threshold,
                f"class {cls.name} splits into {components} groups of methods sharing no state",
                cls.loc / self.file_loc,
            )

    # -- file level -------------------------------------------------------------

    def file(self) -> None:
        cfg = self.config
        if self.module.loc > cfg.threshold("large_file") and cfg.weight("large_file") > 0:
            self.add(
                "large_file",
                "file",
                1,
                None,
                self.module.loc,
                cfg.threshold("large_file"),
                f"file has {self.module.loc} lines of code",
            )
        self.primitive_obsession()

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
            self.add(
                "primitive_obsession",
                "file",
                1,
                None,
                round(ratio, 3),
                threshold,
                f"{primitive} of {known} typed parameters are primitives ({ratio:.0%})",
                integer=False,
            )

    def duplication(self, clones: list[Clone], fn_loc: dict[str, int]) -> None:
        """One finding per function that has duplicated lines."""
        if self.config.weight("code_duplication") <= 0:
            return
        per_fn: dict[str, tuple[set[int], list[str], int]] = {}
        for c in clones:
            for side, other in ((c.a, c.b), (c.b, c.a)):
                if side.path != self.module.path:
                    continue
                lines, partners, first = per_fn.setdefault(
                    side.qualname, (set(), [], side.start_line)
                )
                lines.update(range(side.start_line, side.end_line + 1))
                if other.path != side.path:
                    where = f"{other.path}:{other.qualname}"
                elif other.qualname == side.qualname:
                    where = "itself"  # repeated block inside one function
                else:
                    where = other.qualname
                if where not in partners:
                    partners.append(where)
                per_fn[side.qualname] = (lines, partners, min(first, side.start_line))
        min_lines = self.config.section("duplication")["min_lines"]
        for q, (lines, partners, first) in sorted(per_fn.items()):
            shown = ", ".join(partners[:3]) + (
                f" (+{len(partners) - 3} more)" if len(partners) > 3 else ""
            )
            self.add(
                "code_duplication",
                "function",
                first,
                q,
                len(lines),
                min_lines,
                f"{q} shares {len(lines)} duplicated lines with {shown}",
                fn_loc.get(q, len(lines)) / self.file_loc,
                # 1.0 at the minimum clone size, +1 per further (min_lines - 1)
                severity=_severity(len(lines), min_lines - 1, integer=True)
                if min_lines > 1
                else 1.0,
            )


def _is_build_method(fn: Function) -> bool:
    return fn.kind == "method" and fn.name == "build"


def _effective_args(fn: Function) -> float:
    """Positional params count 1, named (Dart `{}`) 0.5; `this.x`/`super.x`
    field initializers don't count."""
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
    methods = [m for m in cls.methods if m.kind != "constructor" and refs.get(m.qualname)]
    if len(methods) < 4:
        return None
    names = {m.name.rstrip("="): m.qualname for m in methods}
    parent = {m.qualname: m.qualname for m in methods}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    by_field: dict[str, str] = {}
    for m in methods:
        for name in refs[m.qualname]:
            if name in names:
                union(m.qualname, names[name])  # call / access of another method
            elif name in by_field:
                union(m.qualname, by_field[name])
            else:
                by_field[name] = m.qualname
    return len({find(m.qualname) for m in methods})


def detect(
    module: Module,
    adapter: LanguageAdapter,
    config: Config,
    metrics: list[FunctionMetrics],
    clones: list[Clone] | None = None,
) -> list[Finding]:
    d = _Detector(module, adapter, config)
    by_q = {m.qualname: m for m in metrics}
    funcs = module.all_functions()
    for fn in funcs:
        d.function(fn, by_q[fn.qualname])
    for cls in module.classes:
        d.cohesion(cls)
    d.file()
    if clones:
        d.duplication(clones, {f.qualname: f.loc for f in funcs})
    return d.findings
