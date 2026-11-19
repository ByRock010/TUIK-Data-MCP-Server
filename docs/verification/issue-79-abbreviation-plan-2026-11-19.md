# Abbreviation routing: fixed evaluation plan (#79)

Date: 2026-11-19 (Europe/Istanbul). Recorded before implementation or scoring.

## Fixed candidate

Use synonym-aware lexical ranking for a query consisting entirely of one of the
curated abbreviations in `search/synonyms.txt`: TÜFE, ÜFE, Yİ-ÜFE, GSYH, HİA,
ADNKS or NACE. Match Turkish/ASCII spellings case-insensitively, trim/collapse
whitespace, and accept `Yİ ÜFE` as well as `Yİ-ÜFE`. Do not classify arbitrary
uppercase words, substrings, abbreviation-plus-context queries or full expansions.

Use score descending, then stable ID ascending for these lexical requests, matching
the frozen lexical collector's tie ordering. Keep the original query text, filters,
facets and pagination. Bypass model discovery and embedding for these queries;
report `ranking: lexical` with a reason when semantic search would be considered.
Every other query retains equal-weight hybrid ranking and existing scope/fallback
rules. No embedding expansion, weight tuning or per-query exception is selected.

## Evaluation and acceptance

Replay this deterministic routing over the archived #21 rankings, using the
`multi_field_synonyms` top tens for recognized abbreviations and
`hybrid_rrf_equal` top tens otherwise. Verify matching queries, corpus fingerprints,
corpus IDs, analysis settings and ranking-defining request fields before reuse.
This is an archived-routing replay, not fresh OpenSearch retrieval, new embeddings
or new live source verification. Changes to highlights, excluded response fields
and the zero-lexical-match guard do not change these archived nonempty rankings.

Use the existing 48-query / 1,689-pair pool, first assessment, source review and
evidence unchanged. Add the routed run alongside all original runs to
`score-ai --require-reviewed`; any new pair, incomplete archive or changed ranking
configuration must fail instead of inventing a grade. If a new retrieval experiment
is required, grade new pairs under ADR 0004 before claiming acceptance.

Pass only if abbreviation nDCG@10 is at least 0.911 and overall nDCG@10 is at least
0.8516, with zero unjudged returned slots. Publish MRR, pooled recall, category
metrics and per-abbreviation changes, including regressions. Do not tune the
candidate after examining the scores. The inherited AI grades are dependent,
historical evidence, not independent human validation or a held-out experiment.

Check actual backend request routing, normalization boundaries, optional-model
failure, filters, offsets, result provenance and MCP stdio responses separately.
Run repository lint/format checks, offline Python/Node tests and GitHub CI.
