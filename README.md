# tuik-mcp

A locally runnable Python MCP server for publicly accessible Türkiye İstatistik
Kurumu (TÜİK) statistical resources. It searches the current data portal, retrieves
bulletin and dataset metadata, returns bounded rows from public CSV exports, and
verifies official table downloads. Runtime results always come from TÜİK.

## Implemented tools

| Tool | Purpose |
| --- | --- |
| `search_tuik(query, limit=10)` | Turkish portal search; tables, datasets, bulletins, metadata and publications |
| `search_tuik_datasets(query, limit=10, downloadable_only=True, theme=None)` | Search the export catalog for datasets with reusable IDs and public export formats |
| `get_tuik_resource(resource_id)` | Metadata, official source and available downloads |
| `get_tuik_data(resource_id, filters={}, limit=100)` | CSV rows, verified download, or an explicit unsupported response |
| `list_tuik_themes()` | Current major statistical categories |
| `search_tuik_classifications(query, limit=10)` | Classification Server search, including NACE |

Every response includes `source_url`, `retrieved_at` (UTC) and `publisher`.
Collections also include provenance for individual records. Missing fields are
null or empty arrays. Source text is untrusted content, never executable code or
instructions.

## Installation

Requires Python 3.12 or newer. From the directory containing `pyproject.toml`:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Alternatively, with uv:

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e '.[dev]'
```

The implementation uses FastMCP from the **official MCP Python SDK 1.x**
(`mcp.server.fastmcp`), not the separate `fastmcp` distribution. The dependency
constraint deliberately excludes the incompatible SDK 2.x API.

## Run

```bash
.venv/bin/tuik-mcp
# Equivalent:
.venv/bin/python -m tuik_mcp.server
```

The server speaks MCP over stdio. Waiting silently for protocol input is normal.
Connect with an MCP client rather than typing conversational text into stdin.
Logging goes to stderr; stdout is reserved for MCP messages.

## Connect an MCP client

A Claude Desktop style configuration is:

```json
{
  "mcpServers": {
    "tuik": {
      "command": "/absolute/path/to/TUIK-Data-MCP-Server/.venv/bin/tuik-mcp",
      "args": []
    }
  }
}
```

Replace `command` with the absolute path to your environment's `tuik-mcp`
executable (`Scripts/tuik-mcp.exe` on Windows). Any stdio-compatible MCP
client can launch the same command; no API key or working-directory setting is
required. Restart the client after changing its configuration.

## Example questions and calls

```text
TÜİK'te enflasyonla ilgili veri kaynaklarını bul.
Türkiye nüfusu ile ilgili TÜİK tablolarını göster.
İşsizlik konusunda en güncel TÜİK kaynaklarını bul.
NACE sınıflamasını TÜİK'te ara.
```

```json
{"query": "işsizlik", "limit": 10}
{"resource_id": "press:57987"}
{"resource_id": "flow:DF_ALTINSIZ_DTH+V1.0", "filters": {}, "limit": 5}
```

To find datasets that can return rows, first call `search_tuik_datasets`:

```json
{"query": "işsizlik", "limit": 5}
{"query": "nüfus", "theme": "Nüfus ve Demografi", "limit": 5}
```

Then pass a returned `flow:` ID to `get_tuik_resource` or `get_tuik_data`. The dataset
search returns `available_formats`, advertised `download_links`, `updated_at` when
provided, the major theme, and source provenance. It searches a cached copy of the
official export catalog, using Turkish case-insensitive matching: every query word
must appear in the title, description or major theme. Exact titles rank first,
followed by title phrase/word matches; ties use title and ID. This is literal text
matching, without stemming or semantic search. Optional `theme` must match the full
major theme name, case-insensitively; use `list_tuik_themes` to discover names.

By default only datasets advertising public exports are included. Set
`downloadable_only: false` to also discover entries without an export.
`total` counts all matching catalog entries before the result limit; `truncated`
indicates omitted matches. Discovery does not download export files. Availability
is checked by `get_tuik_data`, so advertised links remain `verified: false` until
fetched. Catalog-backed IDs work across sessions while the upstream record exists.

The third example is a public foreign-trade CSV verified during development.
For other resources, use the ID returned by search. `press:` and `flow:` IDs are
reusable across server restarts. Other IDs are bounded in-memory search handles:
search again after a restart, expiration (one hour), or cache eviction.

`get_tuik_data` returns one of:

- `ok`: CSV `columns`, string-valued `rows`, `total_rows` after filtering, and
  `truncated`. Turkish decimal text, labels and missing-value markers are retained.
- `download_available`: an actual file has been downloaded within the size bound
  and its signature/media type checked. `download_links` includes its official URL,
  format, verification flag and byte count. XLS, XLSX, ZIP and PDF are not ingested.
- `not_structured`: no supported public export is advertised.
- `unsupported_filters`: filters were requested for a non-CSV resource; none applied.
- `error`: a structured `code`, `message`, and source URL, for example an upstream
  timeout, unavailable resource, oversized export or `download_throttled` (TÜİK kept
  limiting file downloads; the message says when to retry). MCP schema validation
  failures are standard MCP tool errors before execution.

CSV filters are **local exact string equality**, not a universal query language.
Call without filters to inspect the exact column labels, then use those labels as
keys. A limit bounds returned rows (1–1000), not the full upstream file download.
An unsupported filter column is an error. File download URLs can expire or change;
repeat the search/detail request if necessary.

## Configuration and reliability

Optional process environment variables are listed in `.env.example`. A `.env`
file is not loaded automatically. Defaults are a 20-second HTTP timeout, three
attempts, exponential backoff, and at least 0.5 seconds between request starts.
Only transport errors, HTTP 429 and HTTP 5xx are retried. Redirects are checked
against the same centralized host allowlist; POST redirects are rejected.
Responses are limited to 10 MiB of decoded content.

Table and document downloads (`/api/tr/data/downloads`) run one at a time, at least
6 seconds apart (`TUIK_DOWNLOAD_INTERVAL_SECONDS`). TÜİK answers back-to-back
downloads with an HTML "wait" page instead of the file; the server recognizes that
page, waits for its countdown and retries within the attempt budget. If the limit
persists, `get_tuik_data` returns `download_throttled`. Dataflow CSV exports are not
limited this way and keep the normal request pacing.

The User-Agent is `Mozilla/5.0 (compatible; tuik-mcp/0.1; public statistical research)`.
The public portal rejected curl's default User-Agent during investigation; this
compatible agent explicitly identifies the project. No login or credentials are
used. See [source research](docs/TUIK_SOURCES.md).

The replaceable `Cache` interface has a bounded in-memory TTL implementation:
search 10 minutes, metadata/catalog/search handles one hour, themes six hours,
small downloads 10 minutes. Dataset discovery and detail share one catalog fetch,
including concurrent calls, and retain its retrieval timestamp. Large files are not cached.
Cache hits retain the
original source retrieval timestamp where applicable. Caches are process-local.

## Tests and live verification

```bash
.venv/bin/pytest
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts

