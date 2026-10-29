# Snapshot-bound table queries (#52)

`get_tuik_data` keeps its existing arguments and adds two optional inputs:

```text
get_tuik_data(resource_id, filters=None, limit=100, sheet=None, query=None, cursor=None)
```

No new MCP tool is added. Without `query` or `cursor`, existing exact filters,
first-row limits, file selection and unsupported responses retain their behavior.
Pass `query={}` to opt into complete queries and continuation. Inspect the table
first to learn its actual column names and observed formats; type hints are
inferred, not official dimension declarations.

## Query schema

```json
{
  "columns": ["exact output column"],
  "predicates": [
    {"op": "eq", "column": "province", "value": "Ankara"},
    {"op": "in", "column": "year", "values": ["2024", "2025"]},
    {"op": "range", "column": "measurement", "value_type": "number", "decimal_separator": ",", "minimum": "0", "maximum": "10,5"}
  ],
  "sort": [
    {"column": "measurement", "value_type": "number", "decimal_separator": ",", "direction": "desc", "missing": "last"}
  ]
}
```

These are schema examples, not a claim that any source has these columns.
`columns` defaults to all columns, otherwise selects 1–50 unique exact names.
At most 20 predicates combine with **AND**. Membership accepts 1–100 exact strings
combined with **OR**. Equality and membership preserve whitespace and case.
Legacy `filters` AND the predicates, but the same column cannot appear in both:
put all conditions on that column in `predicates` instead. Unknown properties,
operators, non-string operands and duplicate projection/sort columns are rejected.

All referenced columns are validated, including filter/sort columns excluded from
the projection. Operation order is **filter → stable sort → page → project**.
`total_rows` counts every matching source row before pagination. Duplicate source
rows remain separate rows; original parsed-row position is the final sort tie-breaker.

## Types and ordering

Range predicates require `value_type` (`number`, `date`, `period`) and at least
one inclusive bound (`minimum`/`maximum`). The minimum cannot exceed the maximum.
Up to five sort keys are ordered by priority. Each uses `direction: asc|desc`
(default `asc`), `missing: first|last` (default `last`) and `value_type`
(default `text`). Missing placement is independent of ascending/descending order.

- **Text:** deterministic original Unicode code-point order, not locale collation.
- **Number:** exact `Decimal` comparisons on plain signed integers or decimals.
  A single observed separator is inferred using the same value-shape rules as
  inspection. `1.234`-only or `1,234`-only columns require explicit
  `decimal_separator: "."|","`; mixed separators are not reinterpreted. Grouping,
  exponents and percent suffixes are rejected with an actionable `invalid_query`.
  Explicit numeric ordering can interpret zero-padded digits numerically; choose
  text ordering for identifier columns. No returned value is reformatted.
- **Date:** valid ISO `YYYY-MM-DD` only; no guessed day/month order or timezone.
- **Period:** requires `period_format`. `year` accepts `YYYY`; `month` accepts
  `YYYY-MM`/`YYYYMmm`; `quarter` accepts `YYYY-Qn`/`YYYYQn`; `half` accepts
  `YYYY-Hn`/`YYYYSn` (optional hyphen); `week` accepts `YYYY-Www`/`YYYYWww` and
  validates ISO week existence. Frequencies are never silently mixed.

The same ambiguity rule applies to **range operands**. An integer-only column does
not establish a decimal separator: `minimum: "1.234"` or `maximum: "1,234"` requires
an explicit `decimal_separator`. A matching separator established by unambiguous
source decimals can also disambiguate that operand. Explicit `"."` interprets
`"1.234"` as the decimal 1.234; it never means grouped integer 1234. To request
integer 1234, write `"1234"`. Bound errors identify `minimum`/`maximum` and the
expected notation, independently from errors in the source column itself.

Typed referenced columns are validated against the **complete parsed table**, even
if a predicate would remove an incompatible row. Empty/whitespace values and the
inspection markers `-`, `–`, `—`, `.`, `..`, `...`, `…`, `:`, `x`, `X`, `*` are
excluded from ranges and use missing sort placement. They stay original strings;
equality/membership can explicitly select them. This is a query-ordering rule,
not a declaration of their official statistical meaning. Zero is a valid number.

## Continuation and provenance

Successful advanced responses include `has_more`, `next_cursor` and
`cursor_expires_at`, along with exact `total_rows`, source URL, retrieval timestamp,
content SHA-256 `snapshot_id`, format/sheet, unit, titles, notes and layout warnings.
`truncated` means there are remaining rows in this advanced result, including on
later pages. Final/empty results have `has_more: false` and `next_cursor: null`.

```json
{"resource_id": "the same resource ID", "cursor": "the returned next_cursor", "limit": 100}
```

