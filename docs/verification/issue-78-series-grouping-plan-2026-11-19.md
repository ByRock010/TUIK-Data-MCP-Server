# Bulletin-series grouping: fixed evaluation plan (#78)

Date: 2026-11-19 (Europe/Istanbul). Recorded before implementation, retrieval or
grading of any grouped ranking.

## Problem

The production ranking (equal-weight hybrid with bare-abbreviation lexical routing,
#21/#79) fills most top-ten slots with further releases of series already shown.
On the #21 pool, with the reviewed grades and the series-collapsed metric of
[search-hybrid-series-collapsed-2026-11-05.json](search-hybrid-series-collapsed-2026-11-05.json),
a recomputation gives:

| System | Series-collapsed nDCG@10 | Repeated-series slots (48 queries) |
| --- | ---: | ---: |
| Multi-field + synonyms (v0.3.0 default) | 0.4575 | 252 |
| Hybrid RRF equal (#21) | 0.4526 | 328 |
| Hybrid + abbreviation routing (#79, current production) | 0.4657 | 330 |

The first two rows reproduce the archived diagnostic exactly; the third is new.

## Fixed candidate

**Series key.** Bulletins and bulletin metadata are grouped by their indexed
`series_id` (a bulletin and its metadata share it). Datasets, tables, PDF passages
and documents without a `series_id` are never grouped: each is its own group. No
index schema change or rebuild is needed; every v3 metadata index stores `series_id`.

**Grouping.** Grouped search fetches the first `GROUP_WINDOW = 200` documents of the
normal document ranking, keeps the first (best-ranked) document of each group as the
representative, and pages over the groups. A series therefore takes the position
of its best-ranked release. The representative is the best-matching release, not
the newest, so period-specific queries keep their release. Each grouped result
also reports the series ID, how many other window documents it folded in, how many
bulletin releases the series has in the index, and the series' newest release.

- Lexical requests (including bare abbreviations, which keep the #79 score/ID order)
  use the unchanged lexical query and its document order.
- Hybrid requests use the unchanged equal-weight RRF request with fusion depth and
  k-NN `k` raised from 50 to the 200-document window, so that one large series
  (up to 94 bulletin + metadata documents in the frozen corpus) cannot exhaust the
  window. OpenSearch's native `collapse` is **not** used: a disposable-index probe
  on OpenSearch 3.3.2 showed that it collapses each hybrid subquery before fusion,
  so a series whose best lexical and best semantic members differ (for example a
  bulletin and its metadata) receives split, lower fused scores.
- Highlights come from the existing per-ID lexical highlight request for the
  representatives only. `total` and the facets keep counting matching documents.
- Grouping is the default in the indexed `search_tuik` path; `group_series=false`
  returns every release separately, and a `series` filter lists one series'
  releases (ungrouped). Dataset-only searches are unaffected (datasets have no series).
- Grouped pages cover `offset + limit ≤ 50` groups; a request beyond that is
  served ungrouped with a warning. When fewer groups than requested exist in the
  window, the shorter page is returned with a warning.

No other ranking, weight, analyzer, routing or depth change is part of the candidate.

## Evaluation

Collect fresh rankings from the frozen 3,424-document metadata corpus
([search-full-corpus-2026-11-05.jsonl.gz](search-full-corpus-2026-11-05.jsonl.gz))
in a disposable OpenSearch index with `intfloat/multilingual-e5-small` vectors,
through the production `OpenSearchBackend.search` (unfiltered, limit 10, offset 0):

1. `production_grouped` — the candidate (grouping on).
2. `production_ungrouped` — grouping off; a reproducibility check against the
   archived #79 routed ranking. Differences are reported, not hidden.

Pool both runs into the #21/#79 pool (48 queries, 1,689 pairs) with all earlier
runs. Existing pairs keep their source-reviewed grades unchanged. New pairs are
graded under [ADR 0004](../adr/0004-ai-relevance-evaluation.md) with the inherited
query intents and rubric by Anthropic Claude (Opus 5.5), the same dependent AI
assessor that graded the #21 additions, followed by a live MCP source collection
over the whole enlarged pool and a sequential source review. As in #21, a
further release of a series already graded for the same query inherits that
series' grade unless its own text changes the scope; new series are graded fresh.

**Rubric decision.** Relevance grades stay per document: a historical release is as
relevant as the series it belongs to. For the default search, releases of one
series are *not* separate answers. Series-collapsed nDCG@10 (only the first
document of each series keeps its gain; the ideal list takes each series' best
grade once) is the primary diversity metric; plain nDCG@10 is reported beside it.

## Acceptance (fixed before scoring)

All on the enlarged pool, reviewed grades, `score-ai --require-reviewed`:

1. Series-collapsed nDCG@10 of `production_grouped` is higher than that of the
   ungrouped production ranking (#79 routed archive) and of every #21 system.
2. Plain nDCG@10 of `production_grouped` is at least the plain nDCG@10 of the
   v0.3.0 lexical default (`multi_field_synonyms`) on the same pool. The allowed
   drop is therefore the hybrid gain itself (0.8613 − 0.6944 = 0.1669 on the
   current pool): grouping may give back hybrid's repeat-driven gain, but not
   fall below the pre-hybrid default.
3. Repeated-series slots of `production_grouped` are 0 (well below 252); a nonzero
   count is a defect.
4. Zero unjudged returned slots.

If 1–4 pass, grouping is the default. Otherwise it ships as an option with
`group_series` defaulting to false and the failed criterion is reported. The
window, depth and thresholds are not tuned after examining scores. Publish MRR@10,
pooled Recall@10, per-category metrics and per-query series-collapsed changes,
including regressions.

## Limits

The AI grades are dependent and the assessor implemented the candidate; there is
no independent human validation. The 48-query suite is historical, not held out.
Freshness of the newest-release pointer is bounded by the frozen snapshot.
Dataset-filtered hybrid ranking (#80) is separate work.
