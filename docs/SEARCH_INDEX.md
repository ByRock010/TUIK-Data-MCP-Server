# Search index contract (v0.3)

Issue #14 builds on #13's [analysis design](SEARCH_DESIGN.md). It supplies the
shared `IndexDocument` and packaged `search/mapping.json`; it does not create a
production index, crawl sources or route MCP searches to OpenSearch.

```python
from tuik_mcp.search.documents import IndexDocument
from tuik_mcp.search.schema import index_definition, physical_index_name

name = physical_index_name("tuik-resources", 1)  # tuik-resources-v1
definition = index_definition()                # fresh settings + strict mapping
```

The indexer (#16) creates a new physical index, loads validated documents, then
atomically replaces the `tuik-resources` alias. Naming/definition helpers do not
touch aliases or make network calls. Alias changes belong to the indexer.

## Identity and contents

| Kind | ID | Parent |
| --- | --- | --- |
| dataset | `flow:<code>+V<version>` | none |
| bulletin | `press:<number>` | none |
| metadata | `press:<number>:metadata` | its own `press:<number>` |

Reject session handles, classification IDs, file IDs and kind/ID mismatches.
Table/PDF content documents remain #22. `related_ids` uses the same stable ID
scope, is deduplicated and excludes self-links. `series_id` is an optional series
grouping key, not a file URL. Unknown source fields are forbidden in both the
Pydantic model and OpenSearch (`dynamic: strict`).

`title`, optional `title_en`, `description` and `body` contain plain source text.
The crawler strips upstream HTML; source content remains data, not instructions.
`body` is bulletin HTML text or flattened metadata text, not downloaded file text.
`period` preserves the source period label without guessing observation dates.

`theme`, `subtheme`, `category_path`, `formats` and `related_ids` are arrays of
exact keyword values. A bulletin may belong to multiple categories; retain all
confirmed assignments rather than picking one arbitrarily. Category paths retain
source node identifiers. `downloadable`/`formats` describe advertised availability,
not verified downloads. Missing metadata stays null/empty.

Publication/update dates accept ISO calendar dates or timezone-aware timestamps;
bare epoch numbers, ambiguous date strings and naive timestamps are rejected.
Adapters must explicitly normalize a known source timezone before constructing
documents. Calendar date precision is preserved. `retrieved_at` identifies source
retrieval; `indexed_at` identifies document generation and is refreshed by #16
when actually writing a new index snapshot.

## Fields and analysis

The title has Turkish `title`, stem-free `title.ascii`, exact `title.keyword` and
prefix-only `title.suggest` subfields. Natural-language title/description/body use
the #13 index and search analyzers; English text has its own English analyzer.
Themes, kinds and relations remain keyword fields for term filters/facets.

The suggest subfield adds an isolated ASCII edge-ngram index analyzer (2–20
characters, preserving longer originals) and a plain ASCII search analyzer.
It does not alter the #13 chains or solve inflected ASCII queries. Autocomplete
routing and ranking remain #18. Synonym relevance defaults still require #20.

Source URLs are preserved in `_source` without indexing; dates use explicit
`strict_date_optional_time` formats. The default definition is single-shard,
zero-replica for the existing local development cluster. Production topology
requires a separate deployment decision.

## Verify

```bash
.venv/bin/pytest tests/test_index_documents.py -q
TUIK_OPENSEARCH_URL=http://localhost:9200 .venv/bin/pytest -m opensearch -q
```

Without `TUIK_OPENSEARCH_URL`, the real-cluster test is skipped. It owns a UUID
index, indexes every supported kind, checks synonym search plus exact theme/date
filters and rejects unknown fields, then deletes only that test index. A dedicated
OpenSearch CI job remains #19; normal offline tests do not require a server.
