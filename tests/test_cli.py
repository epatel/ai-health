import pytest

from aihealth.cli import main


def test_help_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for cmd in ("scan", "delta", "mcp", "serve"):
        assert cmd in out


def test_stub_commands_report_milestone(capsys):
    assert main(["delta"]) == 2
    assert "M5" in capsys.readouterr().err
