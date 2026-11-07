# Audit report: aihealth

**Date:** 2026-11-07 · **Commit audited:** `10a08ab` (main) · **Auditor:** Claude Code (Opus 5.5)

## Summary

aihealth is in good shape for a 0.1.0 tool. The code is small (5.9k lines in
`src/`), well-tested (258 tests, about 96 % line coverage), lint-clean, and
scores 9.91 on its own health check. The design is well documented in
`cards/`. Calibration and behaviour-preservation are backed by reproducible
scripts.

The audit found **one real security bug**: option injection through the
CLI's `--head`, which can make git overwrite an arbitrary file. Exposure is
limited because the MCP tools can't reach it. There are also a handful of
hardening and maintenance items. None of them blocks use.

| # | Finding | Area | Severity |
|---|---|---|---|
| 1 | `delta --head=--output=FILE` makes git create/overwrite any writable file | Security | **Medium**, fixed |
| 2 | Dashboard: no CSRF check on `POST /scan`, no Host check (DNS rebinding) | Security | Low |
| 3 | `serve --host 0.0.0.0` exposes an unauthenticated dashboard with no warning | Security | Low |
| 4 | ECharts loaded from a CDN without Subresource Integrity | Security | Low |
| 5 | No file-size limit in delta / hooks / MCP (a 4 MB file takes 8.6 s) | Robustness | Low |
| 6 | Dependencies have open-ended versions and no lockfile; scores depend on the grammar version | Reproducibility | Medium |
| 7 | No CI: tests and lint run only on a developer machine | Process | Medium |
| 8 | MCP server registration and error mapping are untested | Tests | Low |
| 9 | Only Python 3.12 is tested, though `requires-python >= 3.11` | Packaging | Low |
| 10 | Remaining refactor target `smells.py` (8.64); stale test count in the plan | Maintenance | Info |
| 11 | A scanned repo's `.git/config` (`core.fsmonitor`) runs commands (found in the re-audit) | Security | Low, fixed |

---

## Method

- Read the trust boundaries in full: `gitutil.py`, `mcp_server.py`,
  `web/app.py`, `hooks.py`, `store/`, the templates and the CLI argument
  handling.
- Ran attack probes in throwaway git repositories: revisions starting with
  `-` via the CLI and via the MCP tool functions.
- Grepped all SQL statements, template output filters and subprocess calls.
- Ran the test suite under `sys.monitoring` to measure line coverage (no
  coverage package is installed).
- Timed a large-file parse, ran ruff and checked installed dependency
  versions. Ran `aihealth scan .` on the repo itself.
- Out of scope: third-party dependency CVE scanning (no network audit tool
  available) and fuzzing the tree-sitter grammars.

---

## Findings

### 1. Git option injection via `--head` (Medium, fixed)

**Status: fixed** after the audit. `gitutil` rejects revisions that start
with `-` and passes `--end-of-options` before revisions to `diff`,
`ls-tree` and `show`. `delta` checks that `head` exists, as it already did
for `base`. Regression tests in `tests/test_delta.py` cover both CLI flags
and every git call; they fail on the old code. The original finding
follows.

`gitutil._diff_args`, `_all_files` and `read_file` pass revisions to git as
positional arguments. `base` is validated first (`has_revision` runs
`git rev-parse --verify`, which rejects `--output=…`), but **`head` is
never validated**. Reproduced:

```console
$ aihealth delta --base HEAD~1 "--head=--output=/tmp/pwned.txt"
aihealth delta: --output=/tmp/pwned.txt vs HEAD~1: UNCHANGED
$ ls -la /tmp/pwned.txt
-rw-r--r--  7 bytes        # created (or truncated) by git diff --output
```

`git show --output=…` (in `read_file`) and `ls-tree` (no-base path) share
the same pattern.

