"""Bulletin-series grouping in indexed search (#78)."""

import importlib.util
import json
import math
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from tuik_mcp.search.backend import (
    GROUP_WINDOW,
    HYBRID_DEPTH,
    LEXICAL_MATCHES_AGG,
    MAX_GROUPED,
    SERIES_AGG,
    LiteralBackend,
    OpenSearchBackend,
    PortalBackend,
    SearchFilters,
    build_followup_query,
    groupable,
    regroup,
    series_key,
)
from tuik_mcp.search.documents import IndexDocument
from tuik_mcp.search.embeddings import DIMENSION, MODEL
from tuik_mcp.search.indexer import IndexerError, OpenSearchStore, prepare, rebuild

STAMP = datetime(2026, 11, 19, 9, tzinfo=UTC)
LABOUR, YOUTH = "series:press:100", "series:press:200"
URL = "https://veriportali.tuik.gov.tr/tr/press/"


def source(doc_id, kind, series=None, date=None):
    value = {
        "id": doc_id,
        "kind": kind,
        "title": f"Başlık {doc_id}",
        "source_url": URL + doc_id.split(":")[-1],
    }
    if series:
        value["series_id"] = series
    if date:
        value["publication_date"] = date
    return value


# A document ranking as the cluster would return it: three labour releases (one with
# its metadata), a dataset, a youth release, a passage of a labour file.
RANKING = [
    source("press:103", "bulletin", LABOUR, "2026-03-01"),
    source("press:103:metadata", "metadata", LABOUR, "2026-03-01"),
    source("flow:DF_ISGUCU+V1.0", "dataset"),
    source("press:102", "bulletin", LABOUR, "2026-02-01"),
    source("press:201", "bulletin", YOUTH, "2025-05-01"),
    source("press:101", "bulletin", LABOUR, "2026-01-01"),
]
LATEST = {
    LABOUR: (3, source("press:104", "bulletin", None, "2026-04-01")),
    YOUTH: (1, source("press:201", "bulletin", None, "2025-05-01")),
}


def facets():
    return {
        name: {"buckets": [], "sum_other_doc_count": 0, "doc_count_error_upper_bound": 0}
        for name in ("kind", "theme")
    }


def cluster(ranking=RANKING, total=None, model=None, fail_followup=False, matches=1):
    requests = []

    def handler(request):
        if request.url.path.endswith("/_mapping"):
            meta = {"schema_version": 3, "embedding_model": model}
            return httpx.Response(200, json={"tuik-v1": {"mappings": {"_meta": meta}}})
        body = json.loads(request.content)
        requests.append(body)
        if "ids" in json.dumps(body["query"]):
            if fail_followup:
                return httpx.Response(500, json={"error": "follow-up failed"})
            ids = body["query"]["bool"]["filter"][0]["ids"]["values"]
            response = {
                "hits": {
                    "hits": [{"_id": i, "highlight": {"title": [f"<em>{i}</em>"]}} for i in ids]
                }
            }
            if "aggs" in body:
                wanted = body["aggs"][SERIES_AGG]["aggs"]["releases"]["filter"]["bool"]["filter"]
                keys = wanted[0]["terms"]["series_id"]
                response["aggregations"] = {
                    SERIES_AGG: {
                        "releases": {
                            "by_series": {
                                "buckets": [
                                    {
                                        "key": key,
                                        "doc_count": LATEST[key][0],
                                        "latest": {"hits": {"hits": [{"_source": LATEST[key][1]}]}},
                                    }
                                    for key in keys
                                    if key in LATEST
                                ]
                            }
                        }
                    }
                }
            return httpx.Response(200, json=response)
        size, start = body["size"], body["from"]
        aggregations = facets()
        if "hybrid" in body["query"]:
            aggregations[LEXICAL_MATCHES_AGG] = {"doc_count": matches}
        return httpx.Response(
            200,
            json={
                "hits": {
                    "total": {"value": total or len(ranking), "relation": "eq"},
                    "hits": [
                        {"_score": 10.0 - rank, "_source": item}
                        | ({"highlight": {"title": ["x"]}} if "highlight" in body else {})
                        for rank, item in enumerate(ranking[start : start + size])
                    ],
                },
                "aggregations": aggregations,
            },
        )

    return OpenSearchStore("http://cluster", transport=httpx.MockTransport(handler)), requests


class Embedder:
    model = MODEL

    def query(self, text):
        return [0.1] * DIMENSION


