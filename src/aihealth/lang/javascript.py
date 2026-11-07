"""JavaScript adapter (tree-sitter `javascript` grammar, JSX included).

The grammar's node names match TypeScript's (see lang/typescript.py), so this
reuses the TSX adapter. Differences handled there: parameters are bare
patterns (`x`, `x = 1`, `...xs`), class fields are `field_definition`, and
functions assigned to a name are units here (`assigned_units`): CommonJS and
pre-class code defines functions as `module.exports = {...}`,
`app.handle = function handle() {...}` or `Foo.prototype.bar = function`.
"""

from __future__ import annotations

from typing import ClassVar

from aihealth.lang.typescript import TsxAdapter


class JavaScriptAdapter(TsxAdapter):
    language = "javascript"
    extensions = (".js", ".mjs", ".cjs", ".jsx")
    assigned_units: ClassVar[bool] = True
