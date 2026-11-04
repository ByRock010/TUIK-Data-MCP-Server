# Source-checked sequential AI baseline — 2026-11-04

The project owner asked Codex to perform the relevance review instead of requiring
two people to grade the pool. [ADR 0004](../adr/0004-ai-relevance-evaluation.md)
records that method change for project acceptance. This report completes the
AI baseline work for #11 under that disclosed method; normal PR review/merge is
still required. #20's full-corpus experiment and v0.3.0 publication remain pending.

There is **one AI assessor in two dependent passes**, not two human judges.
Independent human validation has not occurred. κ is **not applicable** and remains
null. Human judge slots in the original fixture remain blank.

## Actual execution

`scripts/collect_ai_review.py` started the actual local stdio MCP server, package
**0.2.0**, Python **3.14.0**, based on branch revision `e43ca0e`. The collector's
SHA-256 is stored in the evidence and checked against its committed source.
Requests were paced **2 seconds** apart; file downloads retained the default
**6-second** interval. Collection ran from **16:48:49 to 16:55:00 Istanbul**
(13:48:49–13:55:00 UTC).

- **48/48** frozen queries were replayed through `search_tuik(live=true)` with
  limit 10. Every response used the portal backend and succeeded.
- **346** reusable resource IDs were submitted to `get_tuik_resource`;
  **345** resolved. `flow:DF_MUZE_ESER_SAY+V1.0` returned `not_found`.
- **5** evaluation-only `title:` keys were explicitly skipped as tool inputs.
  These and the removed flow use the archived pool metadata in the assessment.
  Access failure alone was not treated as evidence of irrelevance.
- **8** table resources received both `inspect_tuik_data` and
  `get_tuik_data(limit=2)` probes: **6** yielded structured rows, **1** exceeded
  the existing download size bound, **1** had no structured export. Limits were
  not raised to obtain a passing result.
- This is **410 actual MCP calls**. A further **6** calls verified three exact
  selections after profiling their observed columns/values, for **416 total**.

The [unaltered collection JSON, losslessly compressed](search-ai-live-evidence-2026-11-04.json.gz)
contains complete MCP responses, official URLs, timestamps, error responses,
profile values, data snapshots and tool discovery. `status: partial` is intentional:
it preserves the skipped evaluation-only IDs and real upstream limitations.
It must not be rewritten to claim every resource/download succeeded. Decompress
with `gzip -dk` to inspect the JSON; the scorer reads `.json.gz` directly.

The [supplemental scope-check JSON](search-ai-scope-checks-2026-11-04.json)
retains the small harness source and its hash, all three profiles, exact filter
arguments, bounded rows and source provenance. Each inspect/data pair had the
**same snapshot ID**, and every returned row matched every requested filter.

| Selection verified through MCP | Matching rows | Scope established |
| --- | ---: | --- |
| Monthly basic labour indicators | 153 | Women, ages 15–24, unemployment rate, seasonally adjusted |
| Provincial ADNKS population | 19 | Istanbul, total settlement population, both sexes |
| National housing sales | 178 | First-hand housing, count rather than share or workplace sales |

This does not mean all 351 documents' exported contents were downloaded. Most
source checks use official metadata. The large provincial housing CSV remained
unread within the configured bound, and the provincial birth-rate flow did not
offer a structured export. Their conceptual relevance and operational availability
are separate observations.

## Second-pass judgments

All **917 query–document pairs** across **48 queries / 351 documents** were
revisited in identity order with the original grades visible. This is a source
review by the same assessor, not a blind or independent replication.

- [First assessment](search-ai-assessment-2026-11-04.json) and
  [first metric report](search-ai-scores-2026-11-04.json) remain unchanged.
- [Complete second assessment](search-ai-reviewed-assessment-2026-11-04.json)
  includes **345 live metadata references**, **6 explained archival fallbacks**,
  all final grades, per-query rationale and **11** before/after/reason records.
- [Reviewed metric report](search-ai-reviewed-scores-2026-11-04.json) has
  `status: ai_reviewed`, `review_complete: true`, zero human pairs and an
  unsatisfied standalone full-release flag.

All 11 revisions are **1 → 2** after reading previously omitted scope passages:

