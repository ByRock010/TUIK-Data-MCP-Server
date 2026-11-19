# Bare-abbreviation ranking recovery (#79)

Date: 2026-11-19 (Europe/Istanbul).

## Result

The fixed candidate passes both #79 thresholds on the unchanged #21 pool:
**abbreviation nDCG@10 0.911034** (required ≥0.911) and
**overall nDCG@10 0.861341** (required ≥0.8516). All 480 returned slots are judged;
the pool remains **48 queries / 1,689 pairs**, with no new or changed grade.

| System | Overall nDCG@10 | Abbreviation nDCG@10 | Overall MRR@10 | Pooled Recall@10 |
| --- | ---: | ---: | ---: | ---: |
| Synonym-aware lexical | 0.694428 | 0.911034 | 0.879563 | 0.338670 |
| Original equal-weight hybrid | 0.851599 | 0.833098 | 0.989583 | 0.365338 |
| Hybrid with bare-abbreviation lexical routing | **0.861341** | **0.911034** | **1.000000** | **0.370450** |

This is an **archived-routing replay**, not newly collected OpenSearch rankings.
The production planner selects between the exact archived lexical and hybrid
requests; the replay verifies corpus fingerprints and IDs, engine, analysis,
ranking mappings, query clauses, ordering, depth and fusion parameters. A
placeholder vector exercises request planning only; it is never used for retrieval.
Existing source-reviewed AI grades are reused through `score-ai --require-reviewed`.

## Why this rule

The lexical analyzers know curated TÜİK abbreviations through `synonyms.txt`;
multilingual-e5-small does not reliably associate those acronyms with their
statistical meaning. Equal-weight fusion previously made an irrelevant energy
export index the first HİA result. The rule, spelling normalization, tie ordering,
evaluation method and thresholds were fixed in
[plan commit d90ac3d](https://github.com/ByRock010/TUIK-Data-MCP-Server/commit/d90ac3d)
before implementation or scoring. No query-specific exception or weight tuning
was added after the scores were inspected.

Whole-query TÜFE, ÜFE, Yİ-ÜFE, GSYH, HİA, ADNKS and NACE now use the existing
synonym-aware lexical request. Recognition accepts Turkish/ASCII case variants,
normalized whitespace and `Yİ ÜFE`. Original query text and filters are preserved.
Stable score-descending / ID-ascending ordering matches the lexical archive and
keeps pagination consistent. Planning bypasses vector-model discovery and model
loading. Results report `ranking: lexical` with a reason when semantic ranking
would otherwise be considered. Unknown uppercase words, full expansions and
contextual queries such as `TÜFE 2026` retain the existing hybrid scope.

## Individual abbreviation outcomes

| Query | Original hybrid nDCG@10 | Routed nDCG@10 | Original → routed MRR@10 |
| --- | ---: | ---: | ---: |
| TÜFE | **0.8533** | 0.6873 | 1.0 → 1.0 |
| ÜFE | 1.0000 | 1.0000 | 1.0 → 1.0 |
| Yİ-ÜFE | 1.0000 | 1.0000 | 1.0 → 1.0 |
| GSYH | 1.0000 | 1.0000 | 1.0 → 1.0 |
| HİA | 0.3564 | **0.7789** | 0.5 → **1.0** |
| ADNKS | 0.7889 | **1.0000** | 1.0 → 1.0 |

The other **42** result lists and their category metrics are unchanged. TÜFE's
individual regression is accepted under the pre-stated aggregate thresholds;
there is no special case to retain its hybrid ranking. HİA's new first result is
the grade-2 `İşgücü Piyasasında Gençler, 2024`; ADNKS's is the grade-2
`Adrese Dayalı Nüfus Kayıt Sistemi Sonuçları, 2023`. Historical relevance grades
do not claim these are the newest available releases.

## Evidence and reproduction

Runtime/evaluation implementation: commit `b4c5ec3`. The generated routing file
records the collector hash, original input-file hashes, corpus identity, source
system and warning for every query. Original evidence files are unchanged.

| Artifact | Contents |
| --- | --- |
| [Fixed plan](issue-79-abbreviation-plan-2026-11-19.md) | Candidate and acceptance criteria before implementation/scoring |
| [Routing replay](search-abbreviation-routing-2026-11-19.json.gz) | Exact reused rankings/scores and per-query production routing |
| [Reviewed scores](search-abbreviation-scores-2026-11-19.json) | All systems, categories, resource types, unchanged review provenance |
| [Original #21 report](issue-21-hybrid-search-2026-11-05.md) | Corpus, embedding configuration, rankings, assessments and source evidence |

From the repository root, with the development environment installed:

```bash
.venv/bin/python scripts/evaluate_search.py route-abbreviations \
  --suite docs/verification/search-hybrid-pool-2026-11-05.json.gz \
  --runs docs/verification/search-full-variants-2026-11-05.json.gz \
    docs/verification/search-hybrid-variants-2026-11-05.json.gz \
  --output /tmp/abbreviation-routing.json.gz

.venv/bin/python scripts/evaluate_search.py score-ai \
  --suite docs/verification/search-hybrid-pool-2026-11-05.json.gz \
  --runs docs/verification/search-full-baseline-2026-11-05.json.gz \
    docs/verification/search-full-variants-2026-11-05.json.gz \
    docs/verification/search-hybrid-variants-2026-11-05.json.gz \
    /tmp/abbreviation-routing.json.gz \
  --assessment docs/verification/search-hybrid-ai-assessment-2026-11-05.json \
  --review docs/verification/search-hybrid-ai-reviewed-assessment-2026-11-05.json \
  --evidence docs/verification/search-hybrid-ai-live-evidence-2026-11-05.json.gz \
  --require-reviewed --output /tmp/abbreviation-scores.json

cmp /tmp/abbreviation-scores.json \
  docs/verification/search-abbreviation-scores-2026-11-19.json
```

Routing timestamps/revision/paths vary on recollection; scores reproduce exactly.
The command rejects incompatible corpus, analysis, mapping, model or ranking
requests rather than presenting them as measured. The existing review gate rejects
new pooled pairs; they would need new ADR 0004 grading and source review.

## Verification and limits

- The 15 abbreviation regression cases fail before the runtime fix and pass
  afterwards. Cases include decomposed Unicode, Turkish/ASCII uppercase/lowercase,
  whitespace, unchanged filters and offsets, an unavailable model and preserved
  response provenance/facets. Other-query tests confirm unchanged embedding input.
- Real MCP stdio verifies the lexical ranking, explicit warning, results and
  provenance with a deliberately unavailable embedder. The existing real-cluster
  hybrid integration test also checks HİA synonym retrieval on a disposable index.
- The full offline Python suite passes: **820 passed, 22 skipped, 6 deselected**.
  Skips cover optional external integrations; live TÜİK tests remain opt-in.
  Ruff lint/format, `git diff --check` and all **28 Node tests** pass.
- Local Docker was not running; the real OpenSearch integration check runs in
  GitHub CI. No TÜİK adapter changed, so a new live portal run is not required.

The inherited grades follow [ADR 0004](../adr/0004-ai-relevance-evaluation.md):
dependent AI assessment and sequential source review, with no independent human
validation. No new embeddings or live source review occurred here. This known
historical pool is not held out; generalization to unseen queries is unmeasured.
NACE and spelling variants have routing tests but are outside the six-query
abbreviation relevance set. Freshness, repeated-series diversity (#78) and
dataset-filtered hybrid quality (#80) remain separate work.
