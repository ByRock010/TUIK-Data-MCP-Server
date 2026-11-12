"""Hybrid lexical + semantic search (#21): embeddings, query, routing and indexing."""

import importlib.util
import json
import os
import sys
import uuid
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from tuik_mcp.search import embeddings
from tuik_mcp.search.backend import (
    HYBRID_DEPTH,
    HYBRID_WEIGHTS,
    LEXICAL_MATCHES_AGG,
    NO_LEXICAL_MATCH,
    OpenSearchBackend,
    SearchFilters,
    build_highlight_query,
    build_hybrid_query,
    build_query,
)
from tuik_mcp.search.documents import IndexDocument
from tuik_mcp.search.embeddings import (
    DIMENSION,
    MODEL,
    Embedder,
    EmbeddingUnavailable,
    document_text,
    embed_payloads,
)
from tuik_mcp.search.indexer import (
    IndexerError,
    OpenSearchStore,
    check_vectors,
    comparable,
    prepare,
    rebuild,
    update,
)

STAMP = datetime(2026, 11, 5, 9, tzinfo=UTC)
spec = importlib.util.spec_from_file_location(
    "evaluate_search", Path(__file__).parents[1] / "scripts" / "evaluate_search.py"
)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


class FakeEmbedder:
    """Deterministic vectors without the optional ONNX runtime."""

    model = MODEL
    dimension = DIMENSION

    def __init__(self, fail=False):
        self.fail, self.queries, self.batches = fail, [], []

    @staticmethod
    def vector(text):
        seed = sum(map(ord, text)) % 97 + 1
        return [((seed * (i + 1)) % 13) / 13 + 0.01 for i in range(DIMENSION)]

    def documents(self, texts):
        self.batches.append(list(texts))
        return [self.vector(text) for text in texts]

    def query(self, text):
        if self.fail:
            raise EmbeddingUnavailable("model files are missing")
        self.queries.append(text)
        return self.vector(text)


def metadata_documents():
    return [
        IndexDocument(
            id="flow:DF_ISSIZ+V1.0",
            kind="dataset",
            title="İşsizlik Oranı",
            source_url="https://databrowser2.tuik.gov.tr/",
            downloadable=True,
            formats=["csv"],
        ),
        IndexDocument(
            id="press:57987",
            kind="bulletin",
            title="İşgücü İstatistikleri, Ağustos 2026",
            body="Mevsim etkisinden arındırılmış işsizlik oranı %8,5 oldu.",
            source_url="https://veriportali.tuik.gov.tr/tr/press/57987",
        ),
        IndexDocument(
            id="press:57987:file:isgucu~0123456789#p1.1",
            kind="document_chunk",
            parent_id="press:57987:file:isgucu~0123456789",
            page=1,
            title="İşgücü İstatistikleri",
            body="Tablo 1",
            source_url="https://veriportali.tuik.gov.tr/tr/press/57987",
        ),
    ]


def test_document_text_orders_title_first_and_is_bounded():
    text = document_text({"title": "TÜFE", "description": None, "body": "x" * 5000})
    assert text.startswith("TÜFE x") and len(text) == embeddings.DOCUMENT_CHARS
    assert document_text({"title": "A", "description": "B"}) == "A B"


def test_only_metadata_payloads_receive_vectors():
    payloads, _ = prepare(metadata_documents(), STAMP)
    embedder = FakeEmbedder()
    assert embed_payloads(payloads, embedder) == 2
    assert [("embedding" in p) for p in payloads] == [True, True, False]
    assert len(payloads[0]["embedding"]) == DIMENSION
    assert embedder.batches == [
        ["İşsizlik Oranı", payloads[1]["title"] + " " + payloads[1]["body"]]
    ]