def test_series_key_groups_bulletins_with_their_metadata_only():
    assert series_key(source("press:1", "bulletin", LABOUR)) == LABOUR
    assert series_key(source("press:1:metadata", "metadata", LABOUR)) == LABOUR
    # Datasets, file content and documents without a series are their own group.
    assert series_key(source("flow:X", "dataset")) == "flow:X"
    assert series_key(source("press:1", "bulletin")) == "press:1"
    chunk = source("press:1:file:a~0123456789#p1.1", "document_chunk", LABOUR)
    assert series_key(chunk) == chunk["id"]


def test_regroup_keeps_the_best_ranked_release_and_counts_the_rest():
    items = [{"_source": value} for value in RANKING]
    groups = [(item["_source"]["id"], folded) for item, folded in regroup(items)]
    assert groups == [("press:103", 3), ("flow:DF_ISGUCU+V1.0", 0), ("press:201", 0)]


def test_groupable_scope():
    assert groupable(SearchFilters())
    assert groupable(SearchFilters(kinds=["bulletin"]))
    assert groupable(SearchFilters(kinds=["dataset", "metadata"]))
    assert not groupable(SearchFilters(kinds=["dataset"]))
    assert not groupable(SearchFilters(kinds=["table", "document_chunk"]))
    assert not groupable(SearchFilters(series_ids=[LABOUR]))


def test_followup_counts_bulletin_releases_globally_and_finds_the_newest():
    body = build_followup_query("işgücü", ["press:103"], [LABOUR])
    assert body["query"]["bool"]["filter"] == [{"ids": {"values": ["press:103"]}}]
    series = body["aggs"][SERIES_AGG]
    assert series["global"] == {}
    releases = series["aggs"]["releases"]
    assert releases["filter"]["bool"]["filter"] == [
        {"terms": {"series_id": [LABOUR]}},
        {"term": {"kind": "bulletin"}},
    ]
    latest = releases["aggs"]["by_series"]["aggs"]["latest"]["top_hits"]
    assert latest["size"] == 1
    assert latest["sort"][0]["publication_date"]["order"] == "desc"
    assert "aggs" not in build_followup_query("işgücü", ["flow:X"], [])


async def test_grouped_lexical_search_pages_over_series():
    store, requests = cluster()
    try:
        hits = await OpenSearchBackend(store, "tuik").search(
            "işgücü", SearchFilters(), 2, 0, group=True
        )
        second = await OpenSearchBackend(store, "tuik").search(
            "işgücü", SearchFilters(), 2, 2, group=True
        )
    finally:
        await store.close()
    window = requests[0]
    assert window["size"] == GROUP_WINDOW and window["from"] == 0
    assert "highlight" not in window
    assert [hit.id for hit in hits.hits] == ["press:103", "flow:DF_ISGUCU+V1.0"]
    # Totals and facets still count documents.
    assert hits.total == len(RANKING) and hits.ranking == "lexical" and hits.warnings == []
    labour, dataset = hits.hits
    assert labour.series_id == LABOUR and labour.highlights == {"title": ["<em>press:103</em>"]}
    assert labour.series.collapsed == 3 and labour.series.releases == 3
    assert labour.series.latest.id == "press:104"
    assert labour.series.latest.publication_date == "2026-04-01"
    assert dataset.series_id is None and dataset.series is None
    followup = requests[1]
    assert followup["query"]["bool"]["filter"] == [
        {"ids": {"values": ["press:103", "flow:DF_ISGUCU+V1.0"]}}
    ]
    keys = followup["aggs"][SERIES_AGG]["aggs"]["releases"]["filter"]["bool"]["filter"][0]
    assert keys == {"terms": {"series_id": [LABOUR]}}
    assert [hit.id for hit in second.hits] == ["press:201"]
    assert second.hits[0].series.collapsed == 0 and second.hits[0].series.releases == 1


async def test_ungrouped_search_is_unchanged_and_reports_the_series_id():
    store, requests = cluster()
    try:
        hits = await OpenSearchBackend(store, "tuik").search("işgücü", SearchFilters(), 3)
    finally:
        await store.close()
    assert len(requests) == 1 and requests[0]["size"] == 3 and "highlight" in requests[0]
    assert [hit.id for hit in hits.hits] == [item["id"] for item in RANKING[:3]]
    assert [hit.series_id for hit in hits.hits] == [LABOUR, LABOUR, None]
    assert all(hit.series is None for hit in hits.hits)


