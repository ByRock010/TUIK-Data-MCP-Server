# ADR 0005: Optional hybrid lexical + semantic search

Status: proposed in the #21 PR. Date: 2026-11-05. Issue: #21. Related: ADR 0002,
ADR 0004, #20, #22, #25.

## Context

The v0.3.0 index ranks lexically: Turkish/ASCII analyzers, curated synonyms and a
weaker fuzzy clause. Meaning-level queries still depend on those synonym rules,
and short generic words can dominate. For example, `İşsizlik Oranı` ranked emission
and dependency *ratios* above labour-force bulletins. #21 asks for multilingual
embeddings and a hybrid query. It may merge only if nDCG@10 improves on the
evaluation set.

## Decision

1. **Model:** `intfloat/multilingual-e5-small` (384 dimensions, MIT licence), run as
   ONNX through the optional `semantic` extra (`fastembed`). This needs no PyTorch.
   A feasibility pilot on the frozen 3,424-document corpus compared it with
   `paraphrase-multilingual-MiniLM-L12-v2` and `paraphrase-multilingual-mpnet-base-v2`.
   e5-small had the strongest k-NN-only and fused rankings. The pilot's lower-bound
   metrics counted unjudged documents as zero and were not the selection metric.
2. **Where embeddings run:** in Python. `tuik-mcp-index --embeddings` embeds the
   metadata documents it writes. The MCP server embeds the query with the same
   pinned model, using locally cached files only. OpenSearch ML Commons was not
   chosen, for three reasons:
   - Evaluation and production would then use different code paths.
   - Model choice would be limited to the cluster's pretrained list, which excludes
     e5.
   - The cluster would need model registration, extra heap and internet access
     during setup.
