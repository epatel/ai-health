"""`[[overrides]]` in `.aihealth.toml`: weights and thresholds for some paths.

    [[overrides]]
    paths = ["tests/*", "legacy/*"]     # same matching as scan.exclude
    weights = { code_duplication = 0.25 }
    thresholds = { large_method = 120 }

Every table whose `paths` match a file applies to it, in file order (later
tables win). Paths are relative to the project root.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

# parses raw TOML values, so its parameters are strings, lists and tables by nature
# aihealth: disable=primitive_obsession

# the tables an override may change
OVERRIDABLE = ("thresholds", "weights")


def path_matches(rel_path: str, patterns: Iterable[str]) -> bool:
    """fnmatch where `*` also crosses `/`; a pattern matches the path itself
    or any suffix starting at a directory boundary."""
    candidates = [rel_path] + [rel_path[i + 1 :] for i, ch in enumerate(rel_path) if ch == "/"]
    return any(fnmatch.fnmatch(c, p) for p in patterns for c in candidates)


@dataclass(frozen=True)
class PathRule:
    paths: tuple[str, ...]
    tables: Mapping[str, Mapping[str, float]]

    def value(self, table: str, key: str) -> float | None:
        return self.tables.get(table, {}).get(key)


def parse_rules(raw: Any, defaults: Mapping[str, Mapping[str, Any]]) -> list[PathRule]:
    """Validated rules; TypeError/ValueError names the offending key. ``defaults`` holds
    the known keys of each overridable table."""
    if not isinstance(raw, list):
        raise TypeError("overrides must be an array of tables ([[overrides]])")
    return [_Parser(f"overrides[{i}]", defaults).rule(entry) for i, entry in enumerate(raw)]


@dataclass
class _Parser:
    where: str
    defaults: Mapping[str, Mapping[str, Any]]

    def rule(self, entry: Any) -> PathRule:
        if not isinstance(entry, dict):
            raise TypeError(f"{self.where} must be a table")
        unknown = set(entry) - {"paths", *OVERRIDABLE}
        if unknown:
            allowed = ", ".join(("paths", *OVERRIDABLE))
            raise ValueError(f"{self.where}: only {allowed} can be set, not {min(unknown)}")
        tables = {t: self.table(t, entry[t]) for t in OVERRIDABLE if t in entry}
        return PathRule(self.paths(entry.get("paths")), tables)

    def paths(self, value: Any) -> tuple[str, ...]:
        if _is_glob_list(value):
            return tuple(value)
        raise TypeError(f"{self.where}.paths must be a non-empty list of glob strings")

    def table(self, name: str, values: Any) -> dict[str, float]:
        if not isinstance(values, dict):
            raise TypeError(f"{self.where}.{name} must be a table")
        for key, value in values.items():
            if key not in self.defaults[name]:
                raise ValueError(f"unknown config key: {self.where}.{name}.{key}")
            if not _is_number(value):
                raise TypeError(f"{self.where}.{name}.{key} must be a number")
        return dict(values)


def _is_glob_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(p, str) for p in value)


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)
