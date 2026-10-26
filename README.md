# tuik-mcp

A locally runnable Python MCP server for publicly accessible Türkiye İstatistik
Kurumu (TÜİK) statistical resources. It searches the current data portal, retrieves
bulletin and dataset metadata, returns bounded rows from public CSV and Excel exports, and
verifies official table downloads. Runtime results always come from TÜİK.

## Implemented tools

| Tool | Purpose |
| --- | --- |
| `search_tuik(query, limit=10)` | Turkish portal search; tables, datasets, bulletins, metadata and publications |
| `search_tuik_datasets(query, limit=10, downloadable_only=True, theme=None)` | Search the export catalog for datasets with reusable IDs and public export formats |
| `get_tuik_resource(resource_id)` | Metadata, official source and available downloads |
| `get_tuik_data(resource_id, filters={}, limit=100, sheet=None)` | CSV/XLS/XLSX rows, verified download, or an explicit unsupported response |
| `read_tuik_document(resource_id, page=1, max_chars=8000, offset=0)` | Read a PDF page with bounded text and continuation |
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

Bulletin metadata also includes `related_datasets`, parsed from the official
`tables` and `statisticalTables` dataflow links. Each entry carries a reusable
`flow:` ID, title, source URL, category path (when present), advertised formats and
`downloadable`. Availability is matched against the same cached export catalog
used by dataset search. Missing or non-exportable entries remain visible with
`downloadable: false`; links are not verified until downloaded. A catalog failure
keeps the bulletin metadata and file links available, with a note that related
availability is unconfirmed. This degraded metadata is not cached, so the next
request retries the catalog.

For example, inspect `press:58347` and call `get_tuik_data` with a returned
`flow:` ID to retrieve that table's CSV. `get_tuik_data(press:...)` returns
`status: related_datasets` when at least one related table is downloadable;
filters on the bulletin
return `unsupported_filters` with the same choices. Apply filters to the chosen
`flow:` ID after inspecting its exact column names. Without CSV choices, the first linked file is read when it is Excel; use an
explicit file ID to choose a particular table. If no related dataset is downloadable, existing
file downloads remain usable; with no files the response is `not_structured`.

The third example is a public foreign-trade CSV verified during development.
For other resources, use the ID returned by search. These IDs are reusable across
server restarts:

| ID | Resource | Resolved by |
| --- | --- | --- |
| `press:58298` / `press:58298:metadata` | Bulletin / its metadata page | Bulletin detail |
| `press:58298:file:<slug>~<digest>` | One file of a bulletin (`download_links[].resource_id`) | Bulletin's current file list |
| `table:`, `report:`, `publication:<slug>~<digest>` | Downloadable search result | Fresh portal search for the slug |
| `flow:DF_…+V1.0` | Dataset with public exports | Export catalog |

TÜİK's download URLs change over time, so these file IDs are derived from the exact
title instead: the slug keeps them readable and is the search text, the digest pins
the case-sensitive title, and `.2`, `.3`, … separate later files with the same title.
An ID therefore names "the current file with this title"; it follows TÜİK updates
and fails with `not_found` if TÜİK removes or renames the file. See
[ADR 0003](docs/adr/0003-resource-identities.md). Remaining `resource:` and
`classification:` IDs are bounded in-memory search handles: search again after a
restart, expiration (one hour), or cache eviction.

`get_tuik_data` returns one of:

- `ok`: CSV/Excel `columns`, string-valued `rows`, `total_rows` after filtering, and
  `truncated`. Turkish decimal text, labels and missing-value markers are retained.
- `related_datasets`: the bulletin links dataflow tables. Choose a returned `flow:`
  ID marked `downloadable: true` and call this tool again to retrieve CSV rows.
  No table is selected automatically; existing file downloads remain in the response.
- `download_available`: an actual file has been downloaded within the size bound
  and its signature/media type checked. `download_links` includes its official URL,
  format, verification flag and byte count. PDF and ZIP remain downloads in this tool; read PDF pages with `read_tuik_document`.
