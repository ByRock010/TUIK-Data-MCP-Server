# Usage checks and performance — 2026-11-10

`scripts/usage_perf.py` runs 32 usage steps against the server through a real MCP
stdio session: every tool, both search modes, CSV, Excel and PDF reads, and the
error paths. Each step checks the answer and records wall time, response size and
the upstream requests the server made. A project-wide read of `src/` plus this
baseline found three wrong behaviours and four costs worth removing. This report
lists them with before and after numbers.

**Before** is `develop` plus PR #81 (`2243c2e`); **after** is this change on top of
it. All numbers are medians of three runs per side on one machine (Apple M4,
local OpenSearch 3.3.2, live TÜİK). They show size and direction, not a benchmark
across machines.

## Result

| | Before | After |
| --- | ---: | ---: |
| Usage checks passed | 29 / 32 | **32 / 32** |
| Hybrid search, warm (three unfiltered queries) | 177–306 ms | **33–34 ms** |
| Sum of warm medians, all steps | 1,573 ms | 957 ms |
| Same, without the paced missing-bulletin call | 1,070 ms | 455 ms |
| Response bytes, all 32 steps | 615,663 | **340,189** (−45 %) |
| Default workbook read (`get_tuik_data`, no arguments) | 222,562 bytes | 64,164 bytes |
| Chart response | 107,859 bytes | 38,028 bytes |
| Cold pass (downloads and TÜİK pacing included) | 13.0 s | 12.6 s |
| Tool definitions (`tools/list`) | 21,848 bytes | 21,173 bytes |
| Offline tests | 744 | 757 |

## What was wrong, and what changed

### Fixed behaviour

1. **A missing bulletin was reported as `malformed_response`.** TÜİK answers
   `press:<unknown>` with HTTP 200 and `{"isError": true, "message": "Bülten
   bulunamadı"}`. A client could not tell a wrong ID from a broken portal. It is
   now `not_found`; other portal errors keep `malformed_response`.
2. **A default workbook read returned 222 KB.** Only `query` pages had an output
   budget. `get_tuik_data` without `query` returned every requested row: 100 rows
   of a 37-column bilingual workbook is about 55,000 tokens, which clients such as
   Claude Code reject outright. Such pages now share `TUIK_QUERY_MAX_OUTPUT_BYTES`
   (64 KiB): the rows that fit are returned with `truncated: true`, the exact
   `total_rows`, and a warning that names `query={}` and `query.columns`. At least
   one row is always returned. **This changes what a large `limit` returns without
   `query`.**
3. **A theme filter that matched nothing said nothing.** `theme="nufus"` returned
   zero results with no hint that themes match a full name exactly. A zero-result
   search with a theme filter now carries a warning pointing to `list_tuik_themes`.

### Removed costs

4. **Hybrid search spent over 90 % of its time highlighting.** Inside a hybrid
   request the highlighter cannot read the query's terms and re-runs the query per
   returned document: about 20 ms per hit, so 240 ms at `limit=10` and over 1 s at
   `limit=50`. The same documents highlight in about 1 ms each through the plain
   lexical query. The hybrid request now carries no highlight, and a second
   request restricted to the returned IDs fetches it.

   | 48 evaluation queries, `limit=10`, OpenSearch `took` | Median | p90 |
   | --- | ---: | ---: |
   | Hybrid with inline highlight (before) | 238 ms | 324 ms |
   | Hybrid without highlight | 16 ms | 19 ms |
   | Lexical highlight of the same IDs | 11 ms | 29 ms |

   Rankings, totals and facets are unchanged: only the highlight moved. Highlights
   are identical for 391 of 480 hits. The rest differ because the lexical request
   matches multi-word synonyms as phrases (`tüketici fiyat endeksi`), where the
   hybrid path highlighted each word wherever it occurred. Hybrid highlights are
   now the same as lexical-mode highlights. If the second request fails, the
   ranked hits are returned with a warning.
5. **Tool results were indented JSON.** FastMCP's default text rendering adds
   10–30 % whitespace, which a model pays for as tokens. Results are now compact
   JSON with unchanged `structuredContent`; the output budgets measure the same
   rendering. Row responses shrink 20–35 % (`csv_default` 60,076 → 46,701 bytes).
