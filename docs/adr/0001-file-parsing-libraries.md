# ADR 0001: Shared file parsing and libraries

Status: accepted. Date: 2026-10-18. Issue: #3.

## Context

CSV parsing currently lives inside the portal adapter. Spreadsheet, document and
future index ingestion need a shared representation without coupling parsers to
HTTP requests or MCP tools. Download bounds, verification and provenance remain
the responsibility of the existing HTTP/adapter/service layers.

## Decision

Use `ParsedTable` for string-valued rows, bilingual columns/titles, units, sheet,
notes, source note, extraction method and warnings. `total_rows` counts all rows
after exact filtering, even when output is limited. `DocumentPage` and
`DocumentChunk` retain one-based page numbers, optional headings and text.
Registries dispatch only formats with implemented parsers; unsupported formats
fail explicitly. Parsing has no network access and source text stays data.

Move the existing UTF-8/BOM CSV implementation to `parsers/csv.py`, preserving
delimiter detection, validation, exact string filters, errors and row limits.
Keep `TuikPortal.csv_rows` as a compatibility/error translation wrapper. The
asynchronous service invokes it through `asyncio.to_thread` so parsing does not
block the MCP event loop. New async callers must also offload registry calls.
Parser failures derive from `Exception`, not `ValueError`, so malformed upstream
files and unsupported formats cannot be mistaken for invalid MCP caller input.
Service/adapter boundaries must translate them to `SourceError`; the existing
CSV wrapper preserves its `malformed_response` and `invalid_filters` codes.

For later ingestion features choose:

| Format | Library | License | Reason |
| --- | --- | --- | --- |
| CSV | Python `csv` | PSF | Preserves the current behavior without a dependency |
| XLS | `xlrd` 2.x | BSD | Reads OLE XLS; `formatting_info=True` exposes number formats |
| XLSX | `openpyxl` | MIT | Read-only, data-only workbook access |
| PDF | `pdfplumber` | MIT | Page-aware text, glyph deduplication and simple tables |

Add these optional parsing dependencies with the features that use them, not
as unused imports here. XLSX exports have now been observed despite XLS labels
(PR #30); dispatch must use the verified file format, not the advertised suffix.

Rejected: PyMuPDF (AGPL distribution obligations), Docling (large PyTorch/model
runtime for regular files) and MarkItDown (Markdown loses structured rows, units
and notes). CSV uses only the standard library.

## Consequences

Existing MCP CSV responses stay unchanged. Thread offloading makes the event
loop responsive but is not a hard CPU or memory sandbox; parser cost limits,
parsed-result caching and cancellation policy remain the separate #9 scope.
Spreadsheet heuristics and PDF extraction remain #5 and #7, with real fixtures
from #4; this foundation does not claim those formats are implemented.

## Update — parser error boundary (#39)

`services/parsing.py` is now the only place where the service calls parsers. It
runs them in a worker thread and translates parser errors into `SourceError`s that
cite the file's URL: `InvalidFiltersError` → `invalid_filters`,
`UnsupportedFormatError` → `unsupported_download`, other `ParseError`s →
`parse_failed` (`malformed_response` for CSV, the code CSV responses always used).
`TuikPortal.csv_rows` was removed. As a safety net, `respond()` reports an
untranslated `ParseError` as `parse_failed`. Caller input errors stay
`ValueError` → `invalid_input`.

