# File reading plan and design — 2026-10-22

## Priority for the advisor meeting

The best immediate increment is an end-to-end discover → inspect → read workflow.
The Excel parser already exists, while most sampled bulletin tables are XLS.
Therefore #6 produces more visible value than starting the OpenSearch indexer now.
PDF publications and methodology pages (#7) complete the other advisor-requested
reading path. Input budgets and caching (#9) support both paths because TÜİK
throttles file downloads and compressed files can be much larger when parsed.

Implement and review in order:

1. #9 foundation: workbook/archive limits, bounded file snapshots, worker lifecycle.
2. #6: Excel rows through the existing MCP data tool, retaining CSV precedence.
3. #7: one PDF page through a dedicated document tool, plus lossless continuation.

#11 remains the next research priority: collect and judge the Turkish relevance
set before implementing search changes. A proper two-judge baseline cannot be
replaced by invented relevance labels. #10 still owns the actual v0.2.0 release;
these PRs do not publish/tag a release or complete OpenSearch work.

## TÜİK-specific decisions

- Bulletin HTML is not a PDF. PDF reading targets publications, reports and attached
  methodology files. Excel reading preserves existing TÜİK layout heuristics and
  verified format detection, including XLSX served with XLS MIME types.
- Bulletin CSV links remain choices. Do not automatically select one related table.
  File-level IDs choose a specific Excel/PDF attachment and resolve in fresh sessions.
- The first worksheet is the Excel default; expose all sheet names and allow explicit
  selection. Units, source number precision, sections, notes and raw-layout warnings
  remain visible. Source column labels are not translated or reinterpreted.
- A bulletin with one PDF can select it unambiguously. Multiple PDFs return choices.
- PDF page numbers are physical, one-based PDF pages, including covers. Remove duplicate
  glyphs before extracting text; normalize whitespace and line-end hyphenation.
  Preserve Turkish characters. No OCR or PDF table extraction is claimed.
- A character cap must not skip the rest of a page. `next_page` stays on the current
  page with `next_offset` until all normalized page text has been returned. The final
  segment advances to the next page at offset zero. Offsets are Unicode characters,
  not UTF-8 byte offsets. `truncated` concerns the current page; `has_more` also includes
  later pages. A blank/scanned requested page returns `no_text_layer` with a warning;
  this says nothing about other pages in that document.

## Limits, caching and concurrency

The existing 10 MiB decoded HTTP limit and separate six-second download pacing stay.
Workbook budgets default to 16 sheets and 200,000 rectangular grid cells across the
whole workbook. XLSX ZIP metadata is checked before loading: at most 4,096 entries,
50 MiB declared uncompressed payload, bounded sheet coordinates even when the file's
dimension field lies. Actual rows are counted again during read-only loading. XLS
sheets load on demand, retain source formatting and release library resources.
Formulas/macros are never executed; XLSX uses cached cell values.

PDF metadata enumeration stops at 1,000 pages. Only the selected page undergoes
layout/text extraction. Internal parser batches allow at most five pages and each
page at most 200,000 glyphs. The public tool reads one page and returns at most
20,000 characters (default 8,000). All configurable budgets live in `Settings` and
`.env.example`. Budget failures map to `parse_limit_exceeded`, unreadable files to
`parse_failed`, invalid caller pages/sheets to `invalid_input`.

A shared 32 MiB cache stores immutable body/parsed JSON payloads with one-hour TTL
and LRU eviction. Its byte budget is exact for stored payloads, not total process
memory. Download metadata is separately bounded to 256 records. Download snapshots
use stable IDs; parsed keys include ID + content SHA-256 + format/page. A new content
hash cannot reuse old parsed content. Eviction may cause another download/parse.
No disk catalog or background crawling is introduced. Cached responses retain the
file retrieval timestamp; snapshots can be up to one hour old.

Downloads and parses have separate locks; one parsing worker runs at a time for
these file-reading paths. The event loop handles cache mutations; expensive parsing,
JSON serialization and decoding run in a thread. Cancellation does not kill Python
threads, so even repeated cancellation retains the parsing slot until completion.
These input limits bound ordinary workloads; they are not a hard CPU/process-memory
sandbox for malicious parser-library inputs or compressed PDF streams.

## Research sources and evidence

- Existing real TÜİK corpus: `tests/fixtures/files/manifest.json`, golden observations
  and `docs/TUIK_SOURCES.md`.
- [openpyxl read-only mode and unreliable dimensions](https://openpyxl.readthedocs.io/en/stable/optimized.html).
- [xlrd formatting, on-demand loading and ragged rows](https://xlrd.readthedocs.io/en/latest/api.html).
- [pdfplumber text extraction, deduplication and page resources](https://github.com/jsvine/pdfplumber/blob/stable/README.md).
  The installed library's `PDF.pages` implementation was inspected: it enumerates all
  pages before returning a list, so the bounded reader enumerates PDF page metadata
  directly and instantiates layout objects only for the selected page(s).

Offline tests use both real PDFs, all existing spreadsheet goldens and generated
boundary cases. Live evidence includes seven MCP tools, eight original searches,
Excel rows, CSV retrieval, a publication PDF, a methodology PDF and same-page
continuation. See the dated verification files and report JSON.