def test_missing_runtime_is_reported_once_per_ttl(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "fastembed", None)
    embedder = Embedder(cache_dir=str(tmp_path), local_files_only=True)
    with pytest.raises(EmbeddingUnavailable, match=r"tuik-mcp\[semantic\]"):
        embedder.query("enflasyon")
    calls = []
    monkeypatch.setattr(embedder, "_create", lambda: calls.append(1))
    with pytest.raises(EmbeddingUnavailable):
        embedder.query("enflasyon")
    assert calls == []  # The cached failure is reused, not retried on every search.
    assert Embedder().cache_dir == str(embeddings.DEFAULT_CACHE_DIR)


def test_role_prefixes_and_vector_size_are_enforced(monkeypatch):
    seen = []

    class Model:
        def embed(self, texts, batch_size):
            texts = list(texts)
            seen.extend(texts)
            return [[0.5] * (DIMENSION if "short" not in t else 3) for t in texts]

    embedder = Embedder()
    monkeypatch.setattr(embedder, "_create", Model)
    assert len(embedder.query("hayat pahalılığı")) == DIMENSION
    embedder.documents(["Tüketici Fiyat Endeksi"])
    assert seen == ["query: hayat pahalılığı", "passage: Tüketici Fiyat Endeksi"]
    with pytest.raises(EmbeddingUnavailable, match="unexpected size"):
        embedder.query("short")


def test_hybrid_query_fuses_the_lexical_query_with_filtered_knn():
    filters = SearchFilters(themes=["Nüfus ve Demografi"], downloadable=True)
    vector = [0.1] * DIMENSION
    body = build_hybrid_query("nüfus", filters, 10, 0, vector)
    lexical, semantic = body["query"]["hybrid"]["queries"]
    assert lexical == build_query("nüfus", filters, 10)["query"]
    knn = semantic["knn"]["embedding"]
    assert knn["vector"] == vector and knn["k"] == HYBRID_DEPTH
    assert knn["filter"] == {"bool": {"filter": lexical["bool"]["filter"]}}
    assert body["query"]["hybrid"]["pagination_depth"] == HYBRID_DEPTH
    (processor,) = body["search_pipeline"]["phase_results_processors"]
    combination = processor["score-ranker-processor"]["combination"]
    assert combination["technique"] == "rrf"
    assert combination["parameters"]["weights"] == list(HYBRID_WEIGHTS) == [0.5, 0.5]
    assert "embedding" in body["_source"]["excludes"] and "sort" not in body
    # Highlighting inside a hybrid request is slow: a second request fetches it.
    assert "aggs" in body and "highlight" not in body
    # Counts lexical matches in the same request; the lexical query itself has no such agg.
    assert body["aggs"][LEXICAL_MATCHES_AGG] == {"filter": lexical}
    assert LEXICAL_MATCHES_AGG not in build_query("nüfus", filters, 10)["aggs"]
    with pytest.raises(ValueError, match="first 50"):
        build_hybrid_query("nüfus", filters, 10, 45, vector)


def test_highlight_query_restricts_the_lexical_clauses_to_the_ranked_documents():
    lexical = build_query("nüfus", SearchFilters(), 10)
    body = build_highlight_query("nüfus", ["press:1", "flow:DF_A+V1.0"])
    assert body["query"]["bool"] == {
        "filter": [{"ids": {"values": ["press:1", "flow:DF_A+V1.0"]}}],
        # No minimum_should_match: a semantic-only hit returns without highlights.
        "should": lexical["query"]["bool"]["should"],
    }
    assert body["highlight"] == lexical["highlight"]
    assert body["size"] == 2 and body["_source"] is False and "aggs" not in body


def request_kind(body):
    if "hybrid" in body["query"]:
        return "hybrid"
    return "highlight" if "ids" in str(body["query"]["bool"]["filter"]) else "lexical"


