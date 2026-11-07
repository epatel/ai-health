"""Makefile adapter: units, recipe shell complexity, conditionals, duplication."""

import subprocess
from pathlib import Path

import pytest

from aihealth.config import Config
from aihealth.engine import scan, score_source
from aihealth.lang import adapter_for, is_supported, parse_file, parse_source
from aihealth.lang.base import is_generated
from aihealth.lang.make import _neutralise
from aihealth.metrics import function_metrics

FIXTURE = Path(__file__).parent / "fixtures" / "Makefile"


def units(src: str, name: str = "Makefile") -> dict:
    module = parse_source(src.encode(), name)
    adapter = adapter_for(name)
    return {f.qualname: (f, function_metrics(f, adapter)) for f in module.all_functions()}


@pytest.mark.parametrize(
    ("path", "supported"),
    [
        ("Makefile", True),
        ("sub/GNUmakefile", True),
        ("makefile", True),
        ("rules.mk", True),
        ("BSDmakefile", True),
        ("Makefile.am", False),  # automake input, not make
        ("makefile.py", True),  # python, by extension
    ],
)
def test_registry_by_name_and_extension(path, supported):
    assert is_supported(path) is supported
    if path == "makefile.py":
        assert adapter_for(path).language == "python"
    elif supported:
        assert adapter_for(path).language == "make"


def test_fixture_units_and_spans():
    module = parse_file(FIXTURE)
    assert module.error_count == 0
    got = {f.qualname: (f.kind, f.start_line, f.end_line) for f in module.all_functions()}
    assert got == {
        "MODE": ("variable", 4, 5),
        "ifeq ($(DEBUG),1)": ("conditional", 6, 16),
        "app": ("rule", 21, 23),
        "%.o": ("rule", 24, 26),
        "test": ("rule", 27, 35),
        "run_tool": ("define", 36, 39),
        "clean": ("rule", 40, 41),
    }
    # rules without a recipe (all, .PHONY) and plain assignments are not units


def test_recipe_shell_complexity_on_real_lines():
    by_q = {
        f.qualname: function_metrics(f, adapter_for("Makefile"))
        for f in parse_file(FIXTURE).all_functions()
    }
    t = by_q["test"]
    # if(1) + `&&`(1) + for(2) + if(3) + `||`(1) + else(1)
    assert (t.cyclomatic, t.cognitive, t.max_nesting) == (6, 9, 3)
    assert t.nesting_line == 30  # the inner `if [ -s $$t ]` line of the Makefile
    # only the if-condition is a condition; `sh $$t || exit 1` is a statement
    assert [(b.line, b.operators) for b in t.bool_exprs if b.is_condition] == [(28, 1)]


def test_make_conditionals_and_functions():
    by_q = {
        f.qualname: function_metrics(f, adapter_for("Makefile"))
        for f in parse_file(FIXTURE).all_functions()
    }
    cond = by_q["ifeq ($(DEBUG),1)"]
    # ifeq(1) + else ifeq(1) + else(1) + nested ifdef at level 1 (2)
    assert (cond.cyclomatic, cond.cognitive, cond.max_nesting) == (4, 5, 2)
    assert by_q["MODE"].cyclomatic == 2  # $(if ...)


def test_sonar_examples_in_recipes():
    src = (
        "primes:\n"
        "\tfor i in 1 2 3; do \\\n"
        "\t  for j in 2 3; do \\\n"
        "\t    if [ $$i -eq $$j ]; then continue 2; fi; \\\n"
        "\t  done; \\\n"
        "\tdone\n"
        "words:\n"
        '\tcase "$(N)" in 1) echo one;; 2) echo couple;; 3) echo few;; *) echo lots;; esac\n'
    )
    got = units(src)
    assert (got["primes"][1].cyclomatic, got["primes"][1].cognitive) == (4, 6)
    assert (got["words"][1].cyclomatic, got["words"][1].cognitive) == (4, 1)  # `*)` is default


def test_define_falls_back_to_make_for_templates():
    # make conditionals inside a define are not bash: parsed as make instead
    src = "define tmpl\nifeq ($(1),debug)\nFLAGS += -g\nendif\nendef\n$(eval $(call tmpl,a))\n"
    f, m = units(src)["tmpl"]
    assert f.kind == "define" and f.body.type == "makefile" and not f.body.has_error
    assert m.cyclomatic == 2  # the ifeq is measured
    # a shell canned recipe stays bash
    f, m = units('define say\nif [ -n "$(1)" ]; then echo $(1); fi\nendef\n')["say"]
    assert f.body.type == "program" and m.cyclomatic == 2


def test_neutralise_make_isms():
    assert _neutralise("sh $$t && $(CC) -o $@ ${X} $(if $(A),b,c)") == (
        "sh $t && MAKEVAR -o MAKEVAR MAKEVAR MAKEVAR"
    )


def test_large_recipe_gets_make_specific_hint():
    lines = "".join(f"\techo step {i}\n" for i in range(80))
    found = {f.smell: f for f in score_source(f"build:\n{lines}".encode(), "Makefile").findings}
    assert "script" in found["large_method"].hint


def test_copied_recipes_are_duplication():
    recipe = "".join(
        f"\t@mkdir -p out/{i} && cp -r src/{i} out/{i} && echo copied {i}\n" for i in range(8)
    )
    found = score_source(f"a:\n{recipe}\nb:\n{recipe}".encode(), "Makefile").findings
    dup = [f for f in found if f.smell == "code_duplication"]
    assert {f.symbol for f in dup} == {"a", "b"}
    assert "pattern rule" in dup[0].hint


def test_grammar_gaps_are_not_reported_as_syntax_errors():
    # valid GNU make the grammar can't parse (substitution ref with `=`)
    module = parse_source(b"Q1 = $(V:1=)\nall:\n\techo $(Q1)\n", "Makefile")
    assert module.root.has_error and module.error_count == 0


@pytest.mark.parametrize(
    "head",
    [
        b"# Makefile.in generated by automake 1.16.5 from Makefile.am.\n",
        b"# CMAKE generated file: DO NOT EDIT!\n",
        b"# Generated by qmake (3.1) (Qt 5.15.2)\n",
        b"// GENERATED CODE - DO NOT MODIFY BY HAND\n",
        b"# -*- coding: utf-8 -*-\n# Generated by the protocol buffer compiler.  DO NOT EDIT!\n",
    ],
)
def test_generated_markers(head):
    assert is_generated(head + b"all:\n\techo hi\n")
    assert not is_generated(b"# Build rules for the app\nall:\n\techo hi\n")


def test_scan_and_delta_include_makefiles_and_skip_generated(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "Makefile").write_text("all:\n\techo hi\n")
    (tmp_path / "gen").mkdir()
    (tmp_path / "gen" / "Makefile").write_text(
        "# CMAKE generated file: DO NOT EDIT!\nall:\n\ttrue\n"
    )
    result = scan(tmp_path, Config())
    assert [f.path for f in result.files] == ["Makefile"]
    assert ("gen/Makefile", "generated") in result.skipped

    from aihealth.delta import run_delta

    nested = "all:\n\tif [ -d a ]; then \\\n\t  for f in a/*; do \\\n\t    if [ -s $$f ]; then \\\n\t      while true; do \\\n\t        echo $$f; \\\n\t      done; \\\n\t    fi; \\\n\t  done; \\\n\tfi\n"
    (tmp_path / "Makefile").write_text(nested)
    delta = run_delta(tmp_path, Config())
    assert delta.verdict == "degraded"
    assert any(c.finding.smell == "deep_nesting" for c in delta.files[0].changes)
