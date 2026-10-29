# Search backends (#17)

`tuik_mcp.search.backend` puts the search implementations behind one protocol so MCP
tools do not depend on where results come from:

```python
async def search(query, filters: SearchFilters, limit, offset=0) -> SearchHits
```

`SearchFilters` holds `kinds` (`dataset`, `bulletin`, `metadata`), exact `themes`
(any of them), `downloadable` and a `published_after`/`published_before` date range.
`SearchHits` reports which `backend` answered, the `hits` (ID, kind, title, score,
source URL, themes, dates, formats, highlights, `retrieved_at`, `indexed_at`),
`total`, `limit`, `offset` and `warnings`. `limit` is 1–50 and `offset` 0–1000.

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

## Fallbacks

`CatalogService.search_index` uses the index when `TUIK_OPENSEARCH_URL` is set. When
the URL is unset, or the cluster/alias does not answer (connection error, timeout,
HTTP error, missing alias or malformed response), it falls back explicitly:

- dataset-only searches (`kinds == ["dataset"]`) use `LiteralBackend`;
- every other search uses `PortalBackend`.

The first warning names the reason and the fallback, for example
`Indexed search is not configured (TUIK_OPENSEARCH_URL); used literal catalog
matching.` `TUIK_OPENSEARCH_TIMEOUT_SECONDS` (default 5) bounds how long a search waits
before falling back. A backend never falls back by itself.

`search_tuik_datasets` goes through this path and reports `backend` plus `warnings`.
Index hits resolve through the live dataflow catalog, so results keep their full
records and download links; IDs the catalog no longer lists are omitted with a
warning. Themes resolve case-insensitively to the exact stored name before
filtering. `downloadable_only` filters `downloadable: true`, which the crawler takes
from the same flag that determines the advertised CSV export.

## Known limits

- OpenSearch matches any query word (ranked), while literal matching requires all of
  them; totals and truncation therefore differ between backends.
- ASCII-typed inflected forms such as `issizligin` still miss (#13); typo tolerance
  does not reach them.
- Broad synonyms rank by term statistics: in the integration fixture, `enflasyon`
  ranks the producer price bulletin above the consumer price bulletin. Ranking
  quality is measured in #20 against the #11 judgments, not asserted here.
- Indexed dataset search still needs the cached live catalog to return full records.
- MCP exposure for bulletins/metadata, facets and highlights is #18; the CI job that
  runs the opt-in cluster tests is #19.

## Verify

```bash
.venv/bin/pytest tests/test_search_backend.py -q
TUIK_OPENSEARCH_URL=http://localhost:9200 .venv/bin/pytest -m opensearch -q
```

The opt-in test indexes Turkish fixture documents into a UUID alias and checks
`issizlik`, `işsizliğin`, `TÜFE`/`TUFE` (without ÜFE), every filter, paging and
highlights, then deletes its indices. See the
[dated evidence](verification/issue-17-2026-10-29.md).