def cluster(model=MODEL, fail_hybrid=False, models=None, lexical_matches=1, fail_highlight=False):
    requests = []

    def handler(request):
        path = request.url.path
        if path.endswith("/_mapping"):
            models_by_index = models or [model]
            return httpx.Response(
                200,
                json={
                    f"tuik-resources-v{i}": {
                        "mappings": {"_meta": {"schema_version": 3, "embedding_model": m}}
                    }
                    for i, m in enumerate(models_by_index)
                },
            )
        body = json.loads(request.content)
        requests.append(body)
        if fail_hybrid and "hybrid" in body["query"]:
            return httpx.Response(400, json={"error": "no such query [hybrid]"})
        if request_kind(body) == "highlight":
            if fail_highlight:
                return httpx.Response(500, json={"error": "highlight phase failed"})
            highlight = {"title": ["<em>İşgücü</em> İstatistikleri"]}
            return httpx.Response(
                200, json={"hits": {"hits": [{"_id": "press:57987", "highlight": highlight}]}}
            )
        return httpx.Response(
            200,
            json={
                "hits": {
                    "total": {"value": 1, "relation": "eq"},
                    "hits": [
                        {
                            "_score": 0.016,
                            "_source": {
                                "id": "press:57987",
                                "kind": "bulletin",
                                "title": "İşgücü İstatistikleri",
                                "source_url": "https://veriportali.tuik.gov.tr/tr/press/57987",
                            },
                        }
                    ],
                },
                "aggregations": {
                    name: {
                        "buckets": [{"key": "bulletin", "doc_count": 1}],
                        "sum_other_doc_count": 0,
                        "doc_count_error_upper_bound": 0,
                    }
                    for name in ("kind", "theme")
                }
                | (
                    {LEXICAL_MATCHES_AGG: {"doc_count": lexical_matches}}
                    if body["aggs"].get(LEXICAL_MATCHES_AGG)
                    else {}
                ),
            },
        )

    return OpenSearchStore("http://cluster", transport=httpx.MockTransport(handler)), requests


@pytest.mark.parametrize(
    ("model", "filters", "offset", "fail", "ranking", "warning"),
    [
        (MODEL, SearchFilters(), 0, False, "hybrid", None),
        (MODEL, SearchFilters(kinds=["bulletin", "metadata"]), 0, False, "hybrid", None),
        (None, SearchFilters(), 0, False, "lexical", None),
        ("other/model", SearchFilters(), 0, False, "lexical", "holds other/model vectors"),
        (MODEL, SearchFilters(), 45, False, "lexical", "first 50 results"),
        (MODEL, SearchFilters(), 0, True, "lexical", "Semantic ranking is unavailable"),
        (MODEL, SearchFilters(kinds=["dataset"]), 0, False, "lexical", None),
        (MODEL, SearchFilters(kinds=["table"]), 0, False, "lexical", None),
    ],
)
async def test_backend_uses_hybrid_only_when_vectors_model_and_scope_match(
    model, filters, offset, fail, ranking, warning
):
    store, requests = cluster(model)
    embedder = FakeEmbedder(fail=fail)
    try:
        hits = await OpenSearchBackend(store, "tuik-resources", embedder).search(
            "iş bulamayanlar", filters, 10, offset
        )
    finally:
        await store.close()
    assert hits.ranking == ranking
    kinds = [request_kind(body) for body in requests]
    if ranking == "hybrid":
        assert kinds == ["hybrid", "highlight"]
        assert requests[1]["query"]["bool"]["filter"] == [{"ids": {"values": ["press:57987"]}}]
        assert hits.hits[0].highlights == {"title": ["<em>İşgücü</em> İstatistikleri"]}
    else:
        assert kinds == ["lexical"] and "highlight" in requests[0]
    assert hits.hits[0].id == "press:57987" and hits.facets is not None
    if warning:
        assert any(warning in w for w in hits.warnings)
    else:
        assert hits.warnings == []
    if ranking == "hybrid":
        assert embedder.queries == ["iş bulamayanlar"]


async def test_failed_highlight_request_keeps_the_ranked_hits_with_a_warning():
    store, requests = cluster(fail_highlight=True)
    try:
        hits = await OpenSearchBackend(store, "tuik-resources", FakeEmbedder()).search(
            "nüfus", SearchFilters(), 10
        )
    finally:
        await store.close()
    assert [request_kind(body) for body in requests] == ["hybrid", "highlight"]
    assert hits.ranking == "hybrid" and hits.hits[0].id == "press:57987"
    assert hits.hits[0].highlights == {} and hits.total == 1
    (warning,) = hits.warnings
    assert warning.startswith("Highlights are unavailable")


