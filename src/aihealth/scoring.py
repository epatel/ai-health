"""Findings -> penalties -> file score (1-10) -> LoC-weighted project score.

penalty(finding) = weight x severity x scale
    scale = 1 for file-level smells; for function/class smells
    scale = 0.5 + 0.5 x min(1, 4 x share)  where share = symbol LoC / file LoC
    (a smelly function covering >= 25 % of the file counts fully; a tiny
    one in a big file counts half)

P (file penalty), calibrated in M11 on reference corpora (scripts/calibrate.py):
    - penalties are summed per symbol (function/class) and each symbol's sum
      is capped at `symbol_cap` (default 4): one Brain Method and its three
      overlapping component smells don't dominate a file
    - the function/class part is divided by sqrt(loc / density_loc) for files
      over `density_loc` (default 300) LoC: big files are judged by the
      density of problems, not their count (file-level smells such as Large
      File are not damped, so size is still penalised directly)

file score = 10 - 9 x (1 - exp(-P / decay))      decay = 15 by default
    smooth, never below 1, each extra smell costs a bit less
    (P = 1 -> 9.42, P = 2 -> 8.87, P = 5 -> 7.45, P = 10 -> 5.62, P = 20 -> 3.37)

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


@dataclass(frozen=True)
class ScoreParams:
    decay: float = 15.0
    symbol_cap: float = 4.0
    density_loc: float = 300.0


def file_penalty(findings: list[Finding], loc: int, params: ScoreParams) -> float:
    """Total penalty P (see module docstring). Sets each finding's raw penalty."""
    per_symbol: dict[tuple, float] = {}
    filewide = 0.0
    for f in findings:
        f.penalty = round(penalty(f), 3)
        if f.level == "file":
            filewide += f.penalty
        else:
            key = (f.level, f.symbol)
            per_symbol[key] = per_symbol.get(key, 0.0) + f.penalty
    local = sum(min(p, params.symbol_cap) for p in per_symbol.values())
    damping = math.sqrt(max(1.0, loc / params.density_loc))
    return local / damping + filewide


def file_score(findings: list[Finding], loc: int = 0, params: ScoreParams | None = None) -> float:
    params = params or ScoreParams()
    total = file_penalty(findings, loc, params)
    return round(10.0 - 9.0 * (1.0 - math.exp(-total / params.decay)), 2)


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
