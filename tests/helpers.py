"""Shared helpers for the per-language adapter tests."""

from aihealth.engine import score_source
from aihealth.lang import adapter_for


def check_base_types(path: str, table: str) -> None:
    """Each line `declared type => base types` (`-` = unknown) holds for
    the adapter of ``path``."""
    adapter = adapter_for(path)
    for line in table.strip().splitlines():
        type_text, bases = line.rsplit(" => ", 1)
        expected = None if bases == "-" else bases.split()
        assert adapter.base_types(type_text) == expected, type_text


def findings_of(src: str, path: str, smell: str) -> list:
    """Findings of one smell when ``src`` is scored as ``path``."""
    return [f for f in score_source(src.encode(), path).findings if f.smell == smell]