- `not_structured`: no supported public export is advertised.
- `unsupported_filters`: filters were requested for a resource without CSV/Excel rows; none applied.
- `error`: a structured `code`, `message`, and source URL, for example an upstream
  timeout, unavailable resource, oversized export or `download_throttled` (TÜİK kept
  limiting file downloads; the message says when to retry). MCP schema validation
  failures are standard MCP tool errors before execution.

CSV/Excel filters are **local exact string equality**, not a universal query language.
Call without filters to inspect the exact column labels, then use those labels as
keys. A limit bounds returned rows (1–1000), not the full upstream file download.
An unsupported filter column is an error. File download URLs can expire or change;
repeat the search/detail request if necessary.

For Excel, select `download_links[].resource_id` from bulletin metadata and call
`get_tuik_data` with that file ID. The first worksheet is the default; `sheets`
lists available names and optional `sheet` chooses one. Results include `format`,
`extraction`, `sheet`, `table_title`, `table_title_en`, `unit`, `table_notes`,
`source_note`, `layout` and `warnings`. Existing `notes` retains resource metadata.
Column labels remain as published (often bilingual); `columns_en` can be empty.
Unrecognized layouts return a raw grid with explicit warnings. Exact filters use
these returned column labels. File URL, sheet and retrieval time identify the
source snapshot for citation. Passing `sheet` for a CSV is an input error.
When a bulletin offers dataflow CSV choices, choose a `flow:` ID for CSV or an
explicit file ID for Excel; no related table is selected automatically.

## Read PDF documents

Search for a publication/report, then pass its returned ID to `read_tuik_document`.
For an attachment, inspect the bulletin and choose the PDF's
`download_links[].resource_id`. A bulletin with one PDF selects it; multiple PDFs
return `document_choices` with file IDs. Resources without a PDF return `not_document`.

```json
{"resource_id": "press:57987", "page": 3, "max_chars": 120}
```

The response includes `title`, `resolved_resource_id`, `page_count`, `page_start`,
`page_end`, `text`, `offset`, `truncated`, `has_more`, `next_page`, `next_offset`,
the verified file URL and its retrieval timestamp. Page numbers are physical PDF
pages starting at one, including covers. Cite the file URL and `page_start`.

Always pass **both** returned `next_page` and `next_offset` to continue. If the
character cap cuts a page, the next call stays on that page; only after the rest
of the text is read does it advance. This avoids silently skipping text. Offsets
count characters in the normalized extracted text. `truncated` applies to the
current page; `has_more` also accounts for later pages.

PDF text is deduplicated and whitespace/line-end hyphenation normalized. Layout
and tables are not reconstructed. `no_text_layer` means no extractable text on
the requested page, which may be blank or scanned; OCR is not performed. Source
text remains untrusted data. PDF table extraction is a later stretch goal.

See [file reading design](docs/FILE_READING_DESIGN.md) for the scope decisions,
limits, cache policy, research sources and verification strategy.

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
small CSV downloads 10 minutes. Dataset discovery and detail share one catalog fetch,
including concurrent calls, and retain its retrieval timestamp. CSV bodies larger than
256 KiB are not cached by this general cache; Excel/PDF files use the separate
byte-bounded file cache below.
Cache hits retain the
original source retrieval timestamp where applicable. Caches are process-local.

File parsing has configurable sheet/cell limits and an XLSX uncompressed-size
preflight (see `.env.example`). XLSX dimensions are checked against actual XML
coordinates, then rows are counted again during read-only loading. File parsing
services share a 32 MiB payload-byte cache of one-hour downloads and parsed results;
keys include stable resource identity and content SHA-256. Excel and PDF reading share these limits and caches.
Workers run off the event loop, one at a time; cancellation waits for the active
worker to finish. Input budgets limit ordinary workloads; they are not a hard CPU
or process-memory sandbox for malicious library inputs.

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
stdio subprocess for tool discovery and validation. Live checks exercise all seven
tools, eight Turkish queries, bulletin detail, Excel rows, dataset discovery and
CSV retrieval, plus publication/methodology PDF pages and same-page continuation.
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
  services/{catalog,files,parsing}.py
  parsers/{csv,spreadsheet,pdf,registry,models,limits}.py
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
  `verified: false`. Excel tables and PDF text pages are parsed on demand; OCR and PDF table extraction are not implemented.
