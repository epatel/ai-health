"""`aihealth config`: every setting documented, rendered as text, json and toml."""

import json
import tomllib

from aihealth.cli import main
from aihealth.config import DEFAULTS, KEY_DOCS, SECTION_DOCS, load_config
from aihealth.config_report import render_config, settings
from aihealth.smells import LABELS

CUSTOM = """
[thresholds]
large_method = 100
[scan]
exclude = ["legacy/*"]
[dart]
test_closure_calls = ["group", "test"]
[[overrides]]
paths = ["tests/*"]
weights = { code_duplication = 0.25 }
"""


def test_every_setting_is_documented():
    assert set(SECTION_DOCS) == set(DEFAULTS)
    keys = {f"{section}.{key}" for section, table in DEFAULTS.items() for key in table}
    assert set(KEY_DOCS) == {k for k in keys if not k.startswith("weights.")}
    assert set(DEFAULTS["weights"]) <= set(LABELS)


def test_builtin_lists_shown_as_defaults():
    by_key = {(s.section, s.key): s for s in settings(load_config())}
    go = by_key["go", "test_closure_calls"]
    assert go.default == ["Run"] and not go.changed and go.type == "list of strings"
    assert "it" in by_key["typescript", "test_closure_calls"].default


def test_json_marks_changed_values(tmp_path):
    (tmp_path / ".aihealth.toml").write_text(CUSTOM)
    data = json.loads(render_config(load_config(tmp_path), "json"))
    by_key = {(s["section"], s["key"]): s for s in data["settings"]}
    lm = by_key["thresholds", "large_method"]
    assert (lm["value"], lm["default"], lm["changed"], lm["type"]) == (100, 70, True, "number")
    assert not by_key["thresholds", "complex_method"]["changed"]
    assert data["overrides"]["rules"] == [
        {"paths": ["tests/*"], "tables": {"weights": {"code_duplication": 0.25}}}
    ]
    assert data["source"].endswith(".aihealth.toml")


def test_toml_loads_back_to_the_same_config(tmp_path):
    for content in ("", CUSTOM):
        (tmp_path / ".aihealth.toml").write_text(content)
        before = load_config(tmp_path)
        rendered = render_config(before, "toml")
        tomllib.loads(rendered)  # valid TOML
        (tmp_path / ".aihealth.toml").write_text(rendered)
        after = load_config(tmp_path)
        assert settings(after) == settings(before)
        assert after.rules == before.rules
    assert "# [[overrides]]" in render_config(load_config(), "toml")  # example when unused


def test_text_lists_sections_and_overrides(tmp_path):
    (tmp_path / ".aihealth.toml").write_text(CUSTOM)
    text = render_config(load_config(tmp_path), "text")
    assert "[duplication]" in text and "[[overrides]]" in text and "in use:" in text
    assert "large_method = 100  (default 70)" in text


def test_cli_config(tmp_path, capsys):
    (tmp_path / ".aihealth.toml").write_text(CUSTOM)
    assert main(["config", str(tmp_path), "--format", "toml"]) == 0
    assert tomllib.loads(capsys.readouterr().out)["thresholds"]["large_method"] == 100
    (tmp_path / ".aihealth.toml").write_text("[weights]\nnope = 1\n")
    assert main(["config", str(tmp_path)]) == 2
    assert "nope" in capsys.readouterr().err