async def test_hybrid_without_lexical_matches_returns_no_semantic_neighbours():
    # k-NN always finds neighbours; with no lexical match they are not evidence.
    store, requests = cluster(lexical_matches=0)
    try:
        hits = await OpenSearchBackend(store, "tuik-resources", FakeEmbedder()).search(
            "xqzvkj", SearchFilters(), 10
        )
    finally:
        await store.close()
    assert ["hybrid" in body["query"] for body in requests] == [True]
    assert hits.ranking == "hybrid" and hits.hits == [] and hits.total == 0
    assert hits.warnings == [NO_LEXICAL_MATCH]
    assert hits.facets.kind.counts == hits.facets.theme.counts == {}


async def test_hybrid_response_without_the_lexical_count_falls_back_lexically():
    # A cluster that drops the aggregation must not silently skip the guard.
    store, requests = cluster()
    original = store.query

    async def query(index, body):
        response = await original(index, body)
        response["aggregations"].pop(LEXICAL_MATCHES_AGG, None)
        return response

    store.query = query
    try:
        hits = await OpenSearchBackend(store, "tuik-resources", FakeEmbedder()).search(
            "nüfus", SearchFilters(), 10
        )
    finally:
        await store.close()
    assert ["hybrid" in body["query"] for body in requests] == [True, False]
    assert hits.ranking == "lexical" and hits.hits
    assert any("Hybrid search failed" in w for w in hits.warnings)


async def test_failed_hybrid_request_retries_lexically_and_meta_is_cached():
    store, requests = cluster(fail_hybrid=True)
    backend = OpenSearchBackend(store, "tuik-resources", FakeEmbedder())
    calls = []
    original = store.meta

    async def meta(name):
        calls.append(name)
        return await original(name)

    store.meta = meta
    try:
        first = await backend.search("nüfus", SearchFilters(), 10)
        await backend.search("nüfus", SearchFilters(), 10)
    finally:
        await store.close()
    assert first.ranking == "lexical"
    assert any("Hybrid search failed" in w for w in first.warnings)
    assert ["hybrid" in body["query"] for body in requests] == [True, False, True, False]
    assert calls == ["tuik-resources"]


async def test_mixed_vector_models_and_no_embedder_stay_lexical():
    store, requests = cluster(models=[MODEL, "other/model"])
    try:
        several = await OpenSearchBackend(store, "a", FakeEmbedder()).search(
            "nüfus", SearchFilters(), 10
        )
        none = await OpenSearchBackend(store, "a").search("nüfus", SearchFilters(), 10)
    finally:
        await store.close()
    assert several.ranking == none.ranking == "lexical"
    assert several.warnings == none.warnings == []


class Store:
    """In-memory index store with vector-model metadata."""

    def __init__(self, model=None):
        self.created, self.written, self.model = [], [], model
        self.aliases, self.indices = {}, {}

    async def alias_targets(self, alias):
        return self.aliases.get(alias, [])

    async def exists(self, name):
        return False

    async def versions(self, alias):
        return {}

    async def create(self, name, embedding_model=None):
        self.created.append((name, embedding_model))
        self.indices[name] = {}

    async def bulk(self, index, payloads):
        self.written.extend(payloads)
        self.indices[index].update({p["id"]: p for p in payloads})
        return []

    async def refresh(self, index):
        pass

    async def count(self, index):
        return len(self.indices[index])

    async def swap(self, alias, index, previous):
        self.aliases[alias] = [index]

    async def delete(self, name):
        self.indices.pop(name, None)

    async def schema_version(self, index):
        return 3

    async def embedding_model(self, index):
        return self.model

    async def sources(self, index, ids):
        return {i: self.indices[index][i] for i in ids if i in self.indices[index]}

    async def ids(self, index):
        return set(self.indices[index])


