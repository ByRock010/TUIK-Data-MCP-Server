# Issue #98: installation and read-only diagnosis

Date: 18 November 2026. Branch starts at `origin/develop` commit
`ea26bb4e8e1fd05eed815e4def469b527da9191a`; the report-archive PR #110 is not
part of this change. No package publication, hosted service, user credential
change or automatic client-configuration migration was performed.

## Local evidence

- macOS arm64, isolated Python 3.12.11 environments; no source-checkout dependency
  in the installed smoke. Development dependencies include MCP Python SDK 1.30.0.
- Wheel and sdist built successfully with `python -m build --no-isolation`
  (build 1.6.1, hatchling 1.33.0). Each archive was inspected: required search JSON
  and synonyms are packaged; local reports, temporary files and `.env` are absent.
- Three independent environments install wheel/core, wheel/viz and sdist/core.
  `scripts/check_installed_package.py` changes to a fresh temporary directory,
  asserts imports are under the environment prefix, checks package resources and
  entry points, and discovers all nine tools through actual MCP stdio. An invalid
  resource call returns `invalid_input` without contacting TÜİK. A recorded-shape
  synthetic catalogue checks literal search without an index. Chart rendering
  produces a PNG with viz and `visualization_unavailable` without it.
- `tuik-mcp-doctor --online --json` explicitly invoked: portal and classification
  homepage HEAD probes each returned HTTP 200. This verifies reachability only,
  not dataset correctness or the source contracts.
- The PyPI JSON endpoint `https://pypi.org/pypi/tuik-mcp/json` returned HTTP 404.
  This dated check establishes neither ownership nor a reservation. Installation
  examples use a supplied/built wheel, never an unverified package-index name.
- Unit/offline suite: **791 passed, 22 skipped, 6 deselected** in 42.89 s.
  The skips retain the optional real-index/live contracts; no new full live-data
  test is claimed. Ruff checks/format pass. Node workflow tests: 28 passed.

## Versioned client checks

- **Claude Code 2.1.158:** an isolated configuration directory under the temporary
  directory registers the installed core wheel's stdio executable. `claude mcp
  get tuik_issue98` reports **Connected**. The user's existing configuration is
  not edited; no model call is required for this connection check.
- **Official MCP Python SDK client 1.30.0:** initialize, list all nine tools and
  one input-error tool call pass for every isolated installation profile.
- **Codex CLI 0.169.0-alpha.11.1:** command-line configuration overrides are
  recognized by `codex mcp get tuik_issue98 --json`. This is configuration parsing
  evidence only, not a recorded interactive/model-driven Codex workflow.

Client version observations above do not establish compatibility with every
client or a successful statistical answer. Documentation gives separate client
configuration snippets and calls out client-specific approval for new servers.

## Short guided fresh-install walkthrough

An author-operated setup trial followed the new instructions in a fresh
Python environment: install a specific wheel outside the checkout → run doctor
with no index or extras → register its absolute executable in the isolated
Claude configuration → confirm Connected and nine tools through the SDK smoke.
The core-only install exits doctor successfully and explicitly skips optional
extras. An intentionally invalid setting returns exit 1 without echoing its
value; missing visualization yields the named optional-feature error.

This is a reproducible onboarding scenario, **not independent novice-user
research**. Independent sector-user evaluation belongs to #85/#100. Windows and
Linux results come from the separate `Package installation` CI matrix, rather
than simulated platform checks. Add its completed run link below after the first
PR run; do not claim those jobs passed before observing their results.

## Read-only and diagnostic limits

Doctor never provisions an index/model or creates storage/configuration files.
Permission inspection does not prove actual writes/quota, installed extras do
not prove they load, and nonempty cache directories do not prove usable models.
Python may generate bytecode caches when importing modules. Missing optional
features are skipped/warnings; required runtime/settings/storage failures exit 1.
Remote bodies, URLs, headers, exception text and configuration values are not
printed. Credential/query-bearing probe URLs are skipped; redirects are not
followed. No network request happens without `--online`.

Persistent report artifacts are not implemented in #98. Installation docs state
current MCP image/source-download behavior and link the future #88 scope rather
than offering a nonexistent reports extra.