- **Exposure:** only the CLI. The MCP tool `review_changes` takes `base`,
  which is validated, and doesn't accept `head`, so a prompt-injected agent
  can't use this. `base` via the CLI is rejected as `unknown revision`. Git
  branch names can't start with `-`, so a CI job passing a branch name is
  safe. It matters when a wrapper passes untrusted text as `--head`.
- **Fix:** validate `head` like `base` (`has_revision` unless it is
  WORKTREE/INDEX). As defence in depth, put `--end-of-options` before
  revisions in `diff`/`ls-tree`/`show` (git ≥ 2.24). Add a regression test
  with `--output=` for both flags.

### 2. Dashboard: CSRF on `POST /scan` and DNS rebinding (Low)

`web/app.py` has no CSRF token, no Origin/Referer check and no
`TrustedHostMiddleware`.

- **CSRF:** any web page the user visits can submit a form to
  `http://127.0.0.1:8765/scan`. That starts a full scan and writes a row to
  the history DB: wasted CPU and DB growth, no data leak.
- **DNS rebinding:** a malicious site that rebinds its hostname to
  127.0.0.1 can read the JSON API (`/api/scans`, `/api/scans/{id}/file`).
  That reveals file paths, function names and findings of the scanned repo.
  It does not reveal source code.
- **Fix:** add `TrustedHostMiddleware(allowed_hosts=["127.0.0.1", "localhost", <--host>])`
  and reject `POST /scan` when `Origin` is present and not the dashboard's
  own origin.

### 3. Binding the dashboard to all interfaces (Low)

`aihealth serve --host 0.0.0.0` works, with no authentication and no
warning. Anyone on the network can then browse findings and trigger scans.
**Fix:** print a warning for non-loopback hosts, and document it in the
README.

### 4. CDN script without integrity (Low)

`templates/base.html` loads
`https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js` with no
`integrity`/`crossorigin` attributes. A compromised CDN response would run
in the dashboard origin, which can trigger scans via `POST /scan`. The
version is pinned, which is good. **Fix:** add the SRI hash, or vendor the
file into `web/static/` (about 1 MB). That would also make the dashboard
work offline.

### 5. No size limit outside `scan` (Low)

`scan.max_file_kb` (1 MB) is applied only in `engine.discover`. Delta,
`aihealth hook post-edit` and the MCP tools parse any file whatever its
size. Measured: a 4 MB Python file takes **8.6 s** to score. Each agent edit
of a large, unmarked generated file (a big JSON-in-Python fixture, an
amalgamated source) would stall the post-edit hook by that much.
**Fix:** apply `max_file_kb` in `delta_for_contents` / `score_source` callers
and skip with a note, as scan already does.

### 6. Dependency versions and score reproducibility (Medium)

`pyproject.toml` uses lower bounds only (`tree-sitter>=0.23`,
`tree-sitter-language-pack>=0.7`, `mcp>=2,<3`, …), and there is no
lockfile. The installed `tree-sitter-language-pack` is **1.25.0**, far
beyond the declared floor.

- Grammar upgrades rename nodes or reshape trees. In this repo the spike
  tests catch that. A user who installs later can get a newer grammar
  that was never tested, and adapters degrade *silently*: unknown node
  types just get no role, so complexity drops.
- Scan history stores `tool_version` and `config_hash`, but not the
  grammar version. A score jump after an upgrade is then unexplained in
  `aihealth history` and the dashboard trend.
- **Fix:** add tested upper bounds (`tree-sitter-language-pack>=1.25,<2`,
  `tree-sitter>=0.26,<0.27`), or commit a `uv.lock` for reproducible
  installs. Record the grammar package version in the `scan` row (a new
  migration), and show a marker in the trend when it changes.

### 7. No continuous integration (Medium)

There is no `.github/workflows`. The test suite, ruff and the self-scan run
only through local hooks and the developer's discipline. Contributors'
pull requests, and dependency drift (see 6), would go unnoticed. **Fix:** a
small GitHub Actions workflow: `make setup`, `make test`, `make lint`, and
`aihealth scan . --fail-under 9` on Python 3.11 and 3.12.

