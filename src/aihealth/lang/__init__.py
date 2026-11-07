"""Language adapter registry."""

from __future__ import annotations

import os
import stat
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

from aihealth.lang.base import LanguageAdapter
from aihealth.lang.cfamily import CFamilyAdapter
from aihealth.lang.cmake import CMakeAdapter
from aihealth.lang.dart import DartAdapter
from aihealth.lang.javascript import JavaScriptAdapter
from aihealth.lang.make import MakeAdapter
from aihealth.lang.python import PythonAdapter
from aihealth.lang.rust import RustAdapter
from aihealth.lang.shell import ShellAdapter
from aihealth.lang.shell_zsh import is_shell_head
from aihealth.lang.swift import SwiftAdapter
from aihealth.lang.typescript import TsxAdapter, TypeScriptAdapter
from aihealth.model import Module

if TYPE_CHECKING:
    from aihealth.config import Config

ADAPTERS: tuple[type[LanguageAdapter], ...] = (
    PythonAdapter,
    DartAdapter,
    MakeAdapter,
    SwiftAdapter,
    RustAdapter,
    TypeScriptAdapter,
    TsxAdapter,
    JavaScriptAdapter,
    CMakeAdapter,
    CFamilyAdapter,
    ShellAdapter,
)

_BY_EXT = {ext: cls for cls in ADAPTERS for ext in cls.extensions}
_BY_NAME = {name: cls for cls in ADAPTERS for name in cls.filenames}

SUPPORTED_EXTENSIONS = frozenset(_BY_EXT)
# for messages: keep in sync with ADAPTERS
SUPPORTED_LANGUAGES = (
    "Python, Dart, Swift, Rust, TypeScript, JavaScript, C, C++, Objective-C(++), "
    "shell scripts (bash, zsh), Makefiles and CMake"
)

# An extensionless file starting with a sh/bash/dash/ksh/zsh shebang is a
# shell script (`bin/deploy`, rbenv's `libexec/rbenv-init`), and so is a zsh
# completion or autoload function (`_docker`: `#compdef docker`).
_HEAD_BYTES = 128


def may_be_script(path: str | Path) -> bool:
    """Extensionless and not matched by name: only its shebang can tell."""
    p = Path(path)
    return not p.suffix and p.name not in _BY_NAME


def file_head(path: str | Path) -> bytes | None:
    """The first bytes of a regular file on disk, enough for a shebang, or
    None (also for a named pipe, whose `open` would block until a writer)."""
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            return None
        with open(path, "rb") as f:
            return f.read(_HEAD_BYTES)
    except OSError:
        return None


def _adapter_class(path: str | Path, head: bytes | None) -> type[LanguageAdapter] | None:
    p = Path(path)
    cls = _BY_NAME.get(p.name) or _BY_EXT.get(p.suffix.lower())
    if cls is None and _is_shell_script(p, head):
        return ShellAdapter
    return cls


def _is_shell_script(path: Path, head: bytes | None) -> bool:
    """Extensionless, starting with a shell shebang (``head``)."""
    return bool(head) and may_be_script(path) and is_shell_head(head)


def is_supported(path: str | Path, head: bytes | None = None) -> bool:
    """Does any adapter handle this file (by exact name or extension, or for
    an extensionless file by the shebang in ``head``, its first bytes)?"""
    return _adapter_class(path, head) is not None


def is_supported_file(path: str | Path) -> bool:
    """`is_supported` for a file on disk, reading its head when needed."""
    return is_supported(path, file_head(path) if may_be_script(path) else None)


@cache
def _instance(cls: type[LanguageAdapter], options: tuple) -> LanguageAdapter:
    return cls(**dict(options))


def adapter_for(
    path: str | Path, config: Config | None = None, head: bytes | None = None
) -> LanguageAdapter | None:
    """Adapter for a file path by exact name or extension (or shebang in
    ``head``, the file's first bytes or whole source), or None."""
    cls = _adapter_class(path, head)
    if cls is None:
        return None
    options = config.adapter_options(cls.language) if config else {}
    return _instance(cls, tuple(sorted(options.items())))


def parse_source(source: bytes, path: str | Path, config: Config | None = None) -> Module | None:
    adapter = adapter_for(path, config, source)
    return adapter.parse(source, str(path)) if adapter else None


def parse_file(path: str | Path, config: Config | None = None) -> Module | None:
    return parse_source(Path(path).read_bytes(), path, config)