async def test_grouped_hybrid_fuses_over_the_window():
    store, requests = cluster(model=MODEL)
    try:
        backend = OpenSearchBackend(store, "tuik", Embedder())
        grouped = await backend.search("işgücü durumu", SearchFilters(), 10, group=True)
        plain = await backend.search("işgücü durumu", SearchFilters(), 10)
    finally:
        await store.close()
    hybrid = requests[0]["query"]["hybrid"]
    assert hybrid["pagination_depth"] == GROUP_WINDOW
    assert hybrid["queries"][1]["knn"]["embedding"]["k"] == GROUP_WINDOW
    assert requests[0]["size"] == GROUP_WINDOW
    assert grouped.ranking == "hybrid"
    assert [hit.id for hit in grouped.hits] == ["press:103", "flow:DF_ISGUCU+V1.0", "press:201"]
    assert grouped.hits[0].series.collapsed == 3
    # Without grouping the evaluated #21 depth and page stay unchanged.
    ungrouped = requests[2]["query"]["hybrid"]
    assert ungrouped["pagination_depth"] == HYBRID_DEPTH and requests[2]["size"] == 10
    assert len(plain.hits) == 6


async def test_grouped_abbreviation_keeps_the_stable_lexical_order():
    store, requests = cluster(model=MODEL)
    try:
        hits = await OpenSearchBackend(store, "tuik", Embedder()).search(
            "TÜFE", SearchFilters(), 10, group=True
        )
    finally:
        await store.close()
    assert hits.ranking == "lexical"
    assert requests[0]["sort"] == [{"_score": "desc"}, {"id": "asc"}]
    assert requests[0]["size"] == GROUP_WINDOW
    assert any("abbreviation" in warning for warning in hits.warnings)


async def test_pages_beyond_the_grouped_range_list_releases_separately():
    store, requests = cluster()
    try:
        hits = await OpenSearchBackend(store, "tuik").search(
            "işgücü", SearchFilters(), 10, MAX_GROUPED - 5, group=True
        )
    finally:
        await store.close()
    assert requests[0]["size"] == 10 and requests[0]["from"] == MAX_GROUPED - 5
    assert any("Series grouping covers the first 50" in w for w in hits.warnings)
    assert all(hit.series is None for hit in hits.hits)


@pytest.mark.parametrize(
    "filters",
    [SearchFilters(kinds=["dataset"]), SearchFilters(series_ids=[LABOUR])],
)
async def test_datasets_and_series_listings_are_not_grouped(filters):
    store, requests = cluster()
    try:
        hits = await OpenSearchBackend(store, "tuik").search("işgücü", filters, 5, group=True)
    finally:
        await store.close()
    assert len(requests) == 1 and requests[0]["size"] == 5
    assert all(hit.series is None for hit in hits.hits)
    if filters.series_ids:
        assert {"terms": {"series_id": [LABOUR]}} in requests[0]["query"]["bool"]["filter"]


async def test_a_window_with_few_series_returns_a_shorter_page_with_a_warning():
    store, _ = cluster(total=500)
    try:
        hits = await OpenSearchBackend(store, "tuik").search(
            "işgücü", SearchFilters(), 10, group=True
        )
    finally:
        await store.close()
    assert len(hits.hits) == 3 and hits.total == 500
    (warning,) = hits.warnings
    assert "first 6 ranked documents" in warning and "3 distinct results" in warning


async def test_failed_followup_keeps_grouped_hits_without_details():
    store, _ = cluster(fail_followup=True)
    try:
        hits = await OpenSearchBackend(store, "tuik").search(
            "işgücü", SearchFilters(), 10, group=True
        )
    finally:
        await store.close()
    assert [hit.id for hit in hits.hits] == ["press:103", "flow:DF_ISGUCU+V1.0", "press:201"]
    labour = hits.hits[0]
    assert labour.highlights == {} and labour.series.collapsed == 3
    assert labour.series.releases is None and labour.series.latest is None
    (warning,) = hits.warnings
    assert warning.startswith("Highlights and series details are unavailable")


