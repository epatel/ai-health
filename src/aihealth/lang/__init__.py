"""Language adapter registry."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

from aihealth.lang.base import LanguageAdapter
from aihealth.lang.dart import DartAdapter
from aihealth.lang.make import MakeAdapter
from aihealth.lang.python import PythonAdapter
from aihealth.lang.swift import SwiftAdapter
from aihealth.model import Module

if TYPE_CHECKING:
    from aihealth.config import Config

ADAPTERS: tuple[type[LanguageAdapter], ...] = (
    PythonAdapter,
    DartAdapter,
    MakeAdapter,
    SwiftAdapter,
)

_BY_EXT = {ext: cls for cls in ADAPTERS for ext in cls.extensions}
_BY_NAME = {name: cls for cls in ADAPTERS for name in cls.filenames}

SUPPORTED_EXTENSIONS = frozenset(_BY_EXT)


def _adapter_class(path: str | Path) -> type[LanguageAdapter] | None:
    p = Path(path)
    return _BY_NAME.get(p.name) or _BY_EXT.get(p.suffix.lower())


def is_supported(path: str | Path) -> bool:
    """Does any adapter handle this file (by exact name or extension)?"""
    return _adapter_class(path) is not None


@cache
def _instance(cls: type[LanguageAdapter], options: tuple) -> LanguageAdapter:
    return cls(**dict(options))


def adapter_for(path: str | Path, config: Config | None = None) -> LanguageAdapter | None:
    """Adapter for a file path by exact name or extension, or None."""
    cls = _adapter_class(path)
    if cls is None:
        return None
    options = config.adapter_options(cls.language) if config else {}
    return _instance(cls, tuple(sorted(options.items())))


def parse_source(source: bytes, path: str | Path, config: Config | None = None) -> Module | None:
    adapter = adapter_for(path, config)
    return adapter.parse(source, str(path)) if adapter else None


def parse_file(path: str | Path, config: Config | None = None) -> Module | None:
    adapter = adapter_for(path, config)
    if adapter is None:
        return None
    return adapter.parse(Path(path).read_bytes(), str(path))
