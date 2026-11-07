# architecture

How the aihealth engine, CLI, MCP server, storage and dashboard fit together.

## Components

```mermaid
flowchart LR
    subgraph inputs
        WT[working tree / git diff]
        REPO[source tree]
    end
    subgraph engine["aihealth.engine"]
        LANG[language adapters<br/>python, dart]
        TS[tree-sitter parse]
        MET[function + module metrics]
        SM[smell detectors]
        SC[scorer 1-10]
    end
    WT --> DELTA[delta: before/after]
    REPO --> SCAN[scan: all files]
    DELTA --> LANG
    SCAN --> LANG
    LANG --> TS --> MET --> SM --> SC
    SC --> CLI[aihealth CLI]
    SC --> MCP[MCP server stdio]
    SC --> DB[(SQLite .aihealth/health.db)]
    DB --> WEB[FastAPI dashboard]
```

Planned package layout (`src/aihealth/`):

| Module | Owns |
|---|---|
| `model.py` | The uniform model: `Module` (path, language, source, tree root, loc, error_count), `Class` (name, kind: class/mixin/extension/enum), `Function` (name, qualname, kind: function/method/constructor/getter/setter/operator, 1-based line span, `Param`s, body node, loc). `Module.all_functions()` lists every scorable function. |
| `lang/` | One adapter per language (`python.py`, `dart.py`) subclassing `LanguageAdapter` in `base.py`, plus a registry (`adapter_for(path)`, `parse_file`, `parse_source`). Adapters build the model. LoC counting (code lines only, excluding comments and docstrings) is shared in `base.py`. Nested named functions are separate `Function`s with dotted qualnames (`outer.inner`); closures and lambdas stay inside their parent. M3 adds decision-point and nesting node-type sets here. |
| `metrics.py` | Language-neutral metric computation over the uniform model: LoC, cyclomatic, cognitive complexity, max nesting, bumpy-road bumps, argument count, complex conditionals, widget-tree depth (Dart only, adapter-provided). |
| `duplication.py` | Token-based clone detection across functions in a file and across files in a scan. |
| `smells.py` | Thresholds → smell findings (`Finding(path, function, smell, severity, line, detail, hint)`). |
| `scoring.py` | Penalties → file score, LoC-weighted project score, bands. |
| `config.py` | Defaults + `.aihealth.toml` overrides (thresholds, weights, excludes). |
| `delta.py` | Resolves before/after content via git (`git show <rev>:<path>`, index, working tree), pairs functions by qualified name, classifies smells as new / worsened / fixed. |
| `report.py` | Renders results as text, json, or agent-oriented markdown. |
| `cli.py` | `aihealth scan`, `aihealth delta`, `aihealth serve`, `aihealth mcp`. |
| `mcp_server.py` | FastMCP stdio server wrapping the engine. |
| `store/` | SQLite connection, `MIGRATIONS` list, `PRAGMA user_version` runner, scan persistence. |
| `web/` | FastAPI app, Jinja templates, static assets; reads only from the store. |

## Data flow

- **Scan:** walk files (respect `.gitignore` + config excludes) → adapter by
  extension (`.py`, `.dart`) → metrics → smells → file score → project
  score. Optionally persisted as one `scan` row with `file_score` and
  `finding` rows.
- **Delta:** for each changed file of a supported language, score the
  *before* and *after* content independently, then diff findings per
  function. Deleted files are ignored; new files are scored with an empty
  baseline. Verdict: `improved`, `unchanged`, `degraded`; exit code 1 on
  `degraded` so agents and CI can gate on it.
- **MCP:** the server holds no state; each tool call runs the engine on the
  repo path given (default: cwd) and returns compact JSON with hints.
- **Dashboard:** `aihealth serve --repo PATH` opens the repo's DB, runs a scan
  on request, and renders overview, treemap, file detail and trend pages.

## Agent feedback contract

The agent-facing output (MCP and `--format markdown`) is short by design:
verdict, score before → after per touched file, and at most N findings
ranked by penalty, each with function name, line, the metric value vs.
threshold, and a one-line refactoring hint ("extract the nested loop at
L42 into a helper"). Full detail is available via `--format json`.

## Deployment shape

Local developer tool. Installed into a venv (`pip install -e .`), run as a
CLI, as an MCP server launched by the agent host over stdio, or as a local
web server on `127.0.0.1`. No network services, no auth.