async def test_fallback_backends_accept_grouping_and_report_the_series_filter():
    async def datasets():
        return STAMP, []

    literal = await LiteralBackend(datasets).search(
        "işgücü", SearchFilters(kinds=["dataset"], series_ids=[LABOUR]), 5, group=True
    )
    assert literal.hits == [] and "series filter matched none" in literal.warnings[0]

    class Response:
        results, total, warnings = [], 0, []

    async def portal(query, limit):
        return Response()

    live = await PortalBackend(portal).search(
        "işgücü", SearchFilters(series_ids=[LABOUR]), 5, group=True
    )
    assert "cannot apply these filters: series" in live.warnings[0]


def documents():
    def bulletin(number, month, series):
        return IndexDocument(
            id=f"press:{number}",
            kind="bulletin",
            title=f"İşgücü İstatistikleri, {month} 2026",
            body="İşsizlik oranı açıklandı.",
            series_id=series,
            publication_date=f"2026-{number % 100:02d}-15",
            source_url=URL + str(number),
        )

    labour = [
        bulletin(100 + month, name, LABOUR)
        for month, name in ((1, "Ocak"), (2, "Şubat"), (3, "Mart"))
    ]
    metadata = IndexDocument(
        id="press:103:metadata",
        kind="metadata",
        title="İşgücü İstatistikleri, Mart 2026",
        series_id=LABOUR,
        parent_id="press:103",
        source_url=URL + "103",
    )
    youth = IndexDocument(
        id="press:201",
        kind="bulletin",
        title="İşgücü Piyasasında Gençler, 2025",
        series_id=YOUTH,
        publication_date="2026-05-01",
        source_url=URL + "201",
    )
    dataset = IndexDocument(
        id="flow:DF_ISGUCU+V1.0",
        kind="dataset",
        title="Temel İşgücü Göstergeleri",
        source_url="https://databrowser2.tuik.gov.tr/",
    )
    return [*labour, metadata, youth, dataset]


OPENSEARCH = os.getenv("TUIK_OPENSEARCH_URL")


@pytest.mark.opensearch
@pytest.mark.skipif(not OPENSEARCH, reason="Set TUIK_OPENSEARCH_URL")
async def test_real_cluster_groups_series_and_reports_the_newest_release():
    alias = "tuik-grouping-test-" + uuid.uuid4().hex
    store = OpenSearchStore(OPENSEARCH)
    try:
        payloads, _ = prepare(documents(), STAMP)
        await rebuild(store, alias, payloads)
        backend = OpenSearchBackend(store, alias)
        plain = await backend.search("işgücü", SearchFilters(), 10)
        grouped = await backend.search("işgücü", SearchFilters(), 10, group=True)
        assert plain.total == grouped.total == 6
        assert len(plain.hits) == 6
        # One result per series in the order of each series' best document.
        order = []
        for hit in plain.hits:
            key = hit.series_id or hit.id
            if key not in order:
                order.append(key)
        assert [hit.series_id or hit.id for hit in grouped.hits] == order
        labour = next(hit for hit in grouped.hits if hit.series_id == LABOUR)
        assert labour.series.collapsed == 3 and labour.series.releases == 3
        assert labour.series.latest.id == "press:103"
        assert labour.highlights
        listed = await backend.search("işgücü", SearchFilters(series_ids=[LABOUR]), 10, group=True)
        assert {hit.id for hit in listed.hits} == {
            "press:101",
            "press:102",
            "press:103",
            "press:103:metadata",
        }
    finally:
        try:
            for name in await store.versions(alias):
                await store.delete(name)
        finally:
            await store.close()


spec = importlib.util.spec_from_file_location(
    "evaluate_search", Path(__file__).parents[1] / "scripts" / "evaluate_search.py"
)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def test_series_collapsed_metric_scores_each_series_once():
    series = {"press:1": LABOUR, "press:1:metadata": LABOUR, "press:2": LABOUR}
    grades = {"press:1": 2, "press:1:metadata": 2, "press:2": 2, "flow:X": 1, "press:9": 0}
    repeated = [{"id": "press:1"}, {"id": "press:1:metadata"}, {"id": "press:2"}]
    row = evaluation.series_collapsed_metrics(repeated, grades, series)
    # Ideal: one labour series (2) then the dataset (1); the repeats score nothing.
    ideal = 3 + 1 / math.log2(3)
    assert row["repeated_series_slots"] == 2
    assert row["series_collapsed_ndcg_at_10"] == pytest.approx(3 / ideal)
    diverse = [{"id": "press:2"}, {"id": "flow:X"}, {"id": "press:9"}]
    row = evaluation.series_collapsed_metrics(diverse, grades, series)
    assert row == {"series_collapsed_ndcg_at_10": pytest.approx(1.0), "repeated_series_slots": 0}
    corpus = documents()
    assert evaluation.series_map(corpus) == {
        "press:101": LABOUR,
        "press:102": LABOUR,
        "press:103": LABOUR,
        "press:103:metadata": LABOUR,
        "press:201": YOUTH,
    }