async def test_rebuild_records_the_model_and_embeds_before_creating():
    payloads, _ = prepare(metadata_documents(), STAMP)
    store = Store()
    result = await rebuild(store, "tuik", payloads, embedder=FakeEmbedder())
    assert store.created == [("tuik-v1", MODEL)]
    assert result["embeddings"]["model"] == MODEL and result["embeddings"]["embedded"] == 2
    assert sum("embedding" in p for p in store.written) == 2

    class Broken(FakeEmbedder):
        def documents(self, texts):
            raise EmbeddingUnavailable("no model")

    store = Store()
    with pytest.raises(EmbeddingUnavailable):
        await rebuild(store, "tuik", payloads, embedder=Broken())
    assert store.created == []  # A missing model never leaves an empty build behind.


@pytest.mark.parametrize(
    ("served", "wanted", "message"),
    [
        (MODEL, None, "rerun with --embeddings"),
        (None, MODEL, "run a full build with --embeddings"),
        ("other/model", MODEL, "not intfloat"),
    ],
)
async def test_incremental_writes_never_mix_vector_models(served, wanted, message):
    store = Store(served)
    with pytest.raises(IndexerError, match=message):
        await check_vectors(store, "i", FakeEmbedder() if wanted else None)
    await check_vectors(Store(None), "i", None)
    await check_vectors(Store(MODEL), "i", FakeEmbedder())


async def test_incremental_embeds_only_new_or_changed_documents():
    payloads, _ = prepare(metadata_documents(), STAMP)
    store = Store(MODEL)
    store.aliases["tuik"] = ["tuik-v1"]
    old = [{**p, "embedding": [0.0] * DIMENSION} for p in payloads]
    old[1]["title"] = "Eski başlık"
    store.indices["tuik-v1"] = {p["id"]: p for p in old}
    assert comparable(old[0]) == comparable(payloads[0])
    embedder = FakeEmbedder()
    result = await update(store, "tuik", payloads, embedder=embedder)
    assert result["updated"] == 1 and result["unchanged"] == 2
    assert result["embeddings"]["embedded"] == 1
    assert [p["id"] for p in store.written] == ["press:57987"]


def test_index_cli_accepts_embeddings_flag(monkeypatch):
    from tuik_mcp.search import index_cli

    seen = {}

    async def run(args):
        seen["embeddings"] = args.embeddings
        return 0

    monkeypatch.setattr(index_cli, "run", run)
    assert index_cli.main(["--full", "--embeddings", "--report", "r.json"]) == 0
    assert seen == {"embeddings": True}


async def test_collector_runs_hybrid_variants_with_production_requests(tmp_path):
    suite = {
        "version": 1,
        "queries": [{"id": "q1", "text": "hayat pahalılığı", "category": "synonym"}],
        "judgments": {},
    }
    documents = metadata_documents()[:2]
    created, searches = {}, []

    def handler(request):
        path = request.url.path
        if path == "/":
            return httpx.Response(200, json={"version": {"number": "3.3.2"}})
        if path == "/_bulk":
            lines = [json.loads(line) for line in request.content.splitlines()]
            assert all("embedding" in line for line in lines[1::2])
            return httpx.Response(200, json={"items": [{"index": {"status": 201}}] * 2})
        index = path.split("/")[1]
        if request.method == "PUT":
            created[index] = json.loads(request.content)
        if path.endswith("/_count"):
            return httpx.Response(200, json={"count": 2})
        if path.endswith("/_search"):
            searches.append(json.loads(request.content))
            source = documents[1].model_dump(mode="json")
            return httpx.Response(
                200,
                json={
                    "hits": {
                        "total": {"value": 1, "relation": "eq"},
                        "hits": [{"_score": 0.03, "_source": source}],
                    }
                },
            )
        return httpx.Response(200, json={"acknowledged": True})

    store = OpenSearchStore("http://cluster", transport=httpx.MockTransport(handler))
    output = tmp_path / "hybrid.json"
    variants = tuple(evaluation.HYBRID_VARIANTS)
    try:
        code = await evaluation.collect_index(
            suite, documents, output, "unused", store=store, variants=variants,
            embedder=FakeEmbedder(),
        )  # fmt: skip
    finally:
        await store.close()
    assert code == 0
    report = evaluation.read_json(output)
    assert set(report["systems"]) == set(variants)
    assert report["embeddings"]["model"] == MODEL and report["embeddings"]["documents"] == 2
    assert {d["mappings"]["_meta"]["embedding_model"] for d in created.values()} == {MODEL}
    weights = [
        s["search_pipeline"]["phase_results_processors"][0]["score-ranker-processor"][
            "combination"
        ]["parameters"]["weights"]
        for s in searches
    ]
    assert weights == [list(evaluation.HYBRID_VARIANTS[v]) for v in variants]
    with pytest.raises(ValueError, match="embedder"):
        await evaluation.collect_index(suite, documents, output, "u", variants=variants)
    with pytest.raises(ValueError, match="Unknown"):
        await evaluation.collect_index(suite, documents, output, "u", variants=("nope",))


