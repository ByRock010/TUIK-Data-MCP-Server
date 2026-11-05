# Hybrid semantic search evaluation (#21) — 2026-11-05

This report covers the frozen 3,424-document metadata corpus, 48 queries and 1,689
pooled pairs. It extends the [#20 full-corpus comparison](issue-20-full-corpus-2026-11-05.md)
with two hybrid systems. The design decision is in
[ADR 0005](../adr/0005-hybrid-semantic-search.md). The method follows
[ADR 0004](../adr/0004-ai-relevance-evaluation.md): one dependent AI assessment
followed by a sequential source review. **Independent human validation has not
occurred, κ is not applicable, and `release_gate_satisfied` stays false.**

## Result

Both hybrid settings beat the current production default on the same enlarged pool.
The pre-stated selection rule (higher reviewed nDCG@10) picks **equal-weight RRF**,
which is now the code default.

| System (same pool) | Corpus | nDCG@10 | MRR@10 | Pooled Recall@10 |
| --- | --- | ---: | ---: | ---: |
| Portal (live) | broader live portal | 0.7770 | 0.9362 | 0.3150 |
| Literal | 341 downloadable flows | 0.1661 | 0.2552 | 0.1667 |
| Built-in Turkish | frozen 3,424 | 0.6623 | 0.9075 | 0.3302 |
| Multi-field | frozen 3,424 | 0.6402 | 0.8587 | 0.3273 |
| Multi-field + synonyms (v0.3.0 default) | frozen 3,424 | 0.6944 | 0.8796 | 0.3387 |
| Hybrid RRF, lexical-weighted 0.7/0.3 | frozen 3,424 | 0.7898 | 0.9578 | 0.3595 |
| **Hybrid RRF, equal 0.5/0.5** | frozen 3,424 | **0.8516** | **0.9896** | **0.3653** |

The #20 numbers for the lexical systems were computed on the 1,390-pair pool; for
example, the synonym default scored 0.7304 there. They are lower here because the
hybrid systems found further judged-relevant documents, which raises every query's
ideal DCG. All systems are scored against the same 1,689 reviewed grades. Every
returned slot is judged: the unjudged share is 0 for every system. Portal and
literal still search different corpora (see #20).

### By query category (nDCG@10, MRR@10)

| Category | Synonym default | Hybrid 0.7/0.3 | Hybrid equal | MRR default → equal |
| --- | ---: | ---: | ---: | --- |
| exact | 0.771 | 0.850 | 0.986 | 0.857 → 1.000 |
| abbreviation | **0.911** | 0.888 | 0.833 | 1.000 → 0.917 |
| synonym | 0.620 | 0.651 | 0.721 | 0.833 → 1.000 |
| inflection | 0.692 | 0.738 | 0.773 | 1.000 → 1.000 |
| ascii | 0.585 | 0.609 | 0.723 | 0.690 → 1.000 |
| typo | 0.684 | 0.836 | 0.954 | 0.917 → 1.000 |
| multi_concept | 0.461 | 0.907 | 0.954 | 0.739 → 1.000 |
| regional | 0.832 | 0.840 | 0.869 | 1.000 → 1.000 |

Abbreviations regress. For `HİA`, the k-NN leg ranks "Enerji Hariç İhracat/İthalat
Birim Değer Endeksi" (grade 0) first, because the model has no notion of the
survey acronym. Typical gains look like this:

- `İşsizlik Oranı`: the old top three were dependency/emission *ratios* (all grade 0).
  Hybrid returns İşgücü İstatistikleri bulletins (grade 2).
- `mevsim etkisinden arındırılmış işsizlik`: seasonally adjusted CPI/sales/exports
  (grade 0) are replaced by labour bulletins (grade 2).

## Limitations that bound this result

- **The gain comes from precision, not new series.** Series-collapsed nDCG@10 is
  [unchanged](search-hybrid-series-collapsed-2026-11-05.json): synonym default
  0.4575, hybrid equal 0.4526. In this diagnostic, later releases of a bulletin
  series score zero. Top-ten slots repeating an already-seen series rise from 252
  to 328. Hybrid mostly replaces irrelevant lexical hits with more releases of
  series that are already relevant. That is what nDCG@10 rewards under the
  inherited rubric, where historical releases count separately. It does not
  measure answer diversity or recency. Grouping repeated series in results is a
  follow-up.
- **Datasets rank lower.** Filtering the original top ten by type gives a dataset
  nDCG@10 of 0.157 for hybrid versus 0.315 for the default. Dataset-filtered
  retrieval (`search_tuik_datasets`, `kind="dataset"`) was not measured, so the
  server keeps it lexical (ADR 0005).
- **Two different AI assessors, neither independent.** The 1,390 inherited labels
  are the unchanged OpenAI Codex source-reviewed set. The 299 new pairs were
  graded and reviewed by Anthropic Claude, which also implemented the hybrid
  systems. It saw lower-bound pilot scores before grading. New pairs follow the
  inherited query intents and give repeated releases of an already graded series
  that series' grade unless their text changes the scope.
- **Weights were fixed, but the model was not chosen blind.** The model and the
  two weight settings were chosen before grading. The model was chosen after
  unblinded lower-bound pilots. No setting was tuned after the reviewed grades.
- **Tie order.** Hybrid queries cannot take a `_score` + `id` sort. Ranking ties
  keep OpenSearch's internal order in the freshly loaded single-shard index.

## Evidence

| File | Content |
| --- | --- |
| [search-hybrid-variants-2026-11-05.json.gz](search-hybrid-variants-2026-11-05.json.gz) | Hybrid rankings, mappings, exact query templates, embedding model/digests, cleanup |
| [search-hybrid-pool-2026-11-05.json.gz](search-hybrid-pool-2026-11-05.json.gz) | Enlarged pool: 1,390 → 1,689 judged pairs over 748 documents (299 new pairs) |
| [search-hybrid-ai-assessment-2026-11-05.json](search-hybrid-ai-assessment-2026-11-05.json) | First pass: 1,390 inherited + 299 new grades |
| [search-hybrid-ai-live-evidence-2026-11-05.json.gz](search-hybrid-ai-live-evidence-2026-11-05.json.gz) | 807 real stdio MCP calls: 48 portal replays, 742 resolved identities, bounded probes |
| [search-hybrid-ai-reviewed-assessment-2026-11-05.json](search-hybrid-ai-reviewed-assessment-2026-11-05.json) | Second pass, 748 source checks (742 live, 6 archived), 9 documented revisions |
| [search-hybrid-ai-reviewed-scores-2026-11-05.json](search-hybrid-ai-reviewed-scores-2026-11-05.json) | `score-ai --require-reviewed` output, overall/category/type metrics |
| [search-hybrid-series-collapsed-2026-11-05.json](search-hybrid-series-collapsed-2026-11-05.json) | Supplementary diversity diagnostic |

The 299 new pairs received 227 grade-2, 56 grade-1 and 16 grade-0 first-pass labels.
All 199 documents involved resolved live with titles identical to the frozen corpus.
The review raised nine `sektörel ihracat miktarı` pairs from 1 to 2. Their Dış
Ticaret Endeksleri bulletins report export quantity changes by product sector group.
Each revision cites a frozen body passage that also occurs in the exact live response.

The variant collection and the live evidence ran from this branch's uncommitted
working tree on base `e3e991b`. That is why both files record that revision. The
exact collector script is pinned by its recorded SHA-256. The evidence's
`package_version` 0.2.0 is the editable install's stale metadata; the source is 0.3.0.
The embedding model was `intfloat/multilingual-e5-small`, ONNX snapshot
`614241f622f53c4eeff9890bdc4f31cfecc418b3`, through fastembed 0.9.1 and
onnxruntime 1.31.1. It took 86 s for 3,424 documents on an Apple M4. The engine was
OpenSearch 3.3.2.

## Reproduce

Score the retained evidence offline (byte-identical output):

```bash
.venv/bin/python scripts/evaluate_search.py score-ai \
  --suite docs/verification/search-hybrid-pool-2026-11-05.json.gz \
  --runs docs/verification/search-full-baseline-2026-11-05.json.gz \
    docs/verification/search-full-variants-2026-11-05.json.gz \
    docs/verification/search-hybrid-variants-2026-11-05.json.gz \
  --assessment docs/verification/search-hybrid-ai-assessment-2026-11-05.json \
  --review docs/verification/search-hybrid-ai-reviewed-assessment-2026-11-05.json \
  --evidence docs/verification/search-hybrid-ai-live-evidence-2026-11-05.json.gz \
  --require-reviewed --output /tmp/hybrid-reviewed-scores.json
```

Recollect the hybrid rankings against a disposable cluster. This needs the
`semantic` extra and downloads the model once; it makes no TÜİK requests:

```bash
python -m pip install -e '.[dev,semantic]'
.venv/bin/python scripts/evaluate_search.py collect-index \
  --suite docs/verification/search-full-pool-2026-11-05.json.gz \
  --documents docs/verification/search-full-corpus-2026-11-05.jsonl.gz \
  --url http://127.0.0.1:9200 \
  --variants hybrid_rrf_lexical_weighted hybrid_rrf_equal \
  --output /tmp/hybrid-variants.json.gz
```
