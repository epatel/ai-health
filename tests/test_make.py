"""Makefile adapter: units, recipe shell complexity, conditionals, duplication."""

import subprocess
import time
from pathlib import Path

import pytest

from aihealth.config import Config
from aihealth.engine import scan, score_source
from aihealth.lang import adapter_for, is_supported, parse_file, parse_source
from aihealth.lang.base import is_generated
from aihealth.lang.make import _looks_like_make, _neutralise
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


# --- missing .PHONY ---------------------------------------------------------------


def phony_findings(src: str, config: Config | None = None):
    found = score_source(src.encode(), "Makefile", config).findings
    return [f for f in found if f.smell == "missing_phony"]


UNDECLARED = (
    "all: app\n"
    "app: main.o\n\t$(CC) -o $@ $^\n"
    "clean:\n\trm -f app\n"
    "test: app\n\t./app --selftest\n"
    "lint:\n\truff check .\n"
)


def test_missing_phony_lists_undeclared_targets():
    [f] = phony_findings(UNDECLARED)
    assert f.level == "file" and f.line == 1 and f.value == 4
    assert f.message.endswith("not declared .PHONY: all, clean, test, lint")
    assert f.severity == 3.0  # 1.0 for the first target, +1 per further, capped
    assert ".PHONY" in f.hint
    [one] = phony_findings("clean:\n\trm -f x\n")
    assert one.severity == 1.0 and "1 target looks" in one.message


def test_declared_targets_including_via_variables():
    declared = ".PHONY: all clean\n.PHONY: test lint\n" + UNDECLARED
    assert phony_findings(declared) == []
    via_vars = "P := clean test\nP += lint\nALL := all $(P)\n.PHONY: $(ALL)\n" + UNDECLARED
    assert phony_findings(via_vars) == []
    inside_conditional = "ifeq ($(X),1)\n.PHONY: all clean test lint\nendif\n" + UNDECLARED
    assert phony_findings(inside_conditional) == []


def test_unexpandable_phony_variable_stays_silent():
    # TARGETS comes from the included file: can't tell what's declared
    assert phony_findings("include targets.mk\n.PHONY: $(TARGETS)\n" + UNDECLARED) == []


@pytest.mark.parametrize(
    "rule",
    [
        "build:\n\tmkdir -p build\n",  # creates a directory of its name
        "report.txt:\n\techo hi > $@\n",  # extension, writes $@
        "stamp:\n\ttouch $@\n",  # writes $@
        "FORCE:\n",  # the empty force-target idiom
        "out/%.o: %.c\n\tcc -c $<\n",  # pattern rule with a path
        ".DEFAULT:\n\techo default\n",  # special target
        "$(BIN): main.c\n\tcc -o $(BIN) main.c\n",  # variable target
    ],
)
def test_file_and_special_targets_are_not_flagged(rule):
    assert phony_findings(rule) == []


@pytest.mark.parametrize("name", ["-Os", "-mcpu=cortex-m0plus", "#", "+=", "a=b", "x.o"])
def test_flag_and_assignment_shaped_names_are_never_phony(name):
    from aihealth.lang.make_phony import _looks_phony, _Target

    assert not _looks_phony(name, _Target(line=1, empty=False))
    assert _looks_phony("clean", _Target(line=1, empty=False))


def test_missing_phony_configurable_and_in_delta():
    cfg = Config()
    cfg.data["weights"]["missing_phony"] = 0
    assert phony_findings(UNDECLARED, cfg) == []

    from aihealth.delta import delta_for_contents

    fixed = ".PHONY: all clean test lint\n" + UNDECLARED
    fd = delta_for_contents("Makefile", UNDECLARED.encode(), fixed.encode(), Config())
    assert [(c.kind, c.finding.smell) for c in fd.changes] == [("fixed", "missing_phony")]
    assert fd.verdict == "improved"


def test_other_languages_have_no_language_smells():
    for path in ("a.py", "a.dart"):
        module = parse_source(b"x = 1\n" if path.endswith(".py") else b"int x = 1;\n", path)
        assert adapter_for(path).language_smells(module) == []


def test_define_body_check_is_linear():
    """`^\\s*` ran across blank lines from every line start: a `define`
    body of 40,000 blank lines took 61 s."""
    start = time.perf_counter()
    assert not _looks_like_make("\n" * 80000)
    assert _looks_like_make("\n" * 80000 + "  ifeq ($(X),1)\n")
    assert time.perf_counter() - start < 1