# Optional: makes real public requests, never runs by default
.venv/bin/pytest -m live

# Full MCP stdio workflow with a JSON report
.venv/bin/python scripts/live_smoke.py
```

Offline tests use reduced real response fixtures and mocked HTTP, plus an actual
stdio subprocess for tool discovery and validation. Live checks exercise all six
tools, eight Turkish queries, bulletin detail, an XLS download, and dataset
discovery followed by CSV retrieval using the returned ID.
See [the verification report](docs/VERIFICATION.md) for results and limitations of
the development run.

## Architecture

```text
MCP Client
    ↓ stdio
TÜİK MCP Server (server.py)
    ↓
CatalogService + Cache interface
    ↓
TuikPortal / ClassificationClient
    ↓
Bounded, rate-limited HTTP client
    ↓
Official TÜİK sources
```

Tool functions delegate to the service. Endpoint details and upstream schema
normalization are isolated in adapters. BeautifulSoup only strips HTML fragments
inside official JSON responses; search does not scrape rendered pages. No database,
search cluster, browser runtime, authentication, or background crawler is required.

```text
src/tuik_mcp/
  server.py, config.py, cache.py
  clients/{base,tuik_portal,classification}.py
  models/{common,search,dataset}.py
  services/catalog.py
  utils/normalization.py
tests/                 # unit, protocol and opt-in live tests; response fixtures
scripts/live_smoke.py   # real MCP client workflow
docs/TUIK_SOURCES.md
docs/VERIFICATION.md
pyproject.toml, README.md, .env.example, Dockerfile
```

## Current limitations

- The public portal APIs are frontend-internal and can change without notice.
  The official SDMX endpoint requires authentication and is intentionally excluded.
- Search uses TÜİK's relevance ordering and default non-archive setting, not a
  guaranteed newest-first order. Requests stop at five upstream pages/50 results.
  `total` is the upstream count; disallowed source hosts are omitted with a warning.
- Public CSV exports only exist for some dataflows. Large exports fail cleanly at
  the size bound. CSV values are strings; numeric/unit interpretation is deferred.
  Units, when present, remain in the original CSV columns; the aggregate `unit`
  field stays null. Dataset dimensions without a supported metadata source remain empty.
- Table-only search records may expose little metadata. Classification lookup
  returns classification editions, not code-level rows. Third-party resources and
  unverified TÜİK subdomains are excluded from the allowlist.
- Download links listed in metadata are advertised, not necessarily verified.
  `get_tuik_data` verifies one selected file (preferring CSV); other links retain
  `verified: false`. No spreadsheet or document ingestion is implemented.
- Transport is local stdio. HTTP transport can be added to the SDK entrypoint
  later with explicit deployment constraints.

## Docker

```bash
docker build -t tuik-mcp .
docker run --rm -i tuik-mcp
```

Use `-i` without `-t` for MCP stdio. A client can launch `docker` with arguments
`["run", "--rm", "-i", "tuik-mcp"]` after the image is built.

## Future roadmap

Dataset discovery now searches public export availability and returns catalog-backed
identities. The next step is automated adapter contract monitoring, followed by
stable identities for remaining table/publication search handles.
Later phases may add a PostgreSQL canonical catalog, OpenSearch Turkish full-text
search, PDF/DOCX/XLSX ingestion, automatic synchronization, revision/version
tracking, semantic/hybrid search, dataset/document linking, and richer citations
and provenance. None of those services is part of this MVP.
