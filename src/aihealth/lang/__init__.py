"""Language adapter registry."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from aihealth.lang.base import LanguageAdapter
from aihealth.lang.dart import DartAdapter
from aihealth.lang.python import PythonAdapter
from aihealth.model import Module

ADAPTERS: tuple[type[LanguageAdapter], ...] = (PythonAdapter, DartAdapter)

_BY_EXT = {ext: cls for cls in ADAPTERS for ext in cls.extensions}


@cache
def _instance(cls: type[LanguageAdapter]) -> LanguageAdapter:
    return cls()


def adapter_for(path: str | Path) -> LanguageAdapter | None:
    """Adapter for a file path by extension, or None if unsupported."""
    cls = _BY_EXT.get(Path(path).suffix.lower())
    return _instance(cls) if cls else None


def parse_source(source: bytes, path: str | Path) -> Module | None:
    adapter = adapter_for(path)
    return adapter.parse(source, str(path)) if adapter else None


def parse_file(path: str | Path) -> Module | None:
    adapter = adapter_for(path)
    if adapter is None:
        return None
    return adapter.parse(Path(path).read_bytes(), str(path))