### 8. Untested MCP wiring (Low)

The tool functions are tested directly (`tests/test_mcp.py`), but
`build_server()` and `_as_tool_errors` are not executed by any test. These
are the parts that depend on the `mcp` SDK's API (`MCPServer`, `ToolError`,
`structured_output`). An SDK minor release that changed them would break
the server while all tests stay green. **Fix:** one test that builds the
server, lists its tools, calls one with bad input and asserts a `ToolError`
with the message.

Other untested lines are mostly error paths: the CLI's config-error and
schema-too-new exits, `--staged` together with `--head`, and OSError
branches in `engine`. Overall line coverage: about 96 % (3,641 / 3,802
lines). Lowest: `cli.py` 83 %, `mcp_server.py` 88 %, `engine.py` and
`gitutil.py` 92 %.

### 9. Python versions (Low)

`requires-python = ">=3.11"`, and ruff targets `py311`, but everything is
developed and tested on 3.12. Nothing obviously 3.12-only was found
(`tomllib` is 3.11+, and the coverage measurement used `sys.monitoring` but
that is not in the package). It is unverified, though. CI (item 7) should
cover it.

### 10. Maintenance notes (Info)

- `smells.py` scores 8.64: `_Detector.add` takes 10 parameters, and
  `_Detector.duplication` and `lcom4` are complex. It is already the
  documented next refactor target.
- `project-plan.md` says "256 tests". The suite now has 258.
- The calibration corpora for Swift are shallow clones outside the repo,
  configured by `AIHEALTH_SWIFT_CORPORA`. A small script that clones them
  at pinned commits would make calibration reproducible for others.

---

## Verified as sound

- **SQL:** every query in `store/` uses `?` parameters. The only f-string
  SQL is in `migrations.py`, and it is built from in-code constants.
- **Templates:** Jinja2 autoescape is on (the default for `.html` in
  `Jinja2Templates`). Embedded chart data goes through `|tojson`, which
  escapes `<`, `>` and `&`, so file names or qualnames from a scanned repo
  can't break out of the `<script type="application/json">` block. There
  is no `|safe` or `Markup` anywhere.
- **Subprocesses:** git is called with argument lists, never a shell. Paths
  are passed after `--`. Only the revision positions are open (finding 1).
- **MCP tools:** `base` is validated, `head` is not exposed, and tool input
  errors map to `ToolError` messages. `score_file` / `preview_change` can
  read any path the user can, but they return only scores and findings,
  never file content.
- **Hooks:** they read only the documented hook JSON fields, catch every
  exception (fail open, exit 0), and never fire twice in a row
  (`stop_hook_active`). The `AGENT-INSTALL.md` wrapper
  (`command -v aihealth … || true`) was tested with and without aihealth
  on PATH.
- **Writes:** `scan --save` and the dashboard write only `.aihealth/` in the
  target repo (which gitignores itself), or the `--db` path. The analysis
  itself is read-only.
- **Parsing robustness:** all walks are iterative (no recursion limit on
  deep trees). The Swift adapter ran with 0 crashes on 5,646 real files,
  and the Dart adapter on the Flutter SDK. Grammar error recovery is
  handled, and parse errors are reported per file.
- **Behaviour preservation:** the snapshot tooling shows byte-identical
  results for 59,800 Python/Dart files and 1,217 Makefiles across the Swift
  work.
- **Packaging:** installing from GitHub (`uv tool install git+…`) works,
  and the dashboard templates and static files are included.
- **Lint and health:** ruff is clean, and the repo scores 9.91 with 42 of
  43 files healthy.

## Known limitations (documented, not defects)

These are recorded in the cards. They're listed here so the audit is
complete:

- Delta checks duplication within each file only. Cross-file clones
  introduced by a change are not detected.
- Scores depend on syntax only, with no type information. Primitive
  Obsession ignores untyped Python parameters.