def test_settings_disable_semantic_search(monkeypatch):
    from tuik_mcp.clients.base import HttpClient
    from tuik_mcp.config import Settings
    from tuik_mcp.services.catalog import CatalogService

    monkeypatch.setenv("TUIK_OPENSEARCH_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("TUIK_EMBEDDING_CACHE_DIR", "")
    assert Settings.from_env().embedding_cache_dir is None
    assert CatalogService(HttpClient(Settings.from_env())).index.embedder is not None
    monkeypatch.setenv("TUIK_SEMANTIC_SEARCH", "false")
    assert CatalogService(HttpClient(Settings.from_env())).index.embedder is None


URL = os.getenv("TUIK_OPENSEARCH_URL")


@pytest.mark.opensearch
@pytest.mark.skipif(not URL, reason="Set TUIK_OPENSEARCH_URL")
async def test_real_cluster_hybrid_query_with_inline_rrf_pipeline():
    alias = "tuik-hybrid-test-" + uuid.uuid4().hex
    store = OpenSearchStore(URL)
    try:
        payloads, _ = prepare(metadata_documents(), STAMP)
        await rebuild(store, alias, payloads, embedder=FakeEmbedder())
        backend = OpenSearchBackend(store, alias, FakeEmbedder())
        hits = await backend.search("işsizlik", SearchFilters(), 10)
        assert hits.ranking == "hybrid" and hits.warnings == []
        assert {hit.id for hit in hits.hits} <= {"flow:DF_ISSIZ+V1.0", "press:57987"}
        # Highlights arrive through the second, lexical request.
        by_id = {hit.id: hit.highlights for hit in hits.hits}
        assert any("<em>" in text for text in by_id["flow:DF_ISSIZ+V1.0"]["title"])
        assert any("<em>" in text for text in by_id["press:57987"]["body"])
        assert hits.hits and hits.facets is not None
        nonsense = await backend.search("xqzvkj", SearchFilters(), 10)
        assert nonsense.ranking == "hybrid" and nonsense.hits == [] and nonsense.total == 0
        assert nonsense.warnings == [NO_LEXICAL_MATCH]
        filtered = await backend.search("işsizlik", SearchFilters(downloadable=True), 10)
        assert [hit.id for hit in filtered.hits] == ["flow:DF_ISSIZ+V1.0"]
        content = await backend.search("tablo", SearchFilters(kinds=["document_chunk"]), 10)
        assert content.ranking == "lexical"
        assert [hit.parent_id for hit in content.hits] == ["press:57987:file:isgucu~0123456789"]
    finally:
        try:
            for name in await store.versions(alias):
                await store.delete(name)
        finally:
            await store.close()


def test_report_namespace_matches_cli_defaults():
    # run() reads embeddings with getattr, so older Namespace callers stay compatible.
    assert getattr(Namespace(), "embeddings", False) is False
