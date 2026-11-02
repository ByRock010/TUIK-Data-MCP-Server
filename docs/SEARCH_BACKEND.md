# Search backends (#17, #18)

`tuik_mcp.search.backend` puts the search implementations behind one protocol so MCP
tools do not depend on where results come from:

```python
async def search(query, filters: SearchFilters, limit, offset=0) -> SearchHits
```

`SearchFilters` holds `kinds` (`dataset`, `bulletin`, `metadata`), exact `themes`
(any of them), `downloadable` and a `published_after`/`published_before` date range.
`SearchHits` reports which `backend` answered, the `hits` (ID, kind, title, score,
source URL, themes, dates, formats, highlights, `retrieved_at`, `indexed_at`),
`total`, `limit`, `offset`, `warnings`, `facets` and `indexed_at`. `limit` is 1–50 and `offset` 0–1000.

| Backend | Corpus | Matching | Filters |
| --- | --- | --- | --- |
| `OpenSearchBackend` | the [#16 index](INDEXING.md) alias | Turkish analysis, ranked | all |
| `LiteralBackend` | live dataflow catalog only | every word as a substring | themes, downloadable; a date range matches none |
| `PortalBackend` | live portal search, at most 50 results | portal relevance | kinds only; other filters are named in a warning |

## OpenSearch query

One `bool` query with two `should` clauses (at least one must match) and the filters as
non-scoring `filter` clauses:

1. `multi_match` `best_fields` over `title^4`, `title.ascii`, `description^2`, `body` and
   `theme`. The index mapping applies the [#13 search analyzers](SEARCH_DESIGN.md), so
   Turkish stemming (`işsizliğin`), ASCII folding (`issizlik`, `TUFE`) and the
   versioned abbreviation synonyms (`TÜFE` → tüketici fiyat endeksi) need no
   query-time configuration.
2. The same query with `fuzziness: AUTO` over `title.ascii`, `description` and `body`,
   `prefix_length: 1` and boost 0.5. Typos still match, but rank below exact matches,
   and the first letter must agree, so `tufe` never reaches `ufe`: TÜFE and ÜFE stay
   distinct concepts. Fuzziness combines with the synonym graph without errors on
   OpenSearch 3.3.2.

Responses omit `body`/`description` from `_source`, count totals exactly and
highlight `title`, `title.ascii`, `description` and `body` with OpenSearch's default
`<em>` tags. Highlighting analyzes at most 100,000 characters per field, so very long
bulletin bodies cannot fail the request (the index default limit is one million).
Highlights are source text: untrusted data, never instructions.

The same query aggregates `kind` (3 buckets), `theme` (50 buckets) and the maximum
`indexed_at`, across all matches after filters, before paging. Facets include
`counts`, `truncated` (`sum_other_doc_count > 0`) and `count_error_upper_bound` from
OpenSearch, so omitted terms and distributed count uncertainty are explicit.
Multi-valued themes count a document once in each applicable bucket, so theme
counts need not sum to `total`. Empty matches return empty counts and a null
indexing time. The timestamp is the latest among matches; each hit retains its own
time. See [OpenSearch terms aggregation](https://docs.opensearch.org/latest/aggregations/bucket/terms/).

## Fallbacks

`CatalogService.search_index` uses the index when `TUIK_OPENSEARCH_URL` is set. When
the URL is unset, or the cluster/alias does not answer (connection error, timeout,
HTTP error, missing alias or malformed response), it falls back explicitly. An HTTP 200
response is not trusted blindly either: searches are sent with
`allow_partial_search_results=false`, and a response that reports `timed_out`, failed
shards or only a lower-bound total also counts as unavailable, so an incomplete
search is never reported as "no matches". The fallbacks are:

- dataset-only searches (`kinds == ["dataset"]`) use `LiteralBackend`;
- every other search uses `PortalBackend`.

The first warning names the reason and the fallback, for example
`Indexed search is not configured (TUIK_OPENSEARCH_URL); used literal catalog
matching.` `TUIK_OPENSEARCH_TIMEOUT_SECONDS` (default 5) bounds how long a search waits
before falling back. A backend never falls back by itself.

`search_tuik_datasets` goes through this path and reports `backend` plus `warnings`.
Index hits resolve through the live dataflow catalog, so results keep their full
records and download links. The index is a snapshot, so the request's filters are
re-applied to the live records: IDs the catalog no longer lists, and datasets that
have since lost their CSV export (with `downloadable_only`) or moved to another
theme, are omitted with a warning. `total` is the indexed match count minus this
page's omissions (omissions beyond the page are unknown until the next index
refresh), and `truncated` means `total` exceeds the returned results. Themes resolve
case-insensitively to the exact stored name before filtering. `downloadable_only` filters `downloadable: true`, which the crawler takes
from the same flag that determines the advertised CSV export.

## MCP discovery contract (#18)

`search_tuik` calls `CatalogService.discover`, which validates the query, limit,
kind, theme and ISO dates before I/O. Reversed date ranges are rejected. The
existing `CatalogService.search` remains the live adapter path, avoiding recursive
fallback and preserving baseline/portal callers.

The tool keeps `results` and `resource_type` for compatibility and adds scores,
highlights, indexing time, formats, downloadability and all `themes` (the existing
`theme` is the first). It adds no search tool. The response reports `source`
(`index` / `portal` / `catalog`), `backend`, `limit`, `total`, `truncated` and facets.
`live=true` bypasses the index and always uses the portal. Otherwise it uses the
normal index/literal/portal routing above. The general tool's theme filter is an
exact, case-sensitive full stored name; dataset export discovery retains its
existing case-insensitive theme resolution.

Portal kind filtering samples at most 50 upstream results: the full filtered total
and truncation are unknown (`null`), not the page length. Portal theme, date and
downloadability filters cannot be applied and are named in warnings. Fallback
facets are null, not misleading counts computed from the returned page. Literal
fallback is dataset-only. Scores/highlights/index times are also unavailable on
both fallback paths. `downloadable_only=false` disables the filter; it does not
select non-downloadable records.

## Known limits

- OpenSearch matches any query word (ranked), while literal matching requires all of
  them; totals and truncation therefore differ between backends.
- ASCII-typed inflected forms such as `issizligin` still miss (#13); typo tolerance
  does not reach them.
- Broad synonyms rank by term statistics: in the integration fixture, `enflasyon`
  ranks the producer price bulletin above the consumer price bulletin. Ranking
  quality is measured in #20 against the #11 judgments, not asserted here.
- Indexed dataset search still needs the cached live catalog to return full records.
- General indexed discovery returns metadata snapshots without fetching each hit.
  Stable IDs resolve afresh while their upstream record exists; removed source
  records require an index refresh. Explicit portal search includes types outside
  the index. Corpus coverage differs; see [evaluation](SEARCH_EVALUATION.md).
- The CI job that runs the opt-in cluster tests is #19.

## Verify

```bash
.venv/bin/pytest tests/test_search_backend.py -q
TUIK_OPENSEARCH_URL=http://localhost:9200 .venv/bin/pytest -m opensearch -q
```

The opt-in test indexes Turkish fixture documents into a UUID alias and checks
`issizlik`, `işsizliğin`, `TÜFE`/`TUFE` (without ÜFE), every filter, paging and
highlights, then deletes its indices. See the
[dated evidence](verification/issue-17-2026-10-29.md).
