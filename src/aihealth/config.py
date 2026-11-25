"""Configuration: built-in defaults, overridden by `<repo>/.aihealth.toml`.

Example `.aihealth.toml`::

    [thresholds]
    large_method = 100          # LoC
    deep_nesting = 4

    [weights]
    primitive_obsession = 0     # 0 disables a smell

    [scan]
    exclude = ["legacy/**"]     # added to the default excludes
    max_file_kb = 1024          # larger files aren't scored (scan, delta, hooks, MCP)

    [dart]
    test_closure_calls = ["group", "test", "testWidgets", "patrolTest"]

    [swift]
    test_closure_calls = ["describe", "context", "it"]   # Quick-style specs

    [rust]
    score_test_modules = true   # include `#[cfg(test)] mod tests` (default: false)

    [typescript]                # .ts and .tsx
    test_closure_calls = ["describe", "it", "test"]

    [javascript]                # .js, .mjs, .cjs and .jsx
    test_closure_calls = ["describe", "it", "test"]

    [go]
    count_error_checks = true   # score `if err != nil { return .. }` (default: false)
    test_closure_calls = ["Run"]   # `t.Run("name", func(t *testing.T) {..})`

    [[overrides]]               # for some files (see overrides.py); later tables win
    paths = ["tests/*", "legacy/*"]   # same matching as scan.exclude
    weights = { code_duplication = 0.25 }
    thresholds = { large_method = 120 }
"""

from __future__ import annotations

import copy
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# the config API is keyed by smell and table names: strings by nature
# aihealth: disable=primitive_obsession
from aihealth.overrides import OVERRIDABLE, PathRule, parse_rules, path_matches

CONFIG_FILE = ".aihealth.toml"

# Smell trigger: value > threshold. Severity = min(value / threshold, 3).
DEFAULT_THRESHOLDS: dict[str, float] = {
    "large_method": 70,  # code lines in a function
    "large_method_build": 120,  # Dart widget `build()` methods
    "complex_method": 9,  # cyclomatic
    "cognitive_complexity": 15,
    "deep_nesting": 3,  # max nesting depth (4 triggers)
    "bumpy_road": 1,  # bumps (2 triggers)
    "excess_arguments": 4,  # params; Dart named params count 0.5
    "complex_conditional": 1,  # and/or operators in one expression (2 triggers)
    "deep_widget_tree": 8,  # Dart widget constructor nesting
    "large_file": 1000,  # code lines in a file
    "low_cohesion": 2,  # LCOM4 components (3 triggers)
    "primitive_obsession": 0.6,  # share of typed params that are primitive
    "missing_phony": 0,  # Makefile targets that look phony but aren't in .PHONY
}

DEFAULT_WEIGHTS: dict[str, float] = {
    "brain_method": 2.0,
    "large_method": 1.0,
    "complex_method": 0.75,
    "cognitive_complexity": 1.0,
    "deep_nesting": 1.0,
    "bumpy_road": 1.0,
    "excess_arguments": 0.5,
    "complex_conditional": 0.5,
    "deep_widget_tree": 0.75,
    "large_file": 1.5,
    "low_cohesion": 1.0,
    "primitive_obsession": 0.75,
    "code_duplication": 1.0,
    "missing_phony": 0.5,  # hygiene, not complexity
}

DEFAULT_EXCLUDES: list[str] = [
    "*.g.dart",
    "*.freezed.dart",
    "*.mocks.dart",
    "*.gr.dart",
    "*.pb.dart",
    "*.pbenum.dart",
    "*.pbjson.dart",
    "*_pb2.py",
    "*/generated/*",
    ".dart_tool/*",
    "build/*",
    ".venv/*",
    "venv/*",
    "node_modules/*",
    "*/site-packages/*",
    "*/__pycache__/*",
    "*/migrations/versions/*",
    "*/CMakeFiles/*",  # CMake-generated Makefiles
    "CMakeFiles/*",
    "cmake-build-*/*",  # CLion build directories
    "_deps/*",  # CMake FetchContent sources (third-party)
    "*.h.cmake",  # configure_file() templates: C headers, not CMake
    "*.hpp.cmake",
    "*.generated.swift",  # Sourcery / SwiftGen
    ".build/*",  # SwiftPM
    "Pods/*",  # CocoaPods
    "DerivedData/*",
    "*.d.ts",  # TypeScript declarations: signatures only
    "*.min.js",  # minified bundles
    "*.bundle.js",
    "target/generated-sources/*",  # Maven annotation processors, protoc, ...
    "*/target/generated-sources/*",
    "target/debug/*",  # Cargo build output (build-script generated code)
    "target/release/*",
    "*.docc/*",  # DocC catalogs: tutorial step files repeat each other
    "*.pb.cc",  # protobuf (C++)
    "*.pb.h",
    "moc_*.cpp",  # Qt moc / rcc output (uic `ui_*.h` is caught by its marker)
    "qrc_*.cpp",
    "lex.yy.c",  # flex / bison output
    "*.tab.c",
    "*.tab.cc",
    "*.tab.h",
    "*.tab.hh",
]