- Grammar gaps produce partial results: Makefile substitution references,
  Swift `@_lifetime` / `__owned` and `f(())`.
- Swift lookup-table clones (enum switches mapping cases to values) are a
  measured, accepted false positive (0.5 % of clones).

## Recommended order

1. ~~Fix finding 1~~ (done).
2. Add CI (7) with a 3.11/3.12 matrix (9) and the MCP wiring test (8).
3. Pin tested dependency ranges or add a lockfile, and record the grammar
   version in scans (6).
4. Dashboard hardening: Host check, Origin check, SRI or vendored ECharts,
   warning for non-loopback hosts (2–4).
5. Size limit in delta, hooks and MCP (5).

---

## Re-audit of the follow-up changes

**Date:** 2026-11-07 · **Range:** `10a08ab..2c19ce9` (the finding 1 fix and the
`AGENT-INSTALL.md` rewrite)

### The finding 1 fix: sound

- Every revision that reaches git goes through `_rev()` (rejects a leading
  `-`) and sits after `--end-of-options`: `diff` (all three head modes),
  `ls-tree` (no-base path) and `show`. `has_revision` returns False for
  option-like input before calling git. File paths were already after `--`.
- `delta` now validates `head` as well as `base`, so a typo gives
  `unknown revision` (exit 2) instead of a raw git error.
- The reproduction from finding 1 now exits 2 and creates no file. The four
  new tests fail on the old code and pass on the new. Rename pairing, staged
  and commit-range tests still pass (262 in total).
- **Side effect:** `--end-of-options` needs **git 2.24+** (released 2019).
  On older git every delta call fails. This is now stated in the README
  Install section. Rejecting a leading `-` alone would also block the
  attack, so the flag can be dropped if older git matters.

### 11. A scanned repo's own git config can run commands (new, Low, fixed)

**Status: fixed.** Every git call (`gitutil` and `engine._git_files`) now
starts with `gitutil.GIT = ("git", "-c", "core.fsmonitor=false")`; config
on the command line overrides the repo's. The reproduction below now runs
the command 0 times (14 before). `test_repo_fsmonitor_command_never_runs`
covers scan, `scan --save`, delta and the post-edit hook, and fails on the
old code. Filter drivers remain, as noted below.

Not caused by the recent changes: the first audit missed it. aihealth runs
git inside the target directory (`ls-files` for scan, `status` for
`--save`, `diff` for delta and the hooks), and git reads that
directory's `.git/config`. A `core.fsmonitor` value there is run as a
shell command. Reproduced in a throwaway repo: plain `aihealth scan .`
ran it 4 times, `scan --save` 6 more and `delta` 4 more. Clean/smudge
filters from `.gitattributes` plus config are a second route for
`diff`/`status`.

- **Exposure:** only directories whose `.git/config` an attacker wrote:
  a downloaded archive or a shared folder that includes `.git`.
  `git clone` never copies `.git/config`. The user's own `git status` in
  that directory, and Claude Code's startup `git status`, run the same
  command, so aihealth adds no new exposure beyond git's own threat model.
- **Fix (cheap hardening):** pass `-c core.fsmonitor=false` on every git
  call in `gitutil` and `engine._git_files`. Filters can't be disabled
  generically. Document "scan untrusted code without its `.git` folder, or
  with `--no-git`" if a git-free walk mode is added.

### AGENT-INSTALL.md: four inaccuracies, fixed

- It said every part "fails open" when aihealth isn't installed. True for
  the hooks, but a project's `.mcp.json` entry shows as a failed server in
  `/mcp`. Now stated.
- If `~/.local/bin` isn't on PATH, `$(command -v aihealth)` is empty, so
  `claude mcp add` would register a broken server and the hooks would
  silently do nothing. An `aihealth --version` check with the
  `uv tool update-shell` remedy now follows the install.