6. **Chart images were RGBA PNGs.** A 256-colour palette holds a flat-colour chart
   in a third of the bytes (79,019 → 26,774 for the 24-month chart) with no
   visible difference, for about 10 ms more CPU per chart.
7. **Each CSV was parsed and cached twice.** `inspect_tuik_data` and the
   query/chart path used different cache keys for the same complete table: two
   parses and two 9.6 MB snapshots for the 3.5 MB labour CSV, out of a 32 MB
   cache. Inspection now reads the table that queries and charts read. A CSV
   beyond the query budgets is still profiled from its first rows. The first query
   after an inspection drops from 97 ms to 45 ms in process.

Also fixed: the User-Agent said `tuik-mcp/0.2` (the docs said `0.1`) for package
0.3.0. It now follows the installed version.

## Per step

Warm medians in milliseconds and response bytes, before → after.

| Step | Tool | Before | After | Warm ms | Bytes |
| --- | --- | --- | --- | --- | --- |
| themes | list_tuik_themes | ok | ok | 1.3 → 0.9 | 5,086 → 4,116 |
| search_ascii | search_tuik | ok | ok | 202.0 → 33.0 | 8,255 → 6,508 |
| search_typo | search_tuik | ok | ok | 176.9 → 32.9 | 5,565 → 4,273 |
| search_paraphrase | search_tuik | ok | ok | 306.1 → 34.1 | 8,462 → 6,776 |
| search_filtered | search_tuik | ok | ok | 16.9 → 15.4 | 5,613 → 4,402 |
| search_passage | search_tuik | ok | ok | 9.1 → 8.2 | 3,973 → 3,115 |
| search_table | search_tuik | ok | ok | 8.4 → 7.5 | 2,439 → 1,937 |
| search_gibberish | search_tuik | ok | ok | 31.4 → 14.3 | 779 → 641 |
| search_wrong_theme | search_tuik | **FAIL** | ok | 11.0 → 10.3 | 762 → 749 |
| search_live | search_tuik | ok | ok | 1.4 → 1.0 | 5,005 → 4,167 |
| search_bad_date | search_tuik | ok | ok | 1.0 → 0.6 | 478 → 453 |
| datasets_ascii | search_tuik_datasets | ok | ok | 18.2 → 16.5 | 11,545 → 8,888 |
| classifications | search_tuik_classifications | ok | ok | 1.7 → 1.1 | 6,515 → 5,097 |
| bulletin | get_tuik_resource | ok | ok | 2.5 → 1.8 | 46,791 → 44,444 |
| bulletin_missing | get_tuik_resource | **FAIL** | ok | 502.9 → 502.1 | 302 → 255 |
| bulletin_choice | get_tuik_data | ok | ok | 4.5 → 2.6 | 7,441 → 6,472 |
| csv_inspect | inspect_tuik_data | ok | ok | 2.4 → 2.0 | 11,405 → 7,411 |
| csv_rows | get_tuik_data | ok | ok | 42.1 → 40.0 | 5,166 → 4,141 |
| csv_default | get_tuik_data | ok | ok | 44.1 → 42.2 | 60,076 → 46,701 |
| csv_filter | get_tuik_data | ok | ok | 42.8 → 41.1 | 5,165 → 4,140 |
| csv_query | get_tuik_data | ok | ok | 44.2 → 42.0 | 2,727 → 2,208 |
| csv_cursor | get_tuik_data | ok | ok | 1.4 → 0.9 | 2,727 → 2,208 |
| csv_bad_filter | get_tuik_data | ok | ok | 4.5 → 4.1 | 336 → 311 |
| csv_bad_cursor | get_tuik_data | ok | ok | 1.0 → 0.6 | 288 → 263 |
| chart | chart_tuik_data | ok | ok | 73.7 → 86.5 | 107,859 → 38,028 |
| xls_rows | get_tuik_data | ok | ok | 2.2 → 1.8 | 11,034 → 9,683 |
| xls_default | get_tuik_data | **FAIL** | ok | 8.3 → 5.6 | 222,562 → 64,164 |
| xls_inspect | inspect_tuik_data | ok | ok | 2.6 → 2.8 | 47,415 → 40,547 |
| xls_query | get_tuik_data | ok | ok | 3.6 → 3.1 | 11,183 → 9,832 |
| pdf_page | read_tuik_document | ok | ok | 1.2 → 0.8 | 1,527 → 1,377 |
| pdf_tables | read_tuik_document | ok | ok | 1.2 → 0.8 | 3,610 → 3,460 |
| pdf_from_bulletin | read_tuik_document | ok | ok | 1.3 → 1.0 | 3,572 → 3,422 |