DEFAULTS: dict[str, Any] = {
    "thresholds": DEFAULT_THRESHOLDS,
    "weights": DEFAULT_WEIGHTS,
    "scan": {"exclude": [], "use_default_excludes": True, "max_file_kb": 1024},
    # see scoring.py; calibrated in M11 (cards/code-health-model.md)
    "score": {"decay": 15.0, "symbol_cap": 4.0, "density_loc": 300.0},
    "primitive_obsession": {"min_params": 10},
    "duplication": {"min_tokens": 50, "min_lines": 6, "cross_file": True},
    # score drops up to this are ignored when no smell is new/worse: when a file
    # shrinks, its remaining smelly functions cover a bigger share of it
    "delta": {"tolerance": 0.1},
    "dart": {"test_closure_calls": None},  # None = built-in list
    "swift": {"test_closure_calls": None},
    # `#[cfg(test)]` modules: left out of scores by default (code-health-model)
    "rust": {"score_test_modules": False},
    "typescript": {"test_closure_calls": None},  # for .ts and .tsx
    "javascript": {"test_closure_calls": None},
    # error checks that only return: no complexity by default (code-health-model)
    "go": {"count_error_checks": False, "test_closure_calls": None},
}


# What each section and key means: `aihealth config` and the `list_config` MCP
# tool print these. Weights are described from the smell labels; every other
# key needs an entry here (a test checks).
SECTION_DOCS: dict[str, str] = {
    "thresholds": "A smell triggers when its value exceeds the threshold; "
    "severity grows with value / threshold, up to 3x.",
    "weights": "Penalty multiplier per smell; 0 turns the smell off.",
    "scan": "Which files are scored.",
    "score": "How penalties become the 1-10 file score (calibrated; change with care).",
    "primitive_obsession": "When the file-level Primitive Obsession check runs.",
    "duplication": "Clone detection for the Code Duplication smell.",
    "delta": "Verdict rules of `aihealth delta`.",
    "dart": "Dart/Flutter adapter.",
    "swift": "Swift adapter.",
    "rust": "Rust adapter.",
    "typescript": "TypeScript adapter (.ts, .mts, .cts, .tsx).",
    "javascript": "JavaScript adapter (.js, .mjs, .cjs, .jsx).",
    "go": "Go adapter.",
}

KEY_DOCS: dict[str, str] = {
    "thresholds.large_method": "Code lines in a function.",
    "thresholds.large_method_build": "Code lines in a Dart widget build() method.",
    "thresholds.complex_method": "Cyclomatic complexity of a function.",
    "thresholds.cognitive_complexity": "Cognitive complexity of a function.",
    "thresholds.deep_nesting": "Maximum nesting depth in a function.",
    "thresholds.bumpy_road": "Separate chunks of nested logic in a function.",
    "thresholds.excess_arguments": "Parameters of a function (Dart named and Swift "
    "labelled parameters count half).",
    "thresholds.complex_conditional": "and/or operators in one boolean expression.",
    "thresholds.deep_widget_tree": "Nesting of widget/view/JSX constructors "
    "(Flutter, SwiftUI, JSX).",
    "thresholds.large_file": "Code lines in a file.",
    "thresholds.low_cohesion": "LCOM4: groups of methods and fields that don't use each other.",
    "thresholds.primitive_obsession": "Share (0-1) of a file's typed parameters "
    "that are primitives.",
    "thresholds.missing_phony": "Makefile targets that look phony but aren't in .PHONY.",
    "scan.exclude": "Glob patterns added to the default excludes (`*` crosses `/`, "
    "matched against every path suffix).",
    "scan.use_default_excludes": "Keep the built-in excludes (generated code, "
    "build output, vendored dependency folders).",
    "scan.max_file_kb": "Files larger than this are not scored in scan, delta, the hooks and MCP.",
    "score.decay": "score = 10 - 9 x (1 - exp(-penalty / decay)): larger is more lenient.",
    "score.symbol_cap": "Cap on the summed penalty of one function or class.",
    "score.density_loc": "Function/class penalties of files above this many code lines "
    "are divided by sqrt(loc / density_loc).",
    "primitive_obsession.min_params": "Minimum typed parameters in a file before the check runs.",
    "duplication.min_tokens": "Minimum tokens in a clone.",
    "duplication.min_lines": "Minimum lines in a clone.",
    "duplication.cross_file": "Also find clones across files in a scan "
    "(delta compares within each file).",
    "delta.tolerance": "Score drops up to this are ignored when no smell is new or worse.",
    "dart.test_closure_calls": "Calls whose closure arguments are scored as units "
    "of their own (test closures).",
    "swift.test_closure_calls": "Calls whose closure arguments are scored as units "
    "of their own (Quick specs).",
    "rust.score_test_modules": "Score `#[cfg(test)]` modules too.",
    "typescript.test_closure_calls": "Calls whose callback arguments are scored as "
    "units of their own (describe/it/test).",
    "javascript.test_closure_calls": "Calls whose callback arguments are scored as "
    "units of their own (describe/it/test).",
    "go.count_error_checks": "Count `if err != nil { return .. }` towards complexity.",
    "go.test_closure_calls": "Methods whose func literal argument is a unit of its own "
    "(t.Run subtests).",
}

