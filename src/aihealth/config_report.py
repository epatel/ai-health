"""Every setting of `.aihealth.toml` with its default, the project's value and
what it means: `aihealth config` and the `list_config` MCP tool.

Formats: `text` (grouped, short), `json` (one entry per key) and `toml` (a
complete, commented `.aihealth.toml` that loads back to the same config).
"""

from __future__ import annotations

import importlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from aihealth.config import (
    DEFAULTS,
    KEY_DOCS,
    OVERRIDES_DOC,
    SECTION_DOCS,
    Config,
)
from aihealth.overrides import OVERRIDABLE

CONFIG_FORMATS = ("text", "json", "toml")

# adapters whose `test_closure_calls` default (None) means their built-in list
_BUILTIN_TEST_CALLS = {
    "dart": "aihealth.lang.dart",
    "swift": "aihealth.lang.swift",
    "typescript": "aihealth.lang.typescript",
    "javascript": "aihealth.lang.typescript",
    "go": "aihealth.lang.go",
}

_OVERRIDE_EXAMPLE = {
    "paths": ["tests/*", "legacy/*"],
    "tables": {"weights": {"code_duplication": 0.25}, "thresholds": {"large_method": 120}},
}


@dataclass(frozen=True)
class Setting:
    section: str
    key: str
    type: str
    default: Any
    value: Any
    description: str

    @property
    def changed(self) -> bool:
        return self.value != self.default


def settings(config: Config) -> list[Setting]:
    return [
        _setting(section, key, config.data[section][key])
        for section, table in DEFAULTS.items()
        for key in table
    ]


def _setting(section: str, key: str, value: Any) -> Setting:
    default = _resolved(section, key, DEFAULTS[section][key])
    return Setting(
        section,
        key,
        _type_name(default),
        default,
        _resolved(section, key, value),
        _description(section, key),
    )


def _resolved(section: str, key: str, value: Any) -> Any:
    """None stands for an adapter's built-in list: show the list itself."""
    if value is None and key == "test_closure_calls":
        module = importlib.import_module(_BUILTIN_TEST_CALLS[section])
        return sorted(module.DEFAULT_TEST_CALLS)
    return list(value) if isinstance(value, list | tuple) else value


def _description(section: str, key: str) -> str:
    if section == "weights":
        from aihealth.smells import LABELS

        return f"Weight of {LABELS[key]} findings (0 turns it off)."
    return KEY_DOCS[f"{section}.{key}"]


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, list):
        return "list of strings"
    return "string"


def render_config(config: Config, fmt: str = "text") -> str:
    items = settings(config)
    if fmt == "json":
        return _json(config, items)
    if fmt == "toml":
        return _toml(config, items)
    return _text(config, items)


def _source(config: Config) -> str:
    return str(config.source) if config.source else "built-in defaults (no .aihealth.toml)"


# --- text ------------------------------------------------------------------------


def _text(config: Config, items: list[Setting]) -> str:
    lines = [f"aihealth settings, from: {_source(config)}", "Keys go in .aihealth.toml."]
    for section, group in _grouped(items):
        lines += ["", f"[{section}]  {SECTION_DOCS[section]}"]
        lines += [f"  {s.key} = {_toml_value(s.value)}{_was(s)}  # {s.description}" for s in group]
    lines += ["", f"[[overrides]]  {OVERRIDES_DOC}"]
    lines += [f"  {line}" for line in _rule_lines(_OVERRIDE_EXAMPLE)]
    lines += _current_rules(config, "  ")
    return "\n".join(lines)


def _grouped(items: list[Setting]) -> list[tuple[str, list[Setting]]]:
    groups: dict[str, list[Setting]] = {}
    for s in items:
        groups.setdefault(s.section, []).append(s)
    return list(groups.items())


def _was(s: Setting) -> str:
    return f"  (default {_toml_value(s.default)})" if s.changed else ""


def _current_rules(config: Config, indent: str) -> list[str]:
    if not config.rules:
        return []
    lines = ["", f"{indent}in use:"]
    for rule in config.rules:
        lines += [f"{indent}{line}" for line in _rule_lines(asdict(rule))]
    return lines


# --- json ------------------------------------------------------------------------


def _json(config: Config, items: list[Setting]) -> str:
    data = {
        "source": str(config.source) if config.source else None,
        "sections": SECTION_DOCS,
        "settings": [{**asdict(s), "changed": s.changed} for s in items],
        "overrides": {
            "description": OVERRIDES_DOC,
            "tables": list(OVERRIDABLE),
            "example": _OVERRIDE_EXAMPLE,
            "rules": [asdict(rule) for rule in config.rules],
        },
    }
    return json.dumps(data, indent=2)


# --- toml ------------------------------------------------------------------------


def _toml(config: Config, items: list[Setting]) -> str:
    lines = [
        "# .aihealth.toml: every aihealth setting, with its description.",
        f"# Values from: {_source(config)}",
        "# Keep only the keys you change; the rest fall back to the defaults.",
    ]
    for section, group in _grouped(items):
        lines += ["", f"# {SECTION_DOCS[section]}", f"[{section}]"]
        lines += [f"{s.key} = {_toml_value(s.value)}  # {s.description}{_was(s)}" for s in group]
    lines += ["", *(f"# {line}" for line in _wrap(OVERRIDES_DOC))]
    return "\n".join(lines + _toml_rules(config)) + "\n"


def _toml_rules(config: Config) -> list[str]:
    """The project's rules, or a commented-out example."""
    if not config.rules:
        return [f"# {line}" for line in _rule_lines(_OVERRIDE_EXAMPLE)]
    lines = []
    for rule in config.rules:
        lines += ["", *_rule_lines(asdict(rule))]
    return lines


def _rule_lines(rule: dict[str, Any]) -> list[str]:
    lines = ["[[overrides]]", f"paths = {_toml_value(list(rule['paths']))}"]
    for table, values in rule["tables"].items():
        pairs = ", ".join(f"{k} = {_toml_value(v)}" for k, v in values.items())
        lines.append(f"{table} = {{ {pairs} }}")
    return lines


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, str):
        return json.dumps(value)  # a valid TOML basic string
    return repr(value)


def _wrap(text: str, width: int = 76) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return [*lines, line]
