import pytest

from aihealth.cli import main


def test_help_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for cmd in ("scan", "delta", "mcp", "serve"):
        assert cmd in out


def _serve_warning(tmp_path, monkeypatch, capsys, host: str) -> bool:
    import aihealth.web.app as web

    monkeypatch.setattr(web, "serve", lambda *args: None)
    assert main(["serve", "--repo", str(tmp_path), "--host", host]) == 0
    return "no authentication" in capsys.readouterr().err


def test_serve_warns_when_reachable_from_other_machines(tmp_path, monkeypatch, capsys):
    assert _serve_warning(tmp_path, monkeypatch, capsys, "0.0.0.0")


def test_serve_on_loopback_does_not_warn(tmp_path, monkeypatch, capsys):
    assert not _serve_warning(tmp_path, monkeypatch, capsys, "127.0.0.1")


def test_installed_version_is_the_package_version():
    """pyproject.toml reads the version from aihealth.__version__ (one
    source). Fails after a bump until the package is reinstalled."""
    from importlib import metadata

    import aihealth

    assert metadata.version("aihealth") == aihealth.__version__