class VectorEmbedder:
    """Deterministic vectors for a real-cluster run without the ONNX runtime."""

    model = MODEL
    dimension = DIMENSION

    @staticmethod
    def vector(text):
        seed = sum(map(ord, text)) % 97 + 1
        return [((seed * (i + 1)) % 13) / 13 + 0.01 for i in range(DIMENSION)]

    def documents(self, texts):
        return [self.vector(text) for text in texts]

    def query(self, text):
        return self.vector(text)


@pytest.mark.opensearch
@pytest.mark.skipif(not OPENSEARCH, reason="Set TUIK_OPENSEARCH_URL")
async def test_production_collector_records_grouped_and_plain_served_rankings(tmp_path):
    suite = {
        "version": 1,
        "queries": [
            {"id": "q1", "text": "işgücü istatistikleri", "category": "exact"},
            {"id": "q2", "text": "TÜFE", "category": "abbreviation"},
        ],
        "judgments": {},
    }
    output = tmp_path / "production.json"
    store = OpenSearchStore(OPENSEARCH)
    try:
        code = await evaluation.collect_production(
            suite, documents(), output, OPENSEARCH, embedder=VectorEmbedder(), store=store
        )
        report = evaluation.read_json(output)
        assert code == 0, report["errors"]
        index = report["systems"]["production_grouped"]["index"]
        assert report["cleanup"] == {index: "deleted"}
        assert not await store.exists(index)
    finally:
        await store.close()
    assert report["method"] == "production_backend_search"
    assert report["settings"]["group_window"] == GROUP_WINDOW
    assert report["embeddings"]["documents"] == 6
    grouped = report["systems"]["production_grouped"]
    plain = report["systems"]["production_ungrouped"]
    assert grouped["group"] is True and plain["group"] is False
    assert grouped["routing"]["q1"]["ranking"] == "hybrid"
    assert grouped["routing"]["q2"]["ranking"] == "lexical"
    series = evaluation.series_map(documents())
    for qid in ("q1",):
        keys = [series.get(hit["id"], hit["id"]) for hit in grouped["results"][qid]]
        assert len(keys) == len(set(keys))
        assert len(plain["results"][qid]) > len(grouped["results"][qid])
    labour = next(s for s in grouped["series"]["q1"] if s["series_id"] == LABOUR)
    assert labour["collapsed"] == 3 and labour["latest"]["id"] == "press:103"


class FailingDeleteStore(OpenSearchStore):
    """A real store whose disposable-index DELETE fails once, as on a timeout."""

    def __init__(self, url):
        super().__init__(url)
        self.left = []

    async def delete(self, name):
        self.left.append(name)
        raise IndexerError("Injected DELETE failure: index remains on cluster")


@pytest.mark.opensearch
@pytest.mark.skipif(not OPENSEARCH, reason="Set TUIK_OPENSEARCH_URL")
async def test_production_collector_fails_when_the_disposable_index_is_not_deleted(tmp_path):
    suite = {
        "version": 1,
        "queries": [{"id": "q1", "text": "işgücü istatistikleri", "category": "exact"}],
        "judgments": {},
    }
    output = tmp_path / "production.json"
    store = FailingDeleteStore(OPENSEARCH)
    try:
        code = await evaluation.collect_production(
            suite, documents(), output, OPENSEARCH, embedder=VectorEmbedder(), store=store
        )
        report = evaluation.read_json(output)
        (index,) = store.left
        assert await store.exists(index)
    finally:
        for name in store.left:
            await OpenSearchStore.delete(store, name)
        await store.close()
    assert code == 1 and report["status"] == "error"
    assert "Injected DELETE failure" in report["cleanup"][index]
    assert index in report["errors"]["cleanup"]["message"]
    # Rankings stay for diagnosis, but the report cannot be scored as a complete run.
    assert report["systems"]["production_grouped"]["results"]["q1"]
    with pytest.raises(ValueError, match="Incomplete run"):
        evaluation.validate_run(report, suite)
