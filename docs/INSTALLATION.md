# Install and diagnose a local TÜİK MCP

Requires Python 3.12+. The server runs locally over stdio; no TÜİK API key or
OpenSearch service is required for core discovery and public-file reading.

## Install this repository's artifact

Use a wheel built from a reviewed commit of
`https://github.com/ByRock010/TUIK-Data-MCP-Server`. The existing distribution name
is `tuik-mcp`. The PyPI JSON endpoint for that name returned 404 on 18 November
2026; this is a dated availability observation, **not proof of reservation,
ownership, or permission to publish**. No package was published by this change.
Do not use a bare `uvx tuik-mcp` or `pip install tuik-mcp` until a separate release
verifies index ownership and links the correct project. An installable source
archive in a GitHub release must actually be built from that release; a repository
source ZIP is a different artifact.

Build locally (maintainers):

```sh
python -m pip install build
python -m build
```

This produces a wheel and sdist in `dist/`. Install a chosen wheel outside the
checkout; replace the example filename with the file you actually received.
The examples use the current package version and do not imply a new release.

macOS/Linux:

```sh
python3 -m venv ~/.venvs/tuik
~/.venvs/tuik/bin/python -m pip install '/absolute/path/tuik_mcp-0.3.0-py3-none-any.whl'
~/.venvs/tuik/bin/tuik-mcp-doctor
```

Windows PowerShell:

```powershell
py -3.12 -m venv "$env:LOCALAPPDATA\tuik-mcp\venv"
& "$env:LOCALAPPDATA\tuik-mcp\venv\Scripts\python.exe" -m pip install 'C:\Downloads\tuik_mcp-0.3.0-py3-none-any.whl'
& "$env:LOCALAPPDATA\tuik-mcp\venv\Scripts\tuik-mcp-doctor.exe"
```

With uv already installed, the same wheel can be isolated in a tool environment:

```sh
uv tool install '/absolute/path/tuik_mcp-0.3.0-py3-none-any.whl'
tuik-mcp-doctor
```

## Optional capabilities

Append `[viz]`, `[search]` or `[semantic]` to the **wheel path** during install:

```sh
python -m pip install '/absolute/path/tuik_mcp-0.3.0-py3-none-any.whl[viz]'
```

- Core: search/resolve/inspect/query CSV and Excel, PDF reading and classifications.
- `viz`: matplotlib PNG charts; without it, valid chart requests report
  `visualization_unavailable`, while other tools remain available.
- `search`: optional OpenSearch discovery. Without a configured index, literal
  dataset search and live portal search remain available with their documented
  limitations. Configuring a URL does not make an index populated or current.
- `semantic`: optional hybrid metadata ranking. Model provisioning is an explicit
  `tuik-mcp-index --embeddings` operation. Ordinary searches use local-only model
  loading and fall back to lexical ranking if the runtime/model is unavailable.

PPTX/XLSX/PDF **authoring**, persistent artifact retrieval and a hosted endpoint
are planned in #84/#88; no `report` installation extra exists yet. The current
chart is returned as MCP image content and a structured summary; public source
downloads use the official `download_links`. A server file path is not a download.

## Doctor: no automatic repairs

```sh
tuik-mcp-doctor
tuik-mcp-doctor --json
tuik-mcp-doctor --storage-dir /existing/report-parent
tuik-mcp-doctor --online
```

Default diagnostics inspect Python/package versions, known settings, optional
dependency metadata, prospective storage permissions and model-cache presence.
They never install a dependency, load an embedding model, create a directory,
write a test artifact or edit credentials/configuration. Python itself may cache
imported bytecode. No endpoint is contacted unless `--online` is supplied.

The storage check defaults to the current directory and examines the nearest
existing parent of a supplied path. It does **not** prove an actual write will
succeed or implement the future artifact service. Cache files do not prove that
the embedding model can load. Installed dependencies do not prove live data.

`--online` issues at most three HEAD requests with a three-second timeout each:
the official portal, classification homepage and a configured index URL. No data
files, queries, credentials, retries or redirects are involved. Credential/query
bearing configured URLs are skipped. Only labels/status codes are reported;
URLs, response text, headers, secret values and exception strings are not echoed.
HEAD restrictions or an HTTP 401/405 are diagnosis hints, not proof of an outage.

Exit 0 means no core installation/configuration errors; warnings/skipped optional
features are allowed. Exit 1 means a required runtime, settings or storage error.
This is distinct from running `tuik-mcp`: waiting silently there is normal because
it expects an MCP client. Server logging stays on stderr.

## Connect a client without replacing existing configuration

The same absolute executable works in both Claude Code and Codex; use
`Scripts\tuik-mcp.exe` on Windows. Escape backslashes in JSON/TOML or use `/`.
Merge only a new `tuik` entry into existing configuration and restart the client.

Claude Code project `.mcp.json` (a new server may require client approval):

```json
{"mcpServers":{"tuik":{"command":"/absolute/venv/bin/tuik-mcp","args":[]}}}
```

Codex `~/.codex/config.toml` (Windows: `%USERPROFILE%\.codex\config.toml`):

```toml
[mcp_servers.tuik]
command = "/absolute/venv/bin/tuik-mcp"
args = []
```

Use the client's MCP server list to confirm discovery. There should be nine tools,
including `inspect_tuik_data` and `chart_tuik_data`. Start with:
“TÜİK'te işsizlik veri kaynaklarını bul; seçtiğin kaynağın bağlantısını göster.”
Then inspect the actual columns before requesting a filter. This task needs
network access to TÜİK; successful tool discovery alone does not validate it.

## Maintainer clean-install checks

`Package installation` CI builds/checks wheel and sdist, then installs the wheel
on Linux, macOS and Windows in core-only and `viz` profiles. It also installs the
sdist in an independent Linux job. The smoke runs outside the checkout, checks
packaged search resources, verifies all nine tools over actual stdio, exercises
an invalid-input response without network, and tests the chart/optional-extra path.
No embedding model is downloaded and no live TÜİK/index is needed.

Local equivalent after installing a wheel into a clean environment:

```sh
/clean/venv/bin/python /checkout/scripts/check_installed_package.py --profile core
```

See `docs/verification/issue-98-installation.md` for dated observations and their
limits. Distribution publication, changes to existing client settings and
production hosting are separate actions.