Omit `query`, `filters` and `sheet` to reuse them. If repeated, the validated query
(including defaults and list order), exact filters and resolved sheet must match.
The resource ID must match exactly, even if an alias might resolve to the same file.
The page size may change within 1–1000. Replaying a cursor returns the same rows.

Cursors are authenticated with HMAC-SHA256 and a fresh secret for each
`CatalogService`/server lifespan. They identify immutable bounded result state,
its query/projection/sort, resource/sheet, content hash, extraction/query version
and the next row position. They are opaque client inputs, not public offset
parameters. `QUERY_VERSION` changes when query/extraction semantics change.
The cache is shared across MCP connections handled by that service instance;
it does not persist across server restarts or work across separate processes.

TTL defaults to 900 seconds, is capped by the original file body's remaining
lifetime, and is **not extended** by continuation/replay or later re-downloads of
identical bytes. Earlier
expiry/eviction of either the file body or query-result snapshot invalidates the
cursor. Continuation checks retained state **before resource resolution** and never
fetches current metadata/files to resume an old offset. Restart without a cursor
to deliberately begin against the ordinary current retained/fresh file selection.
No snapshot is pinned indefinitely to satisfy a cursor.

## Budgets and errors

| Setting | Default | Applies to |
| --- | ---: | --- |
| `TUIK_QUERY_MAX_ROWS` | 100,000 | Complete source rows examined |
| `TUIK_QUERY_MAX_CELLS` | 2,000,000 | Source rows × columns |
| `TUIK_QUERY_MAX_SORT_ROWS` | 50,000 | Matching rows retained for sorting |
| `TUIK_QUERY_MAX_SNAPSHOT_BYTES` | 8 MiB | One serialized result/query/provenance snapshot |
| `TUIK_QUERY_MAX_OUTPUT_BYTES` | 64 KiB | Entire response, UTF-8 indented JSON as rendered by FastMCP |
| `TUIK_QUERY_CACHE_BYTES` | 16 MiB | Combined immutable result-state payloads, at most 128 entries |
| `TUIK_QUERY_CURSOR_TTL_SECONDS` | 900 | Fixed process-local continuation lifetime |

Existing HTTP, workbook, sheet/cell, ZIP and file-cache limits also apply. CSV
query scans stop at the row/cell bound; they never report partial scans as exact
counts. Excel retains its existing bounded workbook parsing, then validates the
selected table against query limits. Parsing, query work and JSON serialization
share the cancellation-safe worker slot. Only one advanced query pipeline runs at
a time; cancelling a worker waits for its completion before releasing the slot.
These are bounded ordinary workloads, not a hard OS-level CPU/RSS sandbox.

Parsed tables and original bodies share `TUIK_PARSE_CACHE_BYTES` (default 32 MiB).
A large CSV's parsed JSON may evict its body even when the query result fits the
separate query cache. Multi-page queries then fail explicitly with a retention
error; operators can increase `TUIK_PARSE_CACHE_BYTES` within their memory budget.
Increasing `TUIK_QUERY_CACHE_BYTES` alone does not retain an evicted file body.

The response budget shrinks **rows only**, preserves exact totals, and supplies the
next cursor at the first unreturned position. If metadata or a single row cannot
fit, the query fails explicitly; restart with a narrower projection or budget.
Snapshot/sort/scan budget failures fail rather than returning a partial total.

| Code | Meaning/action |
| --- | --- |
| `invalid_input` / MCP schema error | Invalid caller schema, limit or sheet |
| `invalid_columns` | Unknown projection/filter/sort column; inspect first |
| `invalid_query` | Incompatible/ambiguous typed values or conflicting filters |
| `query_limit_exceeded` | Query/sort/result/response cannot fit the budgets |
| `parse_limit_exceeded` | Source parser or bounded CSV scan limit exceeded |
| `invalid_cursor` | Malformed/tampered cursor, changed version or server session; restart |
| `cursor_expired` | Expired/evicted file or query snapshot; restart |
| `cursor_mismatch` | Changed resource, query, filters or sheet; restart |
| `unsupported_query` | Selected format has no CSV/Excel query implementation |

File download/parser failures retain source URLs and their existing codes, such as
`malformed_response`, `parse_failed`, `download_throttled` and `upstream_http`.
Bulletins with related dataflows still require an explicit choice; PDF/ZIP table
queries and automatic statistical/time-series normalization are outside this scope.

## Verify

```bash
pytest tests/test_table_query.py tests/test_query_mcp.py -q
TUIK_MIN_INTERVAL_SECONDS=2 python scripts/live_query_smoke.py --output /tmp/query-live.json
```

The focused live workflow runs inspect → filtered/projected/numerically sorted
query → at least two pages for a public CSV and an explicit bulletin Excel file.
It checks identical snapshots/source/timestamps and compares the concatenated
two pages with a two-row query. `tests/test_table_query_live.py` is opt-in `live`.
