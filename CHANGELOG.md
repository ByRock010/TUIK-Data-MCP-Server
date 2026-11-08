# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Optional hybrid lexical + semantic search (#21, ADR 0005).
  `tuik-mcp-index --embeddings` stores multilingual-e5-small vectors (`semantic`
  extra, ONNX). `search_tuik` fuses the lexical query with filtered k-NN by
  equal-weight RRF and reports `ranking`. On 1,689 reviewed pairs, nDCG@10 rose
  from 0.694 to 0.852 and MRR@10 from 0.880 to 0.990.
  - Abbreviation queries regress, and series-collapsed nDCG is unchanged.
  - Dataset-only, file-content and deep-page searches stay lexical.
  - The new grades come from a second, non-independent AI assessor.
- `collect-index --variants` collects the hybrid evaluation systems.
- `scripts/mcp_benchmark.py` runs the same prompts through Claude Code with the
  TÜİK MCP, with web tools only and with no tools, and summarizes tool calls, costs
  and which answer numbers occur in tool output. The
  [2026-11-08 run](docs/verification/mcp-benchmark-2026-11-08.md) scored 10 / 8 / 2
  of 10 on hard TÜİK questions (single runs, AI-graded).

### Changed

- Index mapping schema 3 adds the optional `embedding` vector field; rebuild older
  indices once with `tuik-mcp-index --full`.

## [0.3.0] - 2026-11-05

### Added

