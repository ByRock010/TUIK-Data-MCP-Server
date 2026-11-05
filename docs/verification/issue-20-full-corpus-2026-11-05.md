# Full-metadata relevance comparison — 2026-11-05

This candidate completes #20 under the owner-adopted [ADR 0004](../adr/0004-ai-relevance-evaluation.md): one AI assessment followed by a dependent source review. It does not claim independent human validation or publish v0.3.0. Runtime code is `7bd9420`, based on `develop` at `3ec5ae9`; PR #74 carries the implementation and evidence. All times below are Europe/Istanbul; raw provenance uses UTC.

## Frozen corpus and safe collection

The [full normalized JSONL](search-full-corpus-2026-11-05.jsonl.gz) contains **3,424 distinct documents**: **510 dataflows**, **1,494 non-archive bulletins**, and **1,420 nonempty bulletin-metadata documents**. The other 74 bulletins have no nonempty metadata document. All 1,494 bulletins have confirmed themes. Literal search uses the **341 downloadable CSV dataflows** from this same snapshot, rather than a later catalog. This is the complete v0.3 metadata scope, not all portal tables, publications, archives or file contents.

The source's standard listing returned 1,494 rows but only 1,465 unique IDs; `autoFilter:true` also returned 1,494 rows, with 1,468 unique IDs. The [300 actual retained responses](search-full-listing-2026-11-05.json.gz) have an **exact 1,494-ID union**, identical advertised totals and consistent dates. The crawler now optionally alternates these two official modes across bounded complete passes. It never accepts a smaller/larger union, changing totals/dates, repeated pages or an unbounded retry. Listing responses were replayed to avoid repeating 300 upstream requests; every detail response came from the official API with normal two-second pacing. The [completed crawl report](search-full-crawl-2026-11-05.json.gz) retains the exact harness, source-file hashes and cached/live capture counts.

The full crawl exposed two real schema/normalization defects: 33 repeated file-format entries exceeded the 32-unique-format budget, and `press:53842` had a previous-release URL without a display title. Formats are now deduplicated before that budget; the URL identity remains required while the unused title is optional. Already captured responses were reused with their original retrieval times. [Interrupted attempts](search-full-collection-errors-2026-11-05.json) did not advance state. State/output were committed only after all 1,494 details and 3,424 documents validated.

No corpus/ranking was inferred from returned-hit counts. [Variant collection](search-full-variants-2026-11-05.json.gz) ran 09:45:12–09:45:24 on OpenSearch **3.3.2**, producing 144 successful rankings. Every variant used the same corpus/configuration contract and all three UUID indices were deleted. The [current baseline](search-full-baseline-2026-11-05.json.gz) ran 09:45:12–09:46:53, producing 96 successful portal/literal rankings. Both collections have the same canonical corpus SHA-256. Field weights, fuzzy clause and synonym rules were frozen; no relevance tuning followed the judgments.

## Assessment and source review

The [enlarged pool](search-full-pool-2026-11-05.json.gz) preserves all 917 prior pairs and adds 473 new pairs: **1,390 pairs / 608 documents / 48 queries**. Old assessment files are unchanged. The [new first assessment](search-full-ai-assessment-2026-11-05.json) retains the earlier reviewed labels and explicitly grades every new identity-ordered candidate from its title/type/snippet. Its [first metric report](search-full-ai-first-scores-2026-11-05.json) remains separate.

The actual stdio MCP server then ran **667 calls**, 09:49:52–10:09:17: 48 exact portal query replays, 603 resource-resolution attempts and 16 bounded inspect/data probes. **602 resources resolved**. Five evaluation-only title identities were skipped as tool inputs; the removed `flow:DF_MUZE_ESER_SAY+V1.0` retained historical evidence. [Complete responses](search-full-ai-live-evidence-2026-11-05.json.gz) remain `status:partial`: provincial housing exceeds the existing download bound, and the provincial birth-rate flow has no structured export. Six of eight table probes returned structured rows. Limits were not raised to make checks pass.

The [second assessment](search-full-ai-reviewed-assessment-2026-11-05.json) revisits the whole pool and retains 602 live references plus 6 explained archival fallbacks. **55 grades change 1→2**, each preserving before/after/reason, a frozen body passage/hash and the exact live MCP call where the same passage was verified. All 55 passages matched; no previous 917 grade was overwritten. Final distribution: **680 grade 2 / 484 grade 1 / 226 grade 0**.

| Scope clarified | Revised pairs | Source basis |
| --- | ---: | --- |
| HİA methodology/modules | 14 | Explicit survey identity and supplemental-module collection |
| Broad population queries, including typo | 10 | Actual national population totals or explicit national population-series methodology |
| Young women unemployment | 1 | Joint 15–24 female unemployment rate in the full labour bulletin |
| Seasonal unemployment | 2 | Explicit seasonal-adjustment label |
| First-hand housing | 19 | Actual first-hand counts or an explicit first/second-hand count definition |
| Izmir housing | 9 | Explicit Izmir sales total |