3. **Document text:** the title, description and body are concatenated and cut at
   1,000 characters, with the E5 `passage: ` prefix. Queries use `query: `. Only
   `dataset`, `bulletin` and `metadata` documents get vectors. File content (#22)
   stays lexical.
4. **Index:** mapping schema 3 adds an optional `embedding` `knn_vector` field (HNSW,
   cosine, Lucene engine). `_meta.embedding_model` records which model filled it, or
   null. Incremental updates refuse to mix models or to add unvectored documents to
   a vectored index. Only new or changed documents are re-embedded.
5. **Query:** the existing lexical query and a filtered k-NN query (`k=50`) form a
   native `hybrid` query. They are fused by an inline `score-ranker-processor`
   performing weighted RRF (`rank_constant` 60, `pagination_depth` 50). The pipeline
   travels in the request, so no cluster object needs to stay in sync with the code.
6. **Weights:** two settings were fixed before any new grade was made: lexical-weighted
   0.7/0.3 and equal 0.5/0.5. The pre-stated rule was to take the higher reviewed
   nDCG@10. Equal weights won (0.8516 vs 0.7898; current default 0.6944 on the same
   enlarged pool).
7. **Scope and fallback:** hybrid ranking is used only when all of these hold:
   - semantic search is enabled (`TUIK_SEMANTIC_SEARCH`, default true);
   - the extra and the model files are present;
   - the served index's model equals the server's;
   - no `table` or `document_chunk` kind is requested (dataset filters are hybrid
     since decision 10);
   - the entire query is not a curated bare statistical abbreviation (decision 9);
   - `offset + limit ≤ 50`.

   Otherwise the request is lexical, exactly as before. A warning is added when
   vectors exist but cannot be used, or when a hybrid request fails.
   Responses report `ranking: "lexical" | "hybrid"`.
8. **Lexical evidence required (amendment, 2026-11-06):** k-NN always returns its
   50 nearest neighbours, so a gibberish query (`xqzvkj`) used to return 50
   unrelated import-index datasets. A similarity floor cannot fix this: on the frozen
   corpus the top e5 cosine scores were 0.9185 for `xqzvkj`, 0.8990 for `asdf qwer`,
   0.9187 for `issizlik orani` and 0.9211 for `kaç çift ayrıldı`. The hybrid request
   therefore carries a `filter` aggregation that counts the lexical query's matches
   in the same round trip. When the count is zero, the response has no hits, total 0,
   empty facets and a warning. None of the 48 evaluation queries is affected, so the
   measured metrics stand. A response that lacks the count falls back to lexical
   search rather than skipping the check.
9. **Bare abbreviations stay lexical (amendment, 2026-11-19, #79):** match TÜFE,
   ÜFE, Yİ-ÜFE (also `Yİ ÜFE`), GSYH, HİA, ADNKS and NACE as whole queries,
   case-insensitively with Turkish/ASCII spelling and normalized whitespace.
   These are the curated synonym equivalences, not an uppercase-word heuristic.
   Route them to the unchanged synonym-aware lexical query before vector-model
   discovery or embedding, ordered by score descending and ID ascending to match
   the frozen lexical evaluation. Filters, facets and offsets still apply.
   Report `ranking: "lexical"` and explain the choice when semantic search is
   enabled and the kind permits it. Full expansions and contextual queries such as
   `TÜFE 2026` retain the existing hybrid behavior.

   The [fixed plan](../verification/issue-79-abbreviation-plan-2026-11-19.md)
   selected this rule before scoring. An
   [archived-routing replay](../verification/issue-79-abbreviation-routing-2026-11-19.md)
   of the same 48 queries / 1,689 reviewed pairs gives abbreviation nDCG@10
   **0.9110** (previously 0.8331) and overall **0.8613** (previously 0.8516).
   HİA and ADNKS recover; TÜFE drops from 0.8533 to 0.6873. No other query changes.
   No new retrieval, embeddings, judgments or live source review are claimed.
   NACE and spelling variants have routing tests but are outside the six-query
   abbreviation relevance measurement. The historical pool is not held out.

10. **Dataset-filtered searches are hybrid (amendment, 2026-11-19, #80):**
    `search_tuik(kind="dataset")` and `search_tuik_datasets` were lexical because
    only mixed-kind search had been measured. A
    [pre-registered evaluation](../verification/issue-80-dataset-hybrid-plan-2026-11-19.md)
    ranked the frozen corpus with the dataset filter inside every query, all systems
    from one index build. On 1,887 reviewed pairs, dataset-filtered nDCG@10 was
    0.6549 lexical, 0.6854 equal RRF, 0.7138 lexical-weighted 0.7/0.3 and **0.6898**
    for the served combination (equal RRF with decision 9's abbreviation routing);
    MRR@10 rose from 0.7880 to 0.8715. The fixed rule adopts the served combination,
    so `dataset` left `LEXICAL_ONLY_KINDS`. The 0.7/0.3 weighting scored higher but
    cannot be adopted for datasets alone. Synonym, ASCII and typo queries regress
    slightly; see the [report](../verification/issue-80-dataset-hybrid-2026-11-19.md).

## Consequences

- Base installation, Docker image and unconfigured servers are unchanged. Semantic
  search costs one model download of about 490 MB for the indexer and the server,
  plus about 90 s of CPU embedding for 3,424 documents on an Apple M4.
- The loaded model takes about 1 GB of resident memory in the server (measured
  2026-11-10; 91 MB with `TUIK_SEMANTIC_SEARCH=false`). It loads on the first hybrid
  search, which takes 0.5–0.8 s longer.
- Highlights come from a second, lexical request restricted to the ranked IDs.
  Inside the hybrid request, highlighting took about 20 ms per returned document
  (238 ms median at `limit=10`); the two requests together take about 27 ms. Hybrid
  highlights now equal lexical-mode highlights, which match multi-word synonyms as
  phrases. See the
  [usage and performance report](../verification/usage-performance-2026-11-10.md).
- Hybrid `score` values are RRF sums (about 0.01–0.03), not BM25 scores.
- Because of the lexical-evidence rule, semantic search refines queries that share
  at least one word (or a close spelling) with the index; it does not rescue a
  query with no lexical match. English queries such as `unemployment rate` match
  nothing lexically and now return no results, with a warning to retry in Turkish.
  Before the rule they returned relevant labour bulletins through k-NN alone.
- OpenSearch rejects a `_score` tie-breaker sort on hybrid queries. Ties keep the
  engine's internal order. Beyond ties, hybrid top tens differ between index builds
  of the same corpus and vectors: in #78 a fresh build matched the archived #79
  top ten for 9 of 48 queries, and two further builds matched each other for 8–9.
  Approximate HNSW k-NN is the likely cause. Reviewed nDCG@10 moved only slightly
  (0.8560 vs 0.8576), but comparisons must use rankings from one build.
- The original #21 gain mostly replaces irrelevant lexical hits with further releases
  of already relevant series. Series-collapsed nDCG@10 is unchanged (0.4526 vs
  0.4575), and abbreviation queries regress (0.833 vs 0.911). The PR records both.
  Decision 9 recovers the aggregate abbreviation regression. Series grouping
  (#78) is available as `group_series=true`: it raises series-collapsed nDCG@10 to
  0.7407 but lowers plain nDCG@10 to 0.5529, below the v0.3.0 default, so it is
  not the default ([report](../verification/issue-78-series-grouping-2026-11-19.md)).
- The new grades come from a second AI model (Claude) under ADR 0004's single-
  assessor-plus-source-review protocol. The 1,390 inherited Codex labels are
  unchanged. Independent human validation has not occurred.

## Alternatives considered

| Alternative | Assessment |
| --- | --- |
| OpenSearch ML Commons `neural` query | Server stays model-free. However, model choice is limited to the pretrained list, the cluster needs model registration and more memory, and the evaluated path would differ from Python indexing. |
| `bge-m3` / `multilingual-e5-large` | Larger (about 2.2 GB) and slower on CPU for an MCP server process. Not needed to meet the acceptance criterion. |
| Min-max score normalization + arithmetic mean | In the pilot, BM25 and cosine distributions made it sensitive to outliers. RRF is rank-based and parameter-light. |
| Python-side RRF over two requests | Loses native facets, totals and highlights on one response. The native hybrid query keeps the existing response contract. |