- Versioned release candidate, installation/upgrade notes and final acceptance
  evidence for the indexed-search milestone. Reviewed promotion, annotated tag
  and GitHub release publication remain explicit release steps (#29, #24).
- Full-metadata relevance comparison on 3,424 frozen documents and 1,390 pooled
  AI-reviewed pairs, with all five systems' overall/category/type metrics,
  retained source evidence and 55 documented revisions. The existing synonym
  default has the best matched-corpus nDCG@10/pooled recall; built-in Turkish has
  better MRR. A real full-index MCP smoke and live contracts passed. Independent
  human validation and v0.3.0 publication are not implied (#20; acceptance evidence for #29).
- `chart_tuik_data` draws CSV/XLS/XLSX rows as a PNG line or bar chart (MCP image
  content) with a JSON summary of each series' points, gaps and first/last/min/max.
  It shares `get_tuik_data`'s snapshot, filters and query. It adds label columns,
  `series` splitting for long-format dataflows, `last` and line `labels`, and shows
  the title, unit, source and retrieval date. Gaps are never drawn as zero.
  matplotlib is the optional `viz` extra (#72).
- Source-checked sequential AI relevance review with live stdio MCP query/resource
  replay, bounded table probes, retained first-pass grades and explicit revision
  reasons. `score-ai --require-reviewed` validates full-pool coverage and source
  references. ADR 0004 adopts the disclosed AI method for project acceptance;
  independent human validation is not implied (#11, #20).
- Explicit single-AI relevance pilot over 917 pooled query–document pairs, with
  a source-bound `score-ai` path that cannot satisfy the human release gate.
  Blank CSV judge sheets support spreadsheet editing and validated import;
  `score --require-complete` rejects pending human judgments/adjudication (#11, #20).
- Indexed MCP release smoke (`--indexed-only`, optional `--index-query`) follows
  a real OpenSearch dataset hit through resource resolution to verified bounded
  CSV rows, rejecting fallbacks. Full smoke selects portal search explicitly for
  tables/publications; v0.3.0 readiness documents remaining evaluation/release gates (#29).
- Frozen-corpus OpenSearch evaluation collection for built-in Turkish, multi-field
  and synonym variants, with engine/configuration provenance, deterministic ties,
  disposable indices and rejection of incomplete runs. Relevance judgments and
  the final full-corpus comparison is recorded above (#20).

- `search_tuik` uses the optional metadata index for datasets, bulletins and metadata
  with kind/theme/date/downloadability filters, scores, highlights, provenance,
  bounded full-match kind/theme facets and explicit source/backend reporting.
  Existing `results` and `query`/`limit` calls remain supported; `live=true` selects
  the portal. Literal/portal fallbacks report unsupported filters and unavailable
  facets, with null totals/truncation when unknown (#18).
- `read_tuik_document(..., tables=true)` returns a PDF page's ruled tables, best
  effort, with page, title, header or positional columns, string rows and
  warnings. Zebra-striped fragments are joined and unruled rows rebuilt. Coverage
  was measured on the recorded publication and methodology PDFs (#8).
- File content index: `tuik-mcp-index --content` indexes the workbook tables
  (titles, headers, notes) and PDF passages of the newest bulletin per series, and
  `--content-file` adds files on demand. `search_tuik` with `kind="table"` or
  `kind="document_chunk"` returns the file ID with the matching `sheet` or `page`.
  Downloads are budgeted per run and cached in a content state (#22).
- Separate OpenSearch 3.3.2 integration CI job with service health checks and retained
  JUnit results. Opt-in real-cluster tests cover Turkish case/ASCII/inflection,
  apostrophes, abbreviation synonyms, typos, combined filters, highlights and both
  fallback routes, using UUID-isolated indices and recorded upstream fixtures (#19).

### Changed

- Index schema version 2 adds `page`/`sheet` locators; run one `--full` build after
  upgrading (#22).

### Fixed

- Metadata crawling deduplicates advertised formats before enforcing the unique
  format budget, so bulletins with many XLS attachments remain indexable. Recorded
  bulletin retrieval times can be preserved when replaying retained responses (#20).
- Historical bulletin-series links may omit their display title; the crawler
  retains their validated URL identity instead of rejecting the whole bulletin (#20).
- Optional bounded listing retries recover overlapping portal pages only when
  every pass retains the advertised total and dates and the union reaches the
  exact unique-ID count. Unresolved or changing corpora still fail before detail
  downloads, indexing or checkpointing (#20).
- Relevance baseline collection explicitly disables the optional index, supports
  the same frozen metadata JSONL as the OpenSearch variants and rejects resume
  against a changed corpus. Atomic, optionally compressed checkpoints retain
  reproducible full-corpus evidence (#20).
- Search evaluation rejects orphaned or malformed document metadata before
  scoring or judge-sheet export. Resumable baseline collection records provenance
  in every checkpoint and rejects changed/missing source revisions or collector
  hashes before network access or checkpoint writes (#11).

## [0.2.0] - 2026-10-29

### Added

- Optional `get_tuik_data` structured queries: bounded projection, exact equality and
  membership, inclusive numeric/date/period ranges, stable multi-key sorting and
  authenticated process-local cursor pagination. Complete result snapshots preserve
  source strings/provenance; expired, evicted or mismatched cursors require restart
  without silently downloading a newer file. `TUIK_QUERY_*` budgets bound scans,
  sorting, retained state and UTF-8 responses; legacy calls remain compatible (#52).

- Release acceptance smoke paths explicitly follow a bulletin's related CSV dataset
  and a bulletin Excel attachment, recording snapshot/source provenance, package and
  Python versions, and pacing. The eight-tool verification and release checklist
  distinguish prepared code from the final promotion/tag (#10).

- `SearchBackend` protocol with `OpenSearchBackend` (synonym-aware `best_fields` over
  title, ASCII title, description, body and theme; a weaker first-letter-preserving
  fuzzy clause; kind/theme/downloadable/publication-date filters; bounded highlights),
  `LiteralBackend` (previous dataflow matching) and `PortalBackend`. With
  `TUIK_OPENSEARCH_URL` set, `search_tuik_datasets` ranks the index and reports
  `backend`; an unconfigured or unreachable cluster falls back to literal (datasets)
  or portal (general) search with a warning (#17).

- `inspect_tuik_data` profiles a CSV/XLS/XLSX table before querying: exact column keys,
  most frequent observed values with counts, empty counts, uninterpreted placeholder
  symbols and conservative inferred type hints that never resolve ambiguous separators
  or dates. Coverage, distinct-count exactness and output budgets are explicit and
  configurable (`TUIK_INSPECT_*`); profiles are cached per file snapshot (#51).

- `tuik-mcp-index` crawls public metadata and writes it to OpenSearch: `--full`
  loads a new versioned index, verifies its document count and atomically moves the
  alias, keeping previous versions for rollback; `--incremental` upserts only new or
  changed documents and reports stale IDs without deleting them. Crawl state advances
  only after a successful write; `--dry-run` plans without writing (#16).

- GitHub branch ruleset payloads requiring PRs, one approval and CI for `master`
  and `develop`, with a tested branch-policy check enforcing topic PRs into
  `develop` and promotions only from `develop` into `master`.

- Turkish search evaluation preparation: 48 categorized queries, real portal/literal
  baseline snapshots, pooled top-ten candidates, independent judge sheets, validated
  label merge and corpus-aware nDCG/MRR/pooled-recall/Cohen's κ reporting. Human
  judgments and measured baseline quality remain pending (#11).

- `read_tuik_document` reads PDF publication, report and methodology pages with
  glyph deduplication, Turkish text normalization, page provenance and bounded
  character output. Same-page `next_offset` continuation prevents skipped text;
  blank/scanned pages are explicit and OCR stays out of scope (#7).
- Shared parsed PDF page caching and configurable PDF page/glyph budgets complete
  the file-reading cache/limit paths. Repeated cancellation retains the worker slot (#9).

- `get_tuik_data` reads verified XLS/XLSX files into bounded rows with sheet selection,
  exact filters, units, titles, source notes, layout warnings and file provenance.
  Bulletin dataflow CSV choices still take precedence; file IDs select Excel explicitly (#6).

- Configurable spreadsheet input budgets, XLSX archive/grid preflight and a byte-bounded
  one-hour file/parsed-table cache. Worker cancellation retains the parsing slot until
  the worker finishes; parser budget failures report `parse_limit_exceeded` (#9).

- Stable, title-derived IDs for downloadable tables (`table:`), reports
  (`report:`), publications (`publication:`) and bulletin files
  (`press:<id>:file:<key>`, in `download_links[].resource_id`). They resolve in a
  fresh server session by re-reading the bulletin or searching for the title;
  download URLs change over time (ADR 0003, #27).
- XLS/XLSX table parser for TÜİK spreadsheets: title, English title and unit
  rows, multi-row bilingual headers, section rows, forward-filled row labels,
  number-format precision, footnotes and source lines; raw-grid fallback with a
  warning for unrecognized layouts. `xlrd` and `openpyxl` become runtime
  dependencies (#5).
- Automatically close explicitly completed issues when their PR is merged into
  `develop`, with offline tests and documented PR-body closing directives.
- Weekly, optional live contract checks with JSON/JUnit/log artifacts, deduplicated
  failure issue reporting, a no-traffic reporting exercise and a triage guide (#28).
- Bulletin metadata exposes related dataflows with catalog export formats and
  category paths. `get_tuik_data` returns `related_datasets` choices for these
  bulletins; callers select a reusable `flow:` ID to retrieve CSV rows (#26).
- Optional local OpenSearch 3.3.2 Compose stack, Dashboards profile, search client
  extra and validated environment settings (#12). Existing tools stay live-only.
- A 12-file, 2.64 MB official XLS/XLSX/PDF fixture corpus with provenance, SHA-256,
  reviewed golden source observations and a paced refresh script (#4).
- Shared parser models and registry, with CSV parsing offloaded from the MCP event
  loop and parsing library decisions recorded in ADR 0001 (#3).

- Git Flow process: `develop` branch, CI workflow, PR and issue templates,
  `CONTRIBUTING.md` and this changelog.

- Packaged Turkish/ASCII analysis settings and candidate search-time synonym graphs,
  a reproducible OpenSearch token/retrieval probe, measured ASCII-inflection limits,
  search field design and the optional derived-index architecture ADR (#13).

- Validated metadata-only `IndexDocument` schema, packaged strict OpenSearch mapping,
  isolated title-prefix field, versioned physical index names and opt-in real-cluster
  mapping verification (#14).

- JSON-only metadata crawler with full listing enumeration, source-aware category and
  series relations, replayable incremental snapshots, atomic JSONL/state output,
  explicit partial dry runs and crawl-specific pacing/429 cooldown (#15).

### Changed

- `get_tuik_data` CSV exports now use the one-hour content-addressed file snapshot
  shared with Excel/PDF and `inspect_tuik_data` (previously only CSVs up to 256 KiB
  were cached for 10 minutes). `ok` responses report `snapshot_id`, and CSV
  `retrieved_at` is the snapshot's retrieval time (#51).

- Weekly live contract checks run the full MCP smoke workflow once, preserving
  the separate JSON report without duplicating upstream requests (#28).

- README client configuration uses a generic path instead of a developer's machine path.

### Fixed

- Numeric range operands follow the same ambiguity rules as source values:
  integer-only columns cannot silently interpret `1.234`/`1,234` as decimal bounds.
  Invalid operands identify the range minimum/maximum and expected notation (#52).

- Live MCP smoke forwards `TUIK_*` settings to its stdio server so configured request
  and download intervals are honored, including slower release verification (#10).

- Complete metadata crawls reject overlapping pages when unique bulletin IDs do
  not match the upstream total, preserving prior output/state instead of silently
  losing unseen bulletins. Reports retain incomplete-listing diagnostics (#15).

- XLSX preflight counts optional cell coordinates within each row instead of
  accumulating columns across the worksheet, preserving correct cell-budget checks (#9).

- Parser errors are translated at one service boundary into source error codes
  (`parse_failed`, `invalid_filters`, `unsupported_download`; CSV keeps
  `malformed_response`), so file problems are never reported as `invalid_input` (#39).
- Bulletin dataflows retain category paths at every depth. Catalog outages preserve
  bulletin metadata without caching unconfirmed availability, and unavailable
  related datasets no longer prevent existing file downloads (#26).
- Empty or whitespace-only `TUIK_OPENSEARCH_URL` values disable the optional backend
  instead of preventing the MCP server from starting (#12).
- Production download verification recognizes XLSX archives served with XLS media
  types; interrupted fixture refreshes retain unprocessed resume entries (#4).
- Parser failures are distinct from invalid caller input; the CSV adapter keeps
  its existing source-error codes (#3).

- TÜİK's download throttle page is no longer reported as `unsupported_download`.
  Table/document downloads run one at a time at least 6 seconds apart
  (`TUIK_DOWNLOAD_INTERVAL_SECONDS`), the page's countdown is honored with a retry,
  and persistent throttling returns `download_throttled` (#2).

## [0.1.0] - 2026-10-17

### Added

- MCP stdio server with `search_tuik`, `get_tuik_resource`, `get_tuik_data`,
  `list_tuik_themes` and `search_tuik_classifications`.
- `search_tuik_datasets` for the public export catalog, with theme and
  downloadable-only filters and reusable `flow:` IDs.
- Bounded, rate-limited HTTP client with host allowlist and in-memory TTL cache.
- Offline test suite, opt-in live tests and `scripts/live_smoke.py`.
- Dockerfile.

[Unreleased]: https://github.com/ByRock010/TUIK-Data-MCP-Server/compare/v0.3.0...develop
[0.3.0]: https://github.com/ByRock010/TUIK-Data-MCP-Server/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/ByRock010/TUIK-Data-MCP-Server/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ByRock010/TUIK-Data-MCP-Server/releases/tag/v0.1.0
