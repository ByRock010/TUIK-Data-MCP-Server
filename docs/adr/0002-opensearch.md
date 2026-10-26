# ADR 0002: Optional derived OpenSearch index for Turkish retrieval

Status: accepted. Date: 2026-10-26. Issue: #13. Related: #11–#20, #24, #25.

## Context

The current service searches public TÜİK sources live. Dataset discovery uses
Turkish case folding and substring matching over dataflows; it cannot cover ASCII
typing, Turkish suffixes and abbreviations consistently. The advisor requested
advanced Turkish search. Existing parsers, source adapters and stable resource IDs
remain the source of truth; the index must be rebuildable and optional.

## Decision

Use OpenSearch for the v0.3.0 lexical search track, with the existing Compose pin
3.3.2 and the [field/analyzer design](../SEARCH_DESIGN.md). OpenSearch is
[Apache-2.0 licensed](https://raw.githubusercontent.com/opensearch-project/OpenSearch/3.3.2/LICENSE.txt),
has a [built-in Turkish analyzer](https://docs.opensearch.org/latest/analyzers/language-analyzers/turkish/),
and supports a later [hybrid-search pipeline](https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/index/).
These capabilities fit both lexical experiments and the later stretch milestone;
hybrid search itself is not implemented or selected here.

Index only stable dataflow, bulletin and bulletin-metadata IDs initially (#14).
Use a versioned physical index behind the `tuik-resources` alias. The crawler
produces a shared `IndexDocument` independently of OpenSearch; a full index build
finishes before atomic alias replacement. The planned indexer maintains crawl
state in a local file. This ADR specifies that architecture, not an existing
crawler/indexer implementation (#15/#16).

Keep OpenSearch behind `SearchBackend` (#17), not public raw cluster operations.
Without configuration or on a backend outage, dataset search uses the literal
dataflow corpus; general/bulletin search uses the live portal. Report the fallback
explicitly. No OpenSearch client import or connection is required by the base MCP
server. Tool integration and protocol tests remain #18/#19.

Do not introduce PostgreSQL as a canonical store. The indexed catalog is derived
from official sources and can be rebuilt; no product requirement currently needs
independent transactional ownership, joins across user data or a second durable
catalog. A local crawl-state file is sufficient for the planned incremental
checkpoint. Revisit a database if concurrent indexers or audit/history requirements
make a single local state file insufficient.

## Alternatives considered

| Alternative | Assessment for this project |
| --- | --- |
| SQLite FTS5 | Low operational cost, useful local full-text search. Its documented built-in tokenizers include unicode61, ASCII, Porter and trigram; the requested Turkish chain and graph-synonym experiments would require additional custom work. Keep it viable if deployment simplicity becomes the dominant requirement. [FTS5 documentation](https://www.sqlite.org/fts5.html) |
| Meilisearch | Convenient typo tolerance, synonyms and ranking controls. We prefer explicit Lucene token-chain and graph-position inspection for this thesis's controlled Turkish experiments. This is a fit decision, not a claim that Meilisearch cannot search Turkish. [Typo tolerance documentation](https://www.meilisearch.com/docs/capabilities/full_text_search/relevancy/typo_tolerance_settings) |
| Elasticsearch | Similar Lucene analysis capabilities. OpenSearch matches the already provisioned optional stack and Apache-2.0 distribution without needing a different licensing/distribution decision. Elastic offers several source/distribution licensing options; do not characterize it as universally closed source. [Elastic licensing FAQ](https://www.elastic.co/pricing/faq/licensing) |
| Continue live literal search only | Retain as fallback and baseline, but it cannot meet the measured ASCII/inflection/abbreviation goals by itself. |

## Evidence and consequences

The [38-form token report](../verification/search-analysis-2026-10-26.json) was
executed against OpenSearch 3.3.2, not simulated in Python. It demonstrates original
Turkish stemming, a separate stem-free ASCII field and search-time synonym graphs.
It also preserves negative findings: `issizligin` remains unmatched across the tested
variants. The independent relevance study (#11/#20) must still choose query weights,
synonym defaults and any fuzziness policy; token examples do not prove quality gains.

An additional service consumes memory and requires rebuild/health operations.
The localhost development stack with disabled security stays local-only; production
deployment requires its own security/operations work. An absent/stale index must not
prevent live resource reading. Provenance, `indexed_at` and fallback warnings are
part of the future search contract. Table and PDF content indexing, vectors and
model runtimes remain v0.4.0 stretch work.
