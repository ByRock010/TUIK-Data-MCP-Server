# Dataset-filtered hybrid ranking (#80)

Date: 2026-11-19 (Europe/Istanbul). Plan: [issue-80-dataset-hybrid-plan-2026-11-19.md](issue-80-dataset-hybrid-plan-2026-11-19.md), fixed in `d7cc1b9` before retrieval or grading.

## Result

All four systems were collected from one disposable index of the frozen 3,424-document corpus
(`collect-datasets`, OpenSearch 3.3.2, multilingual-e5-small), with the dataset filter in every query.
Scores use `score-ai --require-reviewed` over 1,887 pairs and dataset grades only; no returned slot is unjudged.

| System | Dataset nDCG@10 | MRR@10 | Pooled Recall@10 |
| --- | ---: | ---: | ---: |
| Lexical (previous behaviour) | 0.6549 | 0.7880 | 0.6558 |
| Hybrid 0.7/0.3 | 0.7138 | 0.8771 | 0.7491 |
| Hybrid equal | 0.6854 | 0.8715 | 0.7289 |
| Hybrid equal + abbreviation routing (served) | **0.6898** | **0.8715** | **0.7334** |

The served combination has higher nDCG@10 and higher MRR@10 than lexical, so the fixed rule adopts it:
`dataset` left `LEXICAL_ONLY_KINDS` (ADR 0005 decision 10). The 0.7/0.3 weighting scored higher but
production uses one weighting, as the plan stated.

By category (dataset nDCG@10, lexical → served; six queries each, so the category means equal the
overall figures): exact 0.740 → 0.847, abbreviation 0.719 → 0.719, synonym 0.746 → 0.735,
inflection 0.740 → 0.758, ASCII 0.564 → 0.503, typo 0.586 → 0.558, multi-concept 0.516 → 0.721,
regional 0.629 → 0.678. Synonym, ASCII and typo queries regress.
`enflsyon` returns no dataset in any system (no dataset shares a word with it).

Correction (review of PR #116): the first version of this report listed category values whose ideal
also counted bulletin and metadata grades, although every system is dataset-filtered. Scoring now
applies a run's recorded kind filter (`settings.filters.kinds`) to the ideal, so the overall and
per-category figures use dataset grades alone, and `collect-datasets` declares only dataset IDs as
`corpus_ids`. The archived rankings (SHA-256 bound by both assessments), all grades and the dataset
table above are unchanged; only the reviewed scores file was regenerated.

## Grading and evidence

198 new pairs: 164 graded by Anthropic Claude (Opus 5.5) under ADR 0004; 34 identical pairs take the same
assessor's #78 source-reviewed grade (recorded with that file's SHA-256). A live MCP collection (853 calls)
checked 794 pooled documents: 788 live, 6 archival (`DF_SATIS_SEKLI_DURUMU_ILCE` has left the public catalog;
five evaluation-only portal identities). No grade was revised. Grades are dependent and AI-only.

Files: `search-dataset-hybrid-2026-11-19.json.gz` (rankings), `search-dataset-hybrid-pool-2026-11-19.json.gz`,
`search-dataset-hybrid-ai-assessment-2026-11-19.json`, `search-dataset-hybrid-ai-live-evidence-2026-11-19.json.gz`,
`search-dataset-hybrid-ai-reviewed-assessment-2026-11-19.json`, `search-dataset-hybrid-ai-reviewed-scores-2026-11-19.json`.

After adoption, the collector's lexical system uses `OpenSearchBackend.search` without an embedder; its
request body is identical to the pre-#80 served dataset request, so the archived lexical ranking stands.
872 offline and real-OpenSearch tests pass (`-m "not live"`).
