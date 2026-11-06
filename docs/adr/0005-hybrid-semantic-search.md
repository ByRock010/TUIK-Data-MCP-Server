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
   - no `dataset`, `table` or `document_chunk` kind is requested;
   - `offset + limit ≤ 50`.

   Otherwise the request is lexical, exactly as before. A warning is added when
   vectors exist but cannot be used, or when a hybrid request fails.
   `search_tuik_datasets` stays lexical: dataset-filtered retrieval was not measured,
   and datasets ranked lower in hybrid top tens. Responses report
   `ranking: "lexical" | "hybrid"`.
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

## Consequences

- Base installation, Docker image and unconfigured servers are unchanged. Semantic
  search costs one model download of about 490 MB for the indexer and the server,
  plus about 90 s of CPU embedding for 3,424 documents on an Apple M4.
- Hybrid `score` values are RRF sums (about 0.01–0.03), not BM25 scores.
- Because of the lexical-evidence rule, semantic search refines queries that share
  at least one word (or a close spelling) with the index; it does not rescue a
  query with no lexical match. English queries such as `unemployment rate` match
  nothing lexically and now return no results, with a warning to retry in Turkish.
  Before the rule they returned relevant labour bulletins through k-NN alone.
- OpenSearch rejects a `_score` tie-breaker sort on hybrid queries. Ties keep the
  engine's internal order, which is deterministic for a freshly loaded
  single-shard index but not across arbitrary rebuilds.
- The measured gain mostly replaces irrelevant lexical hits with further releases
  of already relevant series. Series-collapsed nDCG@10 is unchanged (0.4526 vs
  0.4575), and abbreviation queries regress (0.833 vs 0.911). The PR records both.
  Collapsing repeated series in results is a follow-up, not part of this decision.
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
