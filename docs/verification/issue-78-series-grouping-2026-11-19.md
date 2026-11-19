# Bulletin-series grouping (#78)

Date: 2026-11-19 (Europe/Istanbul).

## Result

Grouping removes every repeated series and raises **series-collapsed nDCG@10 from
0.4251 to 0.7407**, higher than every other system. Plain nDCG@10 falls to
**0.5529**, below the v0.3.0 lexical default (0.6918). This fails acceptance
criterion 2 of the [fixed plan](issue-78-series-grouping-plan-2026-11-19.md).
Under that plan, grouping therefore ships as an **option**: `search_tuik(...,
group_series=true)`. The default ranking is unchanged.

All numbers use the enlarged pool (48 queries, **1,913 reviewed pairs**) and
`score-ai --require-reviewed`. Every returned slot is judged.

| System | Plain nDCG@10 | Series-collapsed nDCG@10 | Repeated-series slots | MRR@10 | Pooled Recall@10 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Portal (live, broader corpus) | 0.7736 | 0.4517 | 263 | 0.9362 | 0.2884 |
| Multi-field + synonyms (v0.3.0 default) | 0.6918 | 0.4293 | 252 | 0.8796 | 0.3078 |
| Hybrid RRF equal (#21) | 0.8478 | 0.4190 | 328 | 0.9896 | 0.3316 |
| Hybrid + abbreviation routing (#79 archive) | 0.8576 | 0.4305 | 330 | 1.0000 | 0.3359 |
| `production_ungrouped` (fresh index) | 0.8560 | 0.4251 | 330 | 1.0000 | 0.3349 |
| **`production_grouped`** | 0.5529 | **0.7407** | **0** | **1.0000** | 0.2418 |

| Criterion (fixed before scoring) | Outcome |
| --- | --- |
| 1. Series-collapsed nDCG@10 above the #79 archive and every #21 system | **Pass** (0.7407 vs at most 0.4517) |
| 2. Plain nDCG@10 at least the v0.3.0 lexical default | **Fail** (0.5529 < 0.6918) |
| 3. Zero repeated-series slots | **Pass** (0) |
| 4. Zero unjudged returned slots | **Pass** |

Earlier #21 values are slightly lower here than in the #21/#79 reports. The
grouped lists found more relevant series, so each query's ideal DCG rose.

## What the trade-off looks like

Series-collapsed nDCG@10 improves for 46 queries and is unchanged for 2; no query
gets worse. MRR@10 stays 1.0: every first result is still relevant. Plain nDCG
falls most where the ungrouped list held ten releases of one directly relevant series:

| Query | Series-collapsed (ungrouped → grouped) | Plain (ungrouped → grouped) |
| --- | --- | --- |
| Sanayi Üretim Endeksi | 0.365 → 0.951 | 1.000 → 0.573 |
| Konut Satış İstatistikleri | 0.342 → 0.957 | 1.000 → 0.615 |
| ilk el konut satışları | 0.320 → 0.915 | 1.000 → 0.629 |
| konut satiş | 0.331 → 0.370 | 1.000 → 0.246 |
| Bursa sanayi | 0.220 → 1.000 | 1.000 → 1.000 |

The two metrics measure different things. Under the inherited rubric, every
historical release keeps its series' grade. Plain nDCG therefore rewards ten
monthly releases of the same bulletin. Grouping replaces nine of them with other
series, which are often less relevant. Whether an assistant wants one series or
ten releases depends on the task. That is why grouping is offered per request rather than imposed.

By category, series-collapsed nDCG@10 changes from ungrouped to grouped as follows:

- exact 0.346 → 0.835
- abbreviation 0.496 → 0.870
- synonym 0.454 → 0.707
- inflection 0.402 → 0.727
- ASCII 0.341 → 0.528
- typo 0.401 → 0.612
- multi-concept 0.459 → 0.812
- regional 0.502 → 0.834

Two abbreviation queries (`ÜFE`, `Yİ-ÜFE`) returned 7 and 8 groups: their lexical
match sets hold fewer distinct series than the page size. The response says so.

## Behaviour shipped

- `group_series=true` folds bulletins and their metadata by `series_id` into the
  best-ranked release within the first 200 ranked documents. Hybrid requests fuse
  over that depth. Each grouped result reports:
  - `series.collapsed`: the documents folded into it;
  - `series.releases`: the bulletin releases of the series in the index;
  - `series.latest`: the series' newest indexed release.
- Datasets, file content and `series=` listings are never grouped.
- `total` and the facets keep counting documents.
- Grouped pages cover the first 50 groups; deeper pages list releases separately,
  with a warning.
- All indexed bulletin/metadata results now carry `series_id`. `series=<id>` lists
  one series' releases.
- OpenSearch's native `collapse` was rejected. On OpenSearch 3.3.2 it collapses each
  hybrid subquery before fusion. That split a series' lexical and semantic
  contributions across different members in a disposable-index probe.

## Finding: hybrid rankings are not reproducible across index builds

`production_ungrouped` uses the frozen corpus and identical document vectors
(SHA-256 `9faae809…`). Its query vectors come from the same e5-small embedder,
and it uses the #79 request. Even so, it matches the archived #79 top ten for
only **9 of 48** queries.

Four further builds of the same corpus and vectors also disagreed with each other:
two plain bulk loads matched each other on 8 of 48 queries. Two builds
force-merged to one segment matched on 9. The likely cause is approximate Lucene
HNSW search over graphs that differ per build. The effect on scores is small
(plain nDCG@10 0.8560 vs 0.8576). Rankings themselves, and any comparison
between separately built indices, are not exactly reproducible. For that reason
this experiment compares grouped and ungrouped rankings from the **same** index
build. Making k-NN deterministic is separate work.

## Grading

The 224 new pairs (112 new documents) were graded by Anthropic Claude (Opus 5.5)
under [ADR 0004](../adr/0004-ai-relevance-evaluation.md). The first pass gave
81 grade-2, 54 grade-1 and 89 grade-0 labels. It used the inherited query
intents and the grades of the same document and series in every query.

A live MCP collection then ran 919 real stdio calls: 48 portal replays, 855
resource resolutions and bounded table probes. The second pass checked all 860
pooled documents:

- 854 checked live; 6 kept archival evidence. These are five evaluation-only
  portal identities and `DF_SATIS_SEKLI_DURUMU_ILCE`, which has left the public
  catalog since the snapshot.
- One grade was revised. *İstatistiklerle Gençlik, 2022* states young women's
  unemployment rate, so `genç kadın işsizlik oranı` was raised from 1 to 2. The
  cited frozen passage also occurs in the live response.
- Seven pooled titles differ from their live titles: portal-shortened metadata
  titles and one spacing change. Grades use the frozen records.

The same assessor implemented grouping and saw which system returned each
candidate, but no scores before the review was complete. Grades are dependent
and AI-only. There is no human validation, and κ is not applicable.

## Evidence and reproduction

| File | Contents |
| --- | --- |
| [Fixed plan](issue-78-series-grouping-plan-2026-11-19.md) | Candidate, window and thresholds, before implementation |
| [search-grouping-production-2026-11-19.json.gz](search-grouping-production-2026-11-19.json.gz) | Grouped/ungrouped rankings through `OpenSearchBackend.search`, routing, series details, embedding digest |
| [search-grouping-pool-2026-11-19.json.gz](search-grouping-pool-2026-11-19.json.gz) | Pool 1,689 → 1,913 pairs |
| [search-grouping-ai-assessment-2026-11-19.json](search-grouping-ai-assessment-2026-11-19.json) | First pass |
| [search-grouping-ai-live-evidence-2026-11-19.json.gz](search-grouping-ai-live-evidence-2026-11-19.json.gz) | Live MCP source evidence |
| [search-grouping-ai-reviewed-assessment-2026-11-19.json](search-grouping-ai-reviewed-assessment-2026-11-19.json) | Second pass, source checks, revision |
| [search-grouping-ai-reviewed-scores-2026-11-19.json](search-grouping-ai-reviewed-scores-2026-11-19.json) | Reviewed scores with series metrics |

Score the retained evidence offline:

```bash
V=docs/verification
.venv/bin/python scripts/evaluate_search.py score-ai \
  --suite $V/search-hybrid-pool-2026-11-05.json.gz \
  --runs $V/search-full-baseline-2026-11-05.json.gz \
    $V/search-full-variants-2026-11-05.json.gz \
    $V/search-hybrid-variants-2026-11-05.json.gz \
    $V/search-abbreviation-routing-2026-11-19.json.gz \
    $V/search-grouping-production-2026-11-19.json.gz \
  --assessment $V/search-grouping-ai-assessment-2026-11-19.json \
  --review $V/search-grouping-ai-reviewed-assessment-2026-11-19.json \
  --evidence $V/search-grouping-ai-live-evidence-2026-11-19.json.gz \
  --documents $V/search-full-corpus-2026-11-05.jsonl.gz \
  --require-reviewed --output /tmp/grouping-scores.json
```

Recollect the rankings with a disposable cluster and the `semantic` extra. No
TÜİK requests are made, but rankings will differ slightly (see above):

```bash
.venv/bin/python scripts/evaluate_search.py collect-production \
  --suite docs/verification/search-hybrid-pool-2026-11-05.json.gz \
  --documents docs/verification/search-full-corpus-2026-11-05.jsonl.gz \
  --url http://127.0.0.1:9200 --output /tmp/grouping-production.json.gz
```

The rankings were collected at commit `a749312` (OpenSearch 3.3.2, fastembed
0.9.1, 96 s for 3,424 document embeddings on an Apple M4). The default was then
switched to opt-in. That changes only the MCP parameter default, not the measured
code paths.

## Verification

- 871 offline and real-OpenSearch tests pass (`-m "not live"`, local OpenSearch 3.3.2).
- Grouping tests cover:
  - series keys, regrouping, paging and window warnings;
  - follow-up failure, hybrid depth and abbreviation order;
  - fallback backends and the stdio MCP schema/results;
  - a real-cluster grouping check and a real-cluster collector run.
- `score-ai` without `--documents` reproduces the #79 score file byte-for-byte.
- Two opt-in live TÜİK tests fail on `develop`'s drift, independently of this
  change. One expects the tool list from before `chart_tuik_data` (#72); the
  other is a changed live category path.