- Transport is local stdio. HTTP transport can be added to the SDK entrypoint
  later with explicit deployment constraints.

## Docker

```bash
docker build -t tuik-mcp .
docker run --rm -i tuik-mcp
```

Use `-i` without `-t` for MCP stdio. A client can launch `docker` with arguments
`["run", "--rm", "-i", "tuik-mcp"]` after the image is built.

## Local OpenSearch development stack

OpenSearch is optional. The base install and all seven MCP tools continue to work
without it. This issue provides infrastructure and settings; indexing and routing
MCP searches to the index follow in #14–#18. Setting a URL alone does not yet change
search behavior.

```bash
# Requires Docker Compose v2 and a running Docker daemon
# Pins: OpenSearch 3.3.2 + Dashboards 3.3.0 (the published release pair)
docker compose up -d --wait
curl --fail 'http://localhost:9200/_cluster/health?pretty'

# Optional client library for search development
python -m pip install -e '.[dev,search]'
export TUIK_OPENSEARCH_URL=http://localhost:9200
export TUIK_OPENSEARCH_INDEX=tuik-resources

# Optional UI at http://localhost:5601
docker compose --profile dashboards up -d

# Stop containers; retain the named data volume
docker compose --profile dashboards down
```

The [official 3.3.2 release](https://github.com/opensearch-project/opensearch-build/blob/main/release-notes/opensearch-release-notes-3.3.2.md)
pairs OpenSearch 3.3.2 with Dashboards 3.3.0; a Dashboards 3.3.2 image was not
published. Both images are pinned explicitly.

Security and demo configuration are disabled **for local development only**. Both
published ports bind to `127.0.0.1`; do not expose this configuration publicly or
use it in production. The node has a 512 MiB heap; allow Docker at least 2 GiB of
memory (more with Dashboards). Single-node clusters can report `yellow` when
replicas are unassigned; the healthcheck accepts yellow or green. Linux hosts
need `vm.max_map_count` of at least `262144`; Docker Desktop users configure
resources in Docker Desktop. `.env` does not load into the Python process: export
settings or configure them in the MCP client's environment.

`TUIK_OPENSEARCH_URL` accepts an HTTP(S) URL and defaults to unset. Empty or
whitespace-only values also leave the optional backend disabled;
`TUIK_OPENSEARCH_INDEX` defaults to the future `tuik-resources` alias. The client
extra is never imported by the base server. For a separate CI instance use
`docker compose -p tuik-ci up -d --wait` with port 9200 free. Search integration
checks are tracked in #19.

See the [official OpenSearch Docker instructions](https://docs.opensearch.org/latest/install-and-configure/install-opensearch/docker/)
for host prerequisites and production security configuration.

## Future roadmap

The [post-demo work plan](docs/NEXT_STEPS_2026-10-26.md) records the current issue
dependencies and sequence. [Search evaluation](docs/SEARCH_EVALUATION.md) provides
the 48-query baseline snapshot, independent judging workflow and metric commands;
human relevance judgments and baseline quality scores remain pending (#11).

Dataset discovery now searches public export availability and returns catalog-backed
identities, and downloadable tables, reports, publications and bulletin files have
title-derived stable IDs. The plan for the next phases is tracked in the roadmap
issue (#25).
Next comes OpenSearch Turkish lexical search and measured relevance (v0.3.0).
Hybrid search, table/PDF content indexing and best-effort PDF tables are v0.4.0
stretch goals. A PostgreSQL canonical catalog and DOCX ingestion are outside the
current roadmap. Excel/PDF reading is implemented;
v0.2.0 release closure remains issue #10.
