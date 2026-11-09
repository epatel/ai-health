# Security policy

## Supported versions

Fixes go into `main` and the next release. Only the latest release tag is supported;
pin that one (see the install lines in [README.md](README.md)).

## Reporting a vulnerability

Please report it privately, not in a public issue:
[open a private security advisory](https://github.com/epatel/ai-health/security/advisories/new)
(GitHub's private vulnerability reporting).

Include the version (`aihealth --version`), what you ran (CLI, MCP server or dashboard),
and the steps or input that trigger it. You should get an answer within a week. Once a fix
is released, the advisory is published with credit to you, unless you prefer otherwise.

## Scope

aihealth reads source code and git history and never runs the code it analyses. Things
worth reporting include:

- input files or repositories that make it read or write outside the analysed project,
  run commands, or hang (the zsh grammar runs under a time limit for that reason)
- the dashboard (`aihealth serve`, bound to 127.0.0.1 by default) exposing files or
  accepting requests it shouldn't
- the MCP server or Claude Code hooks doing more than they describe

Known vulnerabilities in dependencies are checked daily by the Audit workflow
(`.github/workflows/audit.yml`); reports about those are welcome too.
