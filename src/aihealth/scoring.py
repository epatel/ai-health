"""Findings -> penalties -> file score (1-10) -> LoC-weighted project score.

penalty(finding) = weight x severity x scale
    scale = 1 for file-level smells; for function/class smells
    scale = 0.5 + 0.5 x min(1, 4 x share)  where share = symbol LoC / file LoC
    (a smelly function covering >= 25 % of the file counts fully; a tiny
    one in a big file counts half)

file score = 10 - 9 x (1 - exp(-P / decay))      P = sum of penalties
    smooth: never below 1, each extra smell costs a bit less, decay = 10
    by default (P = 1 -> 9.14, P = 5 -> 6.46, P = 10 -> 4.31, P = 20 -> 2.22)

project score = sum(score x loc) / sum(loc)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aihealth.metrics import FunctionMetrics
    from aihealth.smells import Finding

HEALTHY, WARNING, ALERT = "healthy", "warning", "alert"


def band(score: float) -> str:
    if score >= 9.0:
        return HEALTHY
    if score >= 4.0:
        return WARNING
    return ALERT


def penalty(f: Finding) -> float:
    scale = 1.0 if f.level == "file" else 0.5 + 0.5 * min(1.0, 4.0 * f.share)
    return f.weight * f.severity * scale


def file_score(findings: list[Finding], decay: float = 10.0) -> float:
    total = 0.0
    for f in findings:
        f.penalty = round(penalty(f), 3)
        total += f.penalty
    return round(10.0 - 9.0 * (1.0 - math.exp(-total / decay)), 2)


@dataclass
class FileScore:
    path: str
    language: str
    loc: int
    score: float
    findings: list[Finding] = field(default_factory=list)
    functions: list[FunctionMetrics] = field(default_factory=list)
    parse_errors: int = 0

    @property
    def band(self) -> str:
        return band(self.score)

    def to_dict(self, with_functions: bool = False) -> dict:
        d = {
            "path": self.path,
            "language": self.language,
            "loc": self.loc,
            "score": self.score,
            "band": self.band,
            "parse_errors": self.parse_errors,
            "findings": [f.to_dict() for f in sorted(self.findings, key=lambda f: -f.penalty)],
        }
        if with_functions:
            d["functions"] = [
                {
                    "qualname": m.qualname,
                    "line": m.start_line,
                    "loc": m.loc,
                    "cyclomatic": m.cyclomatic,
                    "cognitive": m.cognitive,
                    "max_nesting": m.max_nesting,
                    "bumps": len(m.bumps),
                    "args": m.args,
                    "widget_depth": m.widget_depth,
                }
                for m in self.functions
            ]
        return d


def project_score(files: list[FileScore]) -> float | None:
    loc = sum(f.loc for f in files)
    if loc == 0:
        return None
    return round(sum(f.score * f.loc for f in files) / loc, 2)