OVERRIDES_DOC = (
    "[[overrides]] tables set weights and thresholds for the files their `paths` globs "
    "match (relative to the project root, matched like scan.exclude). Every matching "
    "table applies, later tables win."
)


class ConfigError(ValueError):
    pass


@dataclass
class Config:
    data: dict[str, Any] = field(default_factory=lambda: copy.deepcopy(DEFAULTS))
    source: Path | None = None
    rules: list[PathRule] = field(default_factory=list)  # `[[overrides]]`
    _per_path: dict[tuple[int, ...], Config] = field(
        default_factory=dict, repr=False, compare=False
    )

    def threshold(self, smell: str) -> float:
        return float(self.data["thresholds"][smell])

    def weight(self, smell: str) -> float:
        return float(self.data["weights"][smell])

    def section(self, name: str) -> dict[str, Any]:
        return self.data[name]

    def for_path(self, rel_path: str) -> Config:
        """This config with the `[[overrides]]` that match ``rel_path`` applied."""
        rules = self.rules
        matched = self.matching_rules(rel_path)
        if not matched:
            return self
        if matched not in self._per_path:
            data = copy.deepcopy(self.data)
            for i in matched:
                for table, values in rules[i].tables.items():
                    data[table].update(values)
            self._per_path[matched] = Config(data, self.source)
        return self._per_path[matched]

    def matching_rules(self, rel_path: str) -> tuple[int, ...]:
        """Indexes of the `[[overrides]]` whose paths match ``rel_path``."""
        return tuple(i for i, rule in enumerate(self.rules) if path_matches(rel_path, rule.paths))

    def weight_anywhere(self, smell: str) -> bool:
        """Whether ``smell`` has a positive weight for at least some files."""
        weights = [self.weight(smell)] + [r.value("weights", smell) for r in self.rules]
        return any(w is not None and w > 0 for w in weights)

    @property
    def excludes(self) -> list[str]:
        scan = self.data["scan"]
        base = list(DEFAULT_EXCLUDES) if scan["use_default_excludes"] else []
        return base + list(scan["exclude"])

    def adapter_options(self, language: str) -> dict[str, Any]:
        if language in ("dart", "swift", "typescript", "tsx", "javascript"):
            section = "typescript" if language == "tsx" else language
            calls = self.data[section]["test_closure_calls"]
            return {} if calls is None else {"test_calls": frozenset(calls)}
        if language == "rust":
            return {"score_test_modules": bool(self.data["rust"]["score_test_modules"])}
        if language == "go":
            return self._go_options()
        return {}

    def _go_options(self) -> dict[str, Any]:
        go = self.data["go"]
        options: dict[str, Any] = {"count_error_checks": bool(go["count_error_checks"])}
        if go["test_closure_calls"] is not None:
            options["test_calls"] = frozenset(go["test_closure_calls"])
        return options


def _merge(base: dict[str, Any], override: dict[str, Any], path: str = "") -> None:
    for key, value in override.items():
        where = f"{path}{key}"
        if key not in base:
            raise ConfigError(f"unknown config key: {where}")
        if isinstance(base[key], dict):
            if not isinstance(value, dict):
                raise ConfigError(f"{where} must be a table")
            _merge(base[key], value, f"{where}.")
        else:
            base[key] = value


def load_config(repo: str | Path | None = None, path: str | Path | None = None) -> Config:
    """Defaults merged with `path`, or `<repo>/.aihealth.toml` if it exists."""
    cfg = Config()
    file = Path(path) if path else (Path(repo) / CONFIG_FILE if repo else None)
    if file is None or not file.is_file():
        if path:
            raise ConfigError(f"config file not found: {path}")
        return cfg
    try:
        overrides = tomllib.loads(file.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{file}: {e}") from e
    cfg.rules = _parse_rules(overrides.pop("overrides", []))
    _merge(cfg.data, overrides)
    cfg.source = file
    return cfg


def _parse_rules(raw: Any) -> list[PathRule]:
    try:
        return parse_rules(raw, {t: DEFAULTS[t] for t in OVERRIDABLE})
    except (TypeError, ValueError) as e:
        raise ConfigError(str(e)) from e