| Query | Revised candidates | Reason |
| --- | ---: | --- |
| `İstanbul'un nüfusu` | 1 | Full 2024 ADNKS bulletin explicitly gives Istanbul population |
| `genç kadın işsizlik oranı` | 5 | Full labour bulletins explicitly give unemployment for women aged 15–24 |
| `yaş grubuna göre nüfus` | 4 | Full ADNKS bulletins contain the population age-group distribution |
| `İzmir konut satışları` | 1 | Full December 2025 housing bulletin explicitly gives the Izmir sales total |

The final distribution is **394 grade 2 / 325 grade 1 / 198 grade 0**.
The other **906** grades were retained. This revision count is a self-correction
diagnostic, not inter-rater agreement. A source's occurrence of a province name
alone was insufficient: the relevant statistic and subgroup had to be explicit.

## Reviewed metrics on frozen rankings

| System | nDCG@10 | MRR@10 | Pooled Recall@10 |
| --- | ---: | ---: | ---: |
| Historical portal, all public types | 0.8555 | 0.9377 | 0.6300 |
| Historical literal, 342 downloadable flows | 0.1661 | 0.2552 | 0.1667 |
| Built-in Turkish, same 342 flows | 0.6357 | 0.7672 | 0.6818 |
| Multi-field, same 342 flows | 0.6193 | 0.7573 | 0.6676 |
| Multi-field + synonyms, same 342 flows | 0.7332 | 0.8198 | 0.7985 |

The portal's nDCG@10 changed from **0.8479 to 0.8555**. MRR/pooled recall did not
change because grades 1 and 2 both count as relevant for those metrics. The flow
variants' grades did not change, so their numbers remain unchanged. The JSON
retains metrics overall, by query category and by resource type, with zero
unjudged share in this AI-assessed pool.

Portal and flow-only scores are **not a same-corpus comparison**. OpenSearch still
uses the existing historical **342-flow pilot**, omitting the full bulletin and
metadata corpus. Five flow-corpus queries have no positively graded eligible
candidate; macro averages include those zero rows. Pooled recall is not exhaustive
corpus recall. The synonym variant's pilot lead does not establish a
final full-corpus default; the existing analyzer configuration was not changed.

The live replay retained identical top-ten ordering for **21/48** queries; the
other queries showed ordering/selection changes, including **22** newly returned
query–document slots relative to the historical portal top ten. These new live
rankings are diagnostic, **not** substituted into the scored historical experiment
or silently declared graded. A fresh benchmark would require repooling/review.

## Reproduce the acceptance check

```bash
.venv/bin/python scripts/evaluate_search.py score-ai \
  --runs docs/verification/search-baseline-2026-10-26.json \
    docs/verification/search-variants-dataflows-2026-11-03.json \
  --assessment docs/verification/search-ai-assessment-2026-11-04.json \
  --review docs/verification/search-ai-reviewed-assessment-2026-11-04.json \
  --evidence docs/verification/search-ai-live-evidence-2026-11-04.json.gz \
  --require-reviewed --output /tmp/reviewed-ai-scores.json
```

This exits **0** only with the complete, source-bound review. Omitting the review
while retaining `--require-reviewed` writes a preliminary report and exits **1**.
The optional human scorer still reports pending labels; those labels have not
been populated or presented as a human study.

## Verification and next work

Offline regression coverage checks first-pass preservation, exact pool/source
binding, wrong resource references, false human/independence claims, missing
query/source checks, undocumented revisions and strict AI completion. The
committed live evidence, supplemental snapshots and score JSON reproduce without
network access. The recorder's offline tests exercise live-vs-frozen separation,
evaluation-only skips, probe capture, atomic checkpoints and bounded failure stops.
Python **3.14.0** offline results: **674 passed, 21 skipped, 6 deselected** in
22.23 seconds. Ruff lint/format and all **28 Node automation tests** passed.

The local Docker daemon/OpenSearch cluster was unavailable; no fresh cluster
experiment was performed. #20 still needs the full frozen metadata crawl and all
three variant collections, followed by AI assessment/source review of the newly
pooled candidates and the default-configuration decision. #29 then performs final
index/MCP acceptance and the ordinary reviewed release procedure.
