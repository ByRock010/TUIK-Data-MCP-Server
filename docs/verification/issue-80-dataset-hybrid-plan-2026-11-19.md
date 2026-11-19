# Dataset-filtered hybrid ranking: fixed evaluation plan (#80)

Date: 2026-11-19 (Europe/Istanbul). Recorded before retrieval or grading of any
dataset-filtered ranking.

## Question

`search_tuik(kind="dataset")` and `search_tuik_datasets` stay lexical because #21
measured mixed-kind search only (ADR 0005, decision 7). Should dataset-filtered
searches use hybrid ranking?

## Systems (fixed)

All systems search the frozen 3,424-document metadata corpus with the dataset
filter inside the query (`SearchFilters(kinds=["dataset"])`, limit 10, offset 0),
in one disposable index with `intfloat/multilingual-e5-small` vectors:

1. `dataset_lexical` — the production synonym-aware lexical request with the
   dataset filter (the current behaviour).
2. `dataset_hybrid_rrf_lexical_weighted` — weighted RRF 0.7/0.3, filter in both
   subqueries.
3. `dataset_hybrid_rrf_equal` — equal-weight RRF, filter in both subqueries.
4. `dataset_hybrid_routed` — what production would serve if `dataset` left
   `LEXICAL_ONLY_KINDS`: equal-weight hybrid with the #79 bare-abbreviation lexical
   routing (a deterministic combination of systems 1 and 3 per query).

All four come from the same index build, because hybrid top tens are not stable
across index builds (#78 finding). Hybrid systems use the production request and
execution path (`build_hybrid_query`, `OpenSearchBackend.execute`), including the
lexical-evidence guard; the lexical system is the served `OpenSearchBackend.search`.

## Queries and grading

Reuse the 48 suite queries unchanged. Pool every system's top ten into the #21/#79
pool (1,689 reviewed pairs), independently of the unmerged #78 work. Existing pairs
keep their source-reviewed grades. A new pair that the #78 review (same assessor)
already graded keeps that reviewed grade, recorded as such; other new pairs are graded under
ADR 0004 by Anthropic Claude (Opus 5.5) with the inherited query intents and rubric,
followed by a live MCP source collection over the enlarged pool and a sequential
review. Score each system with the grades restricted to dataset documents (the
dataset-filtered ideal), using `score-ai --require-reviewed`.

## Decision rule (fixed before scoring)

Adopt hybrid ranking for dataset-filtered searches (remove `dataset` from
`LEXICAL_ONLY_KINDS` and update ADR 0005) only if `dataset_hybrid_routed` has a
higher dataset-filtered nDCG@10 than `dataset_lexical`, and its MRR@10 is not
lower by more than 0.05. Otherwise datasets stay lexical. The 0.7/0.3 setting is
reported but cannot be adopted for datasets alone: production uses one weighting.
No setting is tuned after examining scores. Report MRR@10, pooled Recall@10 and
per-category metrics, including regressions.

## Limits

Dependent AI grades, no human validation, historical non-held-out queries.
Dataset documents carry short titles and few descriptions, so grades rest on
titles, themes and live catalog metadata.