- The stop hook was described as reacting to "its changes" (the agent's).
  It checks *all* uncommitted changes. Corrected.
- "The agent runs only the steps on this page" promised something a
  prompt can't guarantee. It now says the prompts ask the agent to show
  each change first, so the user can check it against the page.

The commands themselves were checked: `claude mcp add|remove --scope user`
match the current CLI, and the hook command was tested with and without
aihealth on PATH (exit 0, report passed through when a file degrades).

---

## Re-assessment after M14–M20

**Date:** 2026-11-07 · **Commit:** `35169df` (main) · **Range since the re-audit:**
Rust, TypeScript, JavaScript, CMake, C/C++/Objective-C(++), shell scripts
and zsh adapters.

### State

- 12 languages through one adapter layer. `src/` has grown from 5.9k to
  8.3k lines. 484 tests (was 262), line coverage **96.2 %** (5,095 / 5,295;
  measured as before with `sys.monitoring`), ruff clean. The repo scores
  **9.94**: 61 of 62 files healthy, and `smells.py` (8.64) is still the only
  warning.
- Every milestone kept the snapshot discipline: earlier languages
  compared byte-identical before/after (latest: 6,536 shell and Makefile
  files for zsh, 56,388 files for shell scripts). Each language was
  calibrated on real repos and checked for crashes on thousands of files.
- No finding from the first audit was fixed beyond 1 and 11. Two of them
  (5, 6) matter more now, see below.

| # | Finding | Status |
|---|---|---|
| 2 | Dashboard CSRF / DNS rebinding | fixed |
| 3 | `serve --host 0.0.0.0` without warning | fixed |
| 4 | ECharts from a CDN without SRI | fixed |
| 5 | No file-size limit in delta / hooks / MCP | fixed |
| 6 | Open-ended dependency versions | fixed (ranges pinned), see below |
| 7 | No CI | fixed |
| 8 | MCP wiring untested | covered by `test_stdio_server_round_trip` (see below) |
| 9 | Only Python 3.12 tested | fixed: CI runs 3.11–3.13 |
| 10 | `smells.py` refactor; stale test count | `smells.py` open; test count updated |
| 12 | `scan` hangs on a named pipe outside git | **new**, Low, fixed |
| 13 | Quadratic source scanning (header routing, `NS_ENUM`, zsh `${`) | **new**, Low–Medium, fixed |
| 14 | zsh parse guard has no wall-clock bound | **new**, Low, fixed |

**Finding 6, update.** The declared floors can't run the current code:
`tree-sitter-language-pack>=0.7` predates the `zsh`, `objc` and `cmake`
grammars the adapters load at start-up, and the zsh work depends on
tree-sitter 0.26 behaviour (no `timeout_micros`; `progress_callback`
segfaults with these grammars). The zsh grammar's endless loops are a
property of one grammar version: a newer one may fix them or add others.
Pinning tested ranges (`tree-sitter>=0.26,<0.27`,
`tree-sitter-language-pack>=1.25,<1.26`) or committing `uv.lock` is now the
most valuable cheap fix.

### 12. `scan` hangs on a named pipe (new, Low)

Since M19, `engine.discover` reads the first bytes of every extensionless
file to look for a shebang (`file_head`), **before** its `is_file()`
check. Opening a FIFO blocks until a writer appears. Reproduced: a
directory outside git with `bin/pipe` made by `mkfifo` makes
`aihealth scan DIR` hang forever (killed by `timeout 10`, exit 124).
Inside a git work tree it is fine: `git ls-files` doesn't list FIFOs.
`is_supported_file` (hooks, CLI) has the same order. **Fix:** check
`stat.S_ISREG` (or `is_file()`) before `file_head`, or open with
`O_NONBLOCK`.

### 13. Quadratic source scanning (new, Low–Medium)

Measured on synthetic input:

| Code | Input | Time |
|---|---|---:|
| `cfamily.header_grammar` (`^\s*` under MULTILINE in `_OBJC_MARKERS` / `_CPP_MARKERS`) | `.h` file of 80,000 blank lines (80 KB) | 78.6 s |
| `cfamily._APPLE_ENUM` (`\(\s*[\w ]+,`) | `NS_ENUM(` + 80,000 spaces | 9.0 s |
| `shell_zsh.prepare_zsh` (scans to the end of the line for every `${`) | one 40 KB line of `${` | 73.2 s |

Time grows with the square of the input, so a 1 MB file (the scan limit)
takes hours. Real code doesn't look like this, but a hostile repo, or an
unlucky generated file, stalls `scan`, `delta` and the post-edit hook,
which also have no size limit (finding 5). The other preparation regexes
(`_GLOB_QUOTE`, `_CONDITIONALS`, `_BARE_MACRO`, `_SCOPE`) stayed linear.
**Fix:** `^[ \t]*` instead of `^\s*`, `\([ \t]*\w[\w ]*,` for the enum
macro, and match `${`/`}` pairs in one pass per line in `prepare_zsh`.
Add timing tests with large inputs.

### 14. zsh parse guard without a wall-clock bound (new, Low)

`shell_zsh._finishes` forks a child with `RLIMIT_CPU` = 1 s and waits for
it with a blocking `waitpid`. A CPU limit doesn't end a child that blocks
without using CPU. The child only parses, so that would take a lock held
by another thread at fork time. The MCP server runs tools in an anyio
worker thread and the dashboard scans in uvicorn's thread pool, so the
fork does happen in multi-threaded processes. macOS and glibc protect
`malloc` across fork, and no such hang was seen. A hanging child also
grows to about 1 GB before the limit ends it. **Fix:** also call
`signal.alarm(2)` in the child (the default action kills it while
sleeping), and optionally cap memory with `RLIMIT_AS` on Linux.

### Verified as sound in the new code

- Source preparation keeps byte offsets (tested per adapter), so findings
  point at the right lines. Names are read from the original source.
- New adapters' walks are iterative, and parse errors are handled or
  reported per language as documented in `cards/parser-choice.md`.
- The zsh guard: the child exits with `os._exit` (no Python cleanup, no
  duplicated output), is always reaped, and without `fork` (Windows) the
  bash tree is used. Tested with a real hanging input.
- Shebang routing reads at most 128 bytes, and only for extensionless
  files.
- No new subprocess, SQL or template code. All git calls still go through
  `gitutil.GIT` with `core.fsmonitor=false`.

### Recommended order

1. Fix 12, 13 and 14: small, local changes with tests.
2. Pin dependency ranges or add a lockfile (6), and add CI on Python 3.11
   and 3.12 with the MCP wiring test (7, 9, 8).
3. Apply `max_file_kb` in delta, hooks and MCP (5).
4. Dashboard hardening (2–4).
5. Refactor `smells.py` (10), and update the test count in the plan.

### Fixes for 12–14

- **12:** `file_head` reads only regular files (`stat.S_ISREG` first), so
  `discover` and `is_supported_file` never open a FIFO.
  `test_scan_does_not_open_a_named_pipe` runs the scan in a thread with a
  10 s limit, so a regression fails instead of hanging the suite.
- **13:** `^[^\S\n]*` replaces `^\s*` in the header markers (same matches,
  can't cross lines), and `\(\s*\w[\w ]*,` the enum macro's
  `\(\s*[\w ]+,`. `prepare_zsh` matches `${`/`}` pairs in one regex pass,
  bounds the flag and subscript patterns, merges nested parts, and allows
  at most 8 extra `for` variables (that regex was quadratic too, found
  while fixing). Every input from the table now takes milliseconds at 80k
  units, and timing tests cover them. Old and new code give identical
  output on all 319,729 C-family files and all real zsh files on this
  machine. The only change: two bash files in acme.sh, where the word
  "for" in prose was followed by more than 8 words before "in". The zsh
  rewrites don't apply to bash files anyway.
- **14:** the child also sets `SIGALRM` to its default action and calls
  `alarm(3)`. A test with a parser that sleeps (no CPU) returns within the
  limit.
- The three new tests fail on the old code. 492 tests pass, ruff is clean,
  and `aihealth delta` reports no changes in health.

### Fixes for 6–9

- **6:** `pyproject.toml` pins tested ranges: `tree-sitter>=0.26,<0.27`
  and `tree-sitter-language-pack>=1.25,<1.26` (the tested minor; widening
  needs the spike tests, a snapshot comparison and calibration),
  `mcp>=2.3,<3`, and `fastapi`, `uvicorn`, `jinja2` below their next
  major. Dev tools: `ruff>=0.17,<0.18` so new rules or formatting don't
  fail CI, `pytest<10`, `httpx<1`. The ranges resolve to exactly the
  versions tested locally. No lockfile: users install with pip, so the
  ranges are what they get. Not done: recording the grammar version in
  saved scans.
- **7, 9:** `.github/workflows/ci.yml` installs `.[dev]`, then runs ruff
  (check and format), pytest and `aihealth scan . --fail-under 9` on
  Python 3.11, 3.12 and 3.13 (ubuntu) and 3.12 (macOS, where the zsh
  guard's fork behaves differently). It runs on push to main, on pull
  requests, weekly (patch releases inside the ranges) and by hand.
  Python 3.11 compatibility was checked statically (ruff's `py311`
  target, no 3.12-only standard-library calls); CI is the first real
  3.11 run.
- **8:** already covered: `test_stdio_server_round_trip` starts
  `aihealth mcp` over stdio, initialises a client session, lists the tools,
  calls one and checks that a bad input comes back as an error with its
  message. That runs `build_server` and `_as_tool_errors` against the
  installed SDK. The coverage figure missed it because the server runs in
  a subprocess.

### Fix for 5

`engine.too_large(size, config)` applies `[scan] max_file_kb` (default
1 MB) everywhere a file is parsed automatically. `discover` (scan) uses
it. Delta skips a changed file whose new *or* previous version is over
the limit, with the reason in `skipped` (scoring the old version is just
as slow), so the post-edit and stop hooks and MCP `review_changes` skip it
too. MCP `score_file` and `preview_change` raise a tool error naming the
limit. Text and markdown delta reports now list skipped files ("Not
scored: `a.py` (over scan.max_file_kb: 2 KB > 1 KB)", also for generated
and excluded files); before, only JSON showed them, so a skipped file
looked like a clean one. `aihealth scan FILE` stays unlimited: a person
asked for that file. Three tests, one per entry point, fail on the old
code. 495 tests pass.

### Fixes for 2–4

- **2:** `TrustedHostMiddleware` accepts only `Host` values naming
  loopback (`127.0.0.1`, `localhost`, `::1`) or the `--host` given, so a
  rebinding site gets 400. `POST /scan` returns 403 when `Origin` isn't
  the dashboard's own origin, or, without `Origin`, when `Sec-Fetch-Site`
  says cross-site; clients that send neither (curl) still work. Every
  response carries `Content-Security-Policy: frame-ancestors 'none'`,
  `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff` and
  `Referrer-Policy: same-origin`, so "Scan now" can't be clickjacked. A
  full script CSP would need the inline chart scripts moved to files: not
  done.
- **3:** `serve` warns on stderr for any host other than loopback. Bound
  to all interfaces (`0.0.0.0`), any `Host` is accepted, since there is no
  name to check against; the warning covers that.
- **4:** the ECharts `<script>` has
  `integrity="sha384-Mx5lkUEQ…YPzR"` and `crossorigin="anonymous"`, the
  hash of `echarts@5.5.1/dist/echarts.min.js` (identical from jsDelivr
  and unpkg).
- Tests for each check (host refused, cross-site posts refused, headers
  and SRI present, warning or none per host) fail on the old code. A
  real `aihealth serve` checked with curl: 200 for its own host, 400 for
  `Host: evil.example`, 403 for a cross-site post, 303 for a same-origin
  one. 501 tests pass.