`bulletin_missing` stays at about 500 ms because failed lookups are not cached and
every TÜİK request keeps the 0.5 s pacing.

## Where the time goes now

- **Cold calls are bound by TÜİK.** The 3.5 MB labour CSV downloads in 1.4–1.8 s
  (TÜİK does not compress it) and bulletin files keep the 6-second spacing TÜİK's
  throttle requires. Parsing and profiling that CSV takes about 190 ms.
- **Warm data calls are 40–90 ms** on a 21,600-row table: a full CSV scan for a
  page without `query` (41 ms), 25 ms to re-read the 9.6 MB cached snapshot plus
  about 20 ms to evaluate a typed query, and about 40 ms to draw a chart. Cached
  metadata, profiles and PDF pages return in 1–3 ms.
- **The first hybrid search loads the embedding model** (0.5–0.8 s); later query
  embeddings take about 3 ms.

## Memory

| Server state | Resident memory |
| --- | ---: |
| After start | 75 MB |
| Bulletin, Excel and PDF reads | 92–105 MB |
| CSV inspect, query and chart (21,600 rows) | 200–235 MB |
| Any hybrid search (embedding model loaded) | about 1,050–1,200 MB |
| Search with `TUIK_SEMANTIC_SEARCH=false` | 91 MB |

The multilingual-e5-small ONNX model takes about 1 GB once loaded; thread count and
arena settings did not change that. Some runs read as low as 300–600 MB, probably
while macOS was compressing memory, so the lower numbers are not a floor. This change
does not affect memory.

## Found but not changed

These need a team decision, because they change a response shape, the index or
the evaluated model.

| Finding | Measured | Why it was left |
| --- | --- | --- |
| `get_tuik_resource` returns all metadata sections of a bulletin | 44 KB per call, 35 KB of it methodology text | Needs a new parameter or a default change |
| Rows repeat every column name | A 3-row workbook page is 9.7 KB | A columnar row format would break clients |
| Results travel twice, as text and as `structuredContent` | Wire size is double the text | Which one a client shows the model varies by client |
| `TableQuery` schema is repeated in two tool definitions | 12 KB of the 21 KB of tool definitions | Would need hand-written input schemas |
| The embedding model's memory | about 1 GB | A quantized model changes vectors: rebuild and re-evaluate |
| Failed lookups are not cached | 500 ms per repeated miss | Small; negative caching needs an expiry policy |
| TÜİK sends `ETag`; expired snapshots are re-downloaded in full | 1.4–1.8 s per 3.5 MB CSV each hour | Conditional requests need snapshot-identity rules |

## Reproduce

```bash
docker compose up -d opensearch          # with an index built by tuik-mcp-index
export TUIK_OPENSEARCH_URL=http://localhost:9200
.venv/bin/python scripts/usage_perf.py --output /tmp/usage-perf.json
.venv/bin/python scripts/usage_perf.py --compare /tmp/usage-perf.json --output /tmp/after.json
```

Without `TUIK_OPENSEARCH_URL` the indexed steps are reported as skipped. The script
exits non-zero when a check fails. The search steps need the labour bulletin's
methodology PDF and a workbook in the content index (`--content-file`).

## Files

- [`usage-performance-2026-11-10.json`](usage-performance-2026-11-10.json): all six
  run reports and the three-run medians used above.
- `scripts/usage_perf.py` and `tests/test_usage_perf.py`: the harness and its tests.