Generic geography dimensions do not establish the requested province. The youth-methodology calibration weights alone were not promoted to evidence of a published youth unemployment breakdown. National/Bursa/Antalya/Diyarbakır context retains its stated scope. These are one AI assessor's engineering judgments, not human labels or inter-rater agreement. κ and human agreement remain null, with zero human pairs.

## Measured results and default decision

| System | nDCG@10 | MRR@10 | Pooled Recall@10 |
| --- | ---: | ---: | ---: |
| Portal¹ | 0.8096 | 0.9362 | 0.3925 |
| Literal¹ | 0.1661 | 0.2552 | 0.1667 |
| Built-in Turkish | 0.6984 | 0.9075 | 0.4160 |
| Multi-field | 0.6762 | 0.8587 | 0.4125 |
| Multi-field + synonyms | 0.7304 | 0.8796 | 0.4296 |

¹ Portal covers all public portal types; literal covers 341 downloadable flows. Their overall numbers are **not same-corpus comparisons** with the 3,424-document OpenSearch variants. Only the three OpenSearch variants share the complete experiment corpus. The enlarged pool also retains older candidates; do not interpret changes from the 917-pair pilot as controlled regressions/improvements across identical pools.

**Retain `multi_field_synonyms` as the default.** It has the highest full-corpus nDCG@10 (**0.7304**) and pooled Recall@10 (**0.4296**) among matched variants: +0.0320 nDCG over built-in Turkish and +0.0542 over synonym-free multi-field. Built-in Turkish has the better MRR (**0.9075 vs 0.8796**), so this is a graded-ranking/coverage tradeoff, not a win on every metric. Production mapping/query/synonyms remain unchanged; the full experiment now supports that existing choice.

The [validated metric JSON](search-full-ai-reviewed-scores-2026-11-05.json) has `status:ai_reviewed`,1,390 complete grades and **zero unjudged returned slots** for all five systems. `release_gate_satisfied:false` is intentional: this standalone AI report does not certify promotion, tag or publication. Optional independent human validation has not been performed.

Each cell below is **nDCG@10 / MRR@10 / pooled Recall@10**; each query category has six queries. The JSON retains returned counts, unjudged shares and zero-relevant-query diagnostics.

| Query category | Portal¹ | Literal¹ | Built-in Turkish | Multi-field | Multi-field + synonyms |
| --- | --- | --- | --- | --- | --- |
| exact | 1.0000 / 1.0000 / 0.4092 | 0.6472 / 0.8333 / 0.5862 | 0.7980 / 0.8542 / 0.3372 | 0.8091 / 0.8571 / 0.3456 | 0.7714 / 0.8571 / 0.3456 |
| abbreviation | 0.8191 / 1.0000 / 0.3193 | 0.4110 / 0.7083 / 0.4640 | 0.7027 / 1.0000 / 0.4012 | 0.7027 / 1.0000 / 0.4012 | 0.9110 / 1.0000 / 0.4235 |
| synonym | 0.7409 / 0.8519 / 0.4113 | 0.0000 / 0.0000 / 0.0000 | 0.3569 / 0.8333 / 0.3367 | 0.3460 / 0.6667 / 0.3446 | 0.6202 / 0.8333 / 0.4591 |
| inflection | 0.6292 / 0.8542 / 0.4043 | 0.1019 / 0.1667 / 0.1333 | 0.7761 / 1.0000 / 0.5479 | 0.7351 / 1.0000 / 0.5479 | 0.7351 / 1.0000 / 0.5479 |
| ascii | 0.9170 / 1.0000 / 0.3151 | — | 0.7453 / 0.9167 / 0.3420 | 0.5965 / 0.6905 / 0.2936 | 0.5851 / 0.6905 / 0.2936 |
| typo | 0.7651 / 0.9167 / 0.3571 | 0.1473 / 0.1667 / 0.1167 | 0.7645 / 0.9167 / 0.4966 | 0.7874 / 0.9167 / 0.4966 | 0.7874 / 0.9167 / 0.4966 |
| multi_concept | 0.8179 / 0.8667 / 0.4948 | 0.0214 / 0.1667 / 0.0333 | 0.5827 / 0.7389 / 0.3838 | 0.5721 / 0.7389 / 0.3734 | 0.5721 / 0.7389 / 0.3734 |
| regional | 0.7872 / 1.0000 / 0.4290 | — | 0.8613 / 1.0000 / 0.4822 | 0.8607 / 1.0000 / 0.4973 | 0.8607 / 1.0000 / 0.4973 |

Resource-type diagnostics filter the original top ten without backfilling; they are not new type-filtered retrieval runs. A dash means no returned slots. Corpus eligibility still differs for portal/literal.

