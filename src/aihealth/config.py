"""Configuration: built-in defaults, overridden by `<repo>/.aihealth.toml`.

Example `.aihealth.toml`::

    [thresholds]
    large_method = 100          # LoC
    deep_nesting = 4

    [weights]
    primitive_obsession = 0     # 0 disables a smell

    [scan]
    exclude = ["legacy/**"]     # added to the default excludes

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
"""

from __future__ import annotations

import copy
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
}


class ConfigError(ValueError):
    pass


@dataclass
class Config:
    data: dict[str, Any] = field(default_factory=lambda: copy.deepcopy(DEFAULTS))
    source: Path | None = None

    def threshold(self, smell: str) -> float:
        return float(self.data["thresholds"][smell])

    def weight(self, smell: str) -> float:
        return float(self.data["weights"][smell])

    def section(self, name: str) -> dict[str, Any]:
        return self.data[name]

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
        return {}


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
    _merge(cfg.data, overrides)
    cfg.source = file
    return cfg