| Resource type | Portal¹ | Literal¹ | Built-in Turkish | Multi-field | Multi-field + synonyms |
| --- | --- | --- | --- | --- | --- |
| bulletin | 0.7463 / 0.8641 / 0.5828 | — | 0.3183 / 0.5052 / 0.2393 | 0.3884 / 0.5885 / 0.3001 | 0.4752 / 0.6510 / 0.3628 |
| dataset | 0.2387 / 0.4062 / 0.1906 | 0.1661 / 0.2552 / 0.1667 | 0.3064 / 0.5130 / 0.2973 | 0.3146 / 0.5863 / 0.2958 | 0.3192 / 0.5655 / 0.2930 |
| metadata | 0.0704 / 0.1250 / 0.0462 | — | 0.5340 / 0.6042 / 0.5046 | 0.4875 / 0.5833 / 0.4536 | 0.5089 / 0.5833 / 0.4553 |
| publication | 0.0208 / 0.0208 / 0.0208 | — | — | — | — |
| table | 0.1796 / 0.1771 / 0.1875 | — | — | — | — |
| unknown | 0.0417 / 0.0417 / 0.0417 | — | — | — | — |

## Interpretation limits

Pooled recall is not exhaustive corpus recall. This is a small frozen 48-query set, and one dependent assessor knows both the implementation and earlier labels. Historical monthly releases and methodology records count as separate IDs. Undated discovery queries do not test freshness, answer diversity or all statistical values. A high nDCG for a regional query can mean well-ranked context when the pool has no directly answering provincial result.

For the selected variant, only **23/48 first hits** and **36/48 top-ten lists** contain a grade 2 direct candidate; built-in scores 21/34 and synonym-free 19/34 respectively. [Descriptive coverage](search-full-direct-coverage-2026-11-05.json) is derived from these AI labels, not independently verified answer accuracy. Source resolution and conceptual relevance do not guarantee a currently downloadable table. Do not claim that lexical search now answers every semantic or province-specific question.

## Runtime verification

- Python 3.14 offline: **718 passed, 21 skipped, 6 deselected**; Ruff lint/format passed; Node automation/policy: **28 passed**.
- Latest runtime commit `7bd9420`: GitHub branch policy, Python 3.12, Python 3.13, Docker build and OpenSearch integration all passed ([CI run](https://github.com/ByRock010/TUIK-Data-MCP-Server/actions/runs/41979820417)).
- Live contracts excluding the separately executed MCP workflow: **5 passed / 740 deselected**,71.95 seconds, default download bound and normal pacing. The [JUnit result](search-full-live-contracts-2026-11-05.xml) is unchanged.
- The [canonical full rebuild](search-full-index-2026-11-05.json) wrote all 3,424 documents to an owned UUID alias. The [actual full MCP smoke](search-full-live-smoke-2026-11-05.json) passed with **nine tools**, package 0.2.0, Python 3.14.0, two-second requests/six-second downloads. ASCII query `altin haric dis ticaret hadleri` used OpenSearch and followed **`flow:DF_ALTINSIZ_DTH+V1.0`** through current metadata to **2 verified CSV rows** out of 11,564, with a content snapshot and no fallback. Portal/files/PDF/Excel checks also passed.
- The owned acceptance index/alias was deleted after the smoke; the application alias was never changed. The three experimental indices were independently cleaned up.

## Reproduce

Exact offline scoring from the committed snapshot and labels:

```bash
.venv/bin/python scripts/evaluate_search.py score-ai \
  --suite docs/verification/search-full-pool-2026-11-05.json.gz \
  --runs docs/verification/search-full-baseline-2026-11-05.json.gz \
    docs/verification/search-full-variants-2026-11-05.json.gz \
  --assessment docs/verification/search-full-ai-assessment-2026-11-05.json \
  --review docs/verification/search-full-ai-reviewed-assessment-2026-11-05.json \
  --evidence docs/verification/search-full-ai-live-evidence-2026-11-05.json.gz \
  --require-reviewed --output /tmp/full-reviewed-scores.json
```

Recollect analyzer rankings without TÜİK requests or application-alias writes:

```bash
.venv/bin/python scripts/evaluate_search.py collect-index \
  --documents docs/verification/search-full-corpus-2026-11-05.jsonl.gz \
  --url http://127.0.0.1:9200 --output /tmp/full-variants.json.gz
```

A fresh live experiment uses the [crawler](../METADATA_CRAWL.md), fixed queries and a new output path; it is a new source vintage, not a byte-identical replacement for retained evidence:

```bash
.venv/bin/python scripts/crawl_metadata.py --full \
  --listing-attempts 2 --alternate-listing-mode \
  --state /tmp/new-full-state.json --output /tmp/new-full.jsonl \
  --report /tmp/new-full-crawl.json
TUIK_MIN_INTERVAL_SECONDS=2 .venv/bin/python scripts/evaluate_search.py collect \
  --documents /tmp/new-full.jsonl --output /tmp/new-baseline.json.gz
```

The full-corpus comparison and default decision complete #20 in this PR; release preparation/promotion under #29/#24 remains separate. Version 0.2.0 and existing tags are unchanged.
