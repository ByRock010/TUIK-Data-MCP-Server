"""SearchBackend: query building, fallbacks and an opt-in real-cluster check (#17)."""

import json
import os
import uuid
from datetime import UTC, date, datetime

import httpx
import pytest

from tuik_mcp.clients.base import HttpClient
from tuik_mcp.config import Settings
from tuik_mcp.search.backend import (
    HIGHLIGHT_MAX_OFFSET,
    BackendUnavailable,
    LiteralBackend,
    OpenSearchBackend,
    PortalBackend,
    SearchFilters,
    build_query,
)
from tuik_mcp.search.documents import IndexDocument
from tuik_mcp.search.indexer import OpenSearchStore, prepare, rebuild
from tuik_mcp.services.catalog import CatalogService

THEME = "İstihdam, İşsizlik ve Ücret"
RECORDS = [
    {"id": "DF_ISSIZ+V1.0", "name": "İşsizlik Oranı", "downloadable": True,
     "category": {"name": THEME}},
    {"id": "DF_TUFE+V1.0", "name": "Tüketici Fiyat Endeksi", "downloadable": True,
     "category": {"name": "Enflasyon ve Fiyat"}},
    {"id": "DF_KAPALI+V1.0", "name": "İşsizlik Kayıtları", "downloadable": False,
     "category": {"name": THEME}},
]  # fmt: skip


def hit(doc_id, kind="dataset", **extra):
    return {
        "_id": doc_id,
        "_score": 2.5,
        "_source": {
            "id": doc_id,
            "kind": kind,
            "title": "Başlık",
            "source_url": "https://veriportali.tuik.gov.tr/tr/press/1",
            "theme": [THEME],
            "downloadable": True,
            "formats": ["csv"],
            "indexed_at": "2026-10-29T00:00:00Z",
        },
        "highlight": {"title": ["<em>Başlık</em>"]},
        **extra,
    }


def cluster(responder, requests=None):
    def handler(request):
        if requests is not None:
            requests.append(request)
        return responder(request)

    return OpenSearchStore("http://cluster", transport=httpx.MockTransport(handler))


@pytest.fixture
async def service():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/tr/dataflows":
            return httpx.Response(200, json=RECORDS)
        raise AssertionError(f"Unexpected request: {request.url}")

    http = HttpClient(Settings(min_interval_seconds=0, attempts=1), httpx.MockTransport(handler))
    catalog = CatalogService(http)
    try:
        yield catalog, calls
    finally:
        await catalog.close()
        await http.close()


def test_query_matches_all_fields_exactly_and_typos_only_weakly():
    body = build_query("işsizliğin", SearchFilters(), limit=5, offset=10)
    exact, fuzzy = body["query"]["bool"]["should"]
    assert exact["multi_match"]["fields"] == [
        "title^4",
        "title.ascii",
        "description^2",
        "body",
        "theme",
    ]
    assert "fuzziness" not in exact["multi_match"]
    assert fuzzy["multi_match"]["fuzziness"] == "AUTO"
    # A typo must keep its first letter, so TÜFE never drifts to ÜFE.
    assert fuzzy["multi_match"]["prefix_length"] == 1 and fuzzy["multi_match"]["boost"] < 1
    assert body["query"]["bool"]["minimum_should_match"] == 1
    assert body["query"]["bool"]["filter"] == []
    assert (body["from"], body["size"], body["track_total_hits"]) == (10, 5, True)
    assert body["_source"]["excludes"] == ["body", "description"]
    assert body["highlight"]["max_analyzer_offset"] == HIGHLIGHT_MAX_OFFSET
    assert set(body["highlight"]["fields"]) == {"title", "title.ascii", "description", "body"}


def test_query_filters_and_paging_are_validated():
    filters = SearchFilters(
        kinds=["bulletin", "metadata"],
        themes=[THEME],
        downloadable=False,
        published_after=date(2026, 1, 1),
        published_before=date(2026, 12, 31),
    )
    assert build_query("x", filters, 10)["query"]["bool"]["filter"] == [
        {"terms": {"kind": ["bulletin", "metadata"]}},
        {"terms": {"theme": [THEME]}},
        {"term": {"downloadable": False}},
        {"range": {"publication_date": {"gte": "2026-01-01", "lte": "2026-12-31"}}},
    ]
    only_after = SearchFilters(published_after=date(2026, 5, 1))
    assert build_query("x", only_after, 1)["query"]["bool"]["filter"] == [
        {"range": {"publication_date": {"gte": "2026-05-01"}}}
    ]
    for limit, offset in [(0, 0), (51, 0), (True, 0), (10, -1), (10, 1001)]:
        with pytest.raises(ValueError):
            build_query("x", SearchFilters(), limit, offset)
    with pytest.raises(ValueError):
        SearchFilters(kinds=["table"])


async def test_opensearch_hits_keep_scores_highlights_and_provenance():
    requests = []
    response = {"hits": {"total": {"value": 7}, "hits": [hit("flow:DF_ISSIZ+V1.0")]}}
    store = cluster(lambda request: httpx.Response(200, json=response), requests)
    try:
        hits = await OpenSearchBackend(store, "tuik-resources").search(
            "issizlik", SearchFilters(kinds=["dataset"]), 3
        )
        assert hits.backend == "opensearch" and hits.total == 7 and hits.limit == 3
        (first,) = hits.hits
        assert (first.id, first.kind, first.score) == ("flow:DF_ISSIZ+V1.0", "dataset", 2.5)
        assert first.highlights == {"title": ["<em>Başlık</em>"]}
        assert first.indexed_at == "2026-10-29T00:00:00Z" and first.theme == [THEME]
        (request,) = requests
        assert request.url.path == "/tuik-resources/_search"
        assert json.loads(request.content)["size"] == 3
    finally:
        await store.close()


@pytest.mark.parametrize(
    "responder",
    [
        lambda r: httpx.Response(404, json={"error": "no such index [tuik-resources]"}),
        lambda r: httpx.Response(503, text="unavailable"),
        lambda r: httpx.Response(200, json={"unexpected": True}),
        "refuse",
    ],
)
async def test_cluster_failures_are_backend_unavailable(responder):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    store = cluster(refuse if responder == "refuse" else responder)
    try:
        with pytest.raises(BackendUnavailable):
            await OpenSearchBackend(store, "tuik-resources").search("x", SearchFilters(), 5)
    finally:
        await store.close()


async def test_dataset_search_without_configuration_falls_back_to_literal(service):
    catalog, _ = service
    assert catalog.index is None
    found = await catalog.search_datasets("işsizlik", theme="istihdam, işsizlik ve ücret")
    assert found.backend == "literal"
    assert [r.id for r in found.results] == ["flow:DF_ISSIZ+V1.0"]
    assert found.warnings == [
        "Indexed search is not configured (TUIK_OPENSEARCH_URL); used literal catalog matching."
    ]


async def test_unreachable_cluster_falls_back_for_datasets_and_general_search(catalog):
    # A refused local port: the real transport fails fast, without a cluster.
    service, calls = catalog
    service.index = OpenSearchBackend(OpenSearchStore("http://127.0.0.1:1", timeout=2), "x")
    try:
        found = await service.search_datasets("Altın Hariç Dış Ticaret Hadleri")
        assert found.backend == "literal" and found.results
        assert found.warnings[0].startswith("Indexed search is unavailable (")
        assert found.warnings[0].endswith("; used literal catalog matching.")
        general = await service.search_index("işsizlik", SearchFilters(themes=[THEME]), 3)
        assert general.backend == "portal" and len(general.hits) == 3
        assert general.warnings[0].endswith("; used live portal search.")
        assert "cannot apply these filters: theme" in general.warnings[1]
        assert any(path.endswith("/search") for path in (c.url.path for c in calls))
    finally:
        await service.close()


async def test_indexed_dataset_search_resolves_through_the_live_catalog(service):
    catalog, _ = service
    requests = []
    hits = [hit("flow:DF_ISSIZ+V1.0"), hit("flow:DF_REMOVED+V1.0")]
    response = {"hits": {"total": {"value": 2}, "hits": hits}}
    catalog.index = OpenSearchBackend(
        cluster(lambda r: httpx.Response(200, json=response), requests), "tuik-resources"
    )
    found = await catalog.search_datasets("issizlik", limit=5, theme="İSTİHDAM, İŞSİZLİK VE ÜCRET")
    # The total excludes the omitted stale hit, so truncation stays consistent.
    assert found.backend == "opensearch" and (found.total, found.truncated) == (1, False)
    assert [r.id for r in found.results] == ["flow:DF_ISSIZ+V1.0"]
    assert found.results[0].download_links  # Full catalog records, not index excerpts.
    assert found.warnings == [
        "1 indexed dataset(s) are no longer in the live catalog and were omitted."
    ]
    filters = json.loads(requests[0].content)["query"]["bool"]["filter"]
    # The theme resolves case-insensitively to the exact stored name.
    assert filters == [
        {"terms": {"kind": ["dataset"]}},
        {"terms": {"theme": [THEME]}},
        {"term": {"downloadable": True}},
    ]
    everything = await catalog.search_datasets("issizlik", downloadable_only=False)
    assert {"term": {"downloadable": True}} not in json.loads(requests[1].content)["query"]["bool"][
        "filter"
    ]
    assert everything.backend == "opensearch"


async def test_literal_backend_parity_and_limits(service):
    catalog, _ = service
    literal = LiteralBackend(catalog._datasets)
    all_hits = await literal.search("işsizlik", SearchFilters(kinds=["dataset"]), 1)
    assert all_hits.total == 2 and len(all_hits.hits) == 1
    first = await literal.search("işsizlik", SearchFilters(), 1)
    second = await literal.search("işsizlik", SearchFilters(), 1, offset=1)
    assert [h.id for h in first.hits + second.hits] == ["flow:DF_KAPALI+V1.0", "flow:DF_ISSIZ+V1.0"]
    assert first.hits[0].downloadable is False
    dated = await literal.search("işsizlik", SearchFilters(published_after=date(2026, 1, 1)), 5)
    assert dated.hits == [] and dated.total == 0 and dated.warnings
    with pytest.raises(ValueError, match="datasets only"):
        await literal.search("x", SearchFilters(kinds=["bulletin"]), 5)


async def test_portal_backend_filters_kinds_and_bounds_offsets(catalog):
    service, _ = catalog
    portal = PortalBackend(service.search)
    # The recorded page ranks 5 tables, 1 bulletin and 4 datasets.
    everything = await portal.search("işsizlik", SearchFilters(), 5)
    assert {h.kind for h in everything.hits} == {"table"} and everything.total == 553
    bulletins = await portal.search("işsizlik", SearchFilters(kinds=["bulletin"]), 5)
    assert [h.kind for h in bulletins.hits] == ["bulletin"]
    # The upstream total counts every kind, so a kind-filtered total is unknown.
    assert bulletins.total is None and bulletins.backend == "portal"
    datasets = await portal.search("işsizlik", SearchFilters(kinds=["dataset"]), 2, offset=1)
    assert [h.kind for h in datasets.hits] == ["dataset", "dataset"]
    with pytest.raises(ValueError, match="at most 50"):
        await portal.search("işsizlik", SearchFilters(), 10, offset=45)


URL = os.getenv("TUIK_OPENSEARCH_URL")
PRESS = "https://veriportali.tuik.gov.tr/tr/press/"
FLOW = "https://databrowser2.tuik.gov.tr/#/tr/tuik/TR,"


def documents():
    def bulletin(number, title, body, theme, published):
        return IndexDocument(
            id=f"press:{number}",
            kind="bulletin",
            title=title,
            body=body,
            theme=[theme],
            publication_date=published,
            source_url=PRESS + str(number),
            downloadable=True,
        )

    def dataset(code, title, theme, downloadable=True):
        return IndexDocument(
            id=f"flow:{code}+V1.0",
            kind="dataset",
            title=title,
            theme=[theme],
            downloadable=downloadable,
            formats=["csv", "json", "xml"] if downloadable else [],
            source_url=FLOW + code + ",1.0",
        )

    price = "Enflasyon ve Fiyat"
    return [
        bulletin(1, "Tüketici Fiyat Endeksi, Eylül 2026", "Tüketici fiyatları yıllık arttı.",
                 price, "2026-10-03"),
        bulletin(2, "Yurt İçi Üretici Fiyat Endeksi, Eylül 2026",
                 "Yurt içi üretici fiyatları yıllık arttı.", price, "2026-10-03"),
        bulletin(3, "İşgücü İstatistikleri, Ağustos 2026",
                 "İşsizlik oranı yüzde 8,5 oldu. " + "Ayrıntı. " * 20_000, THEME, "2026-09-30"),
        bulletin(4, "İşgücü İstatistikleri, Ağustos 2025", "İşsizlik oranı yüzde 8,8 oldu.",
                 THEME, "2025-09-30"),
        dataset("DF_ISSIZ", "İşsizlik Oranı", THEME),
        dataset("DF_KAPALI", "İşsizlik Kayıtları", THEME, downloadable=False),
        dataset("DF_NUFUS", "İllere Göre Nüfus", "Nüfus ve Demografi"),
    ]  # fmt: skip


@pytest.mark.opensearch
@pytest.mark.skipif(not URL, reason="Set TUIK_OPENSEARCH_URL")
async def test_turkish_queries_filters_and_highlights_against_a_real_cluster():
    alias = "tuik-backend-test-" + uuid.uuid4().hex
    store = OpenSearchStore(URL)
    backend = OpenSearchBackend(store, alias)

    async def ids(query, filters=None, limit=10):
        hits = await backend.search(query, filters or SearchFilters(), limit)
        assert hits.backend == "opensearch"
        return [h.id for h in hits.hits], hits

    try:
        payloads, _ = prepare(documents(), datetime(2026, 10, 29, tzinfo=UTC))
        await rebuild(store, alias, payloads)
        # ASCII typing, Turkish inflection and an abbreviation via the synonym graph.
        found, _ = await ids("issizlik")
        assert found[0] == "flow:DF_ISSIZ+V1.0" and "press:3" in found
        found, _ = await ids("işsizliğin")
        assert {"flow:DF_ISSIZ+V1.0", "press:3", "press:4"} <= set(found)
        found, _ = await ids("TÜFE")
        assert found == ["press:1"]  # ÜFE stays a distinct concept.
        found, _ = await ids("TUFE")
        assert found == ["press:1"]

        datasets = SearchFilters(kinds=["dataset"], downloadable=True)
        found, _ = await ids("işsizlik", datasets)
        assert found == ["flow:DF_ISSIZ+V1.0"]
        themed = SearchFilters(themes=[THEME], kinds=["bulletin"])
        found, _ = await ids("işsizlik", themed)
        assert set(found) == {"press:3", "press:4"}
        recent = SearchFilters(published_after=date(2026, 1, 1))
        found, _ = await ids("işsizlik", recent)
        assert found == ["press:3"]
        page, hits = await ids("işsizlik", SearchFilters(kinds=["bulletin"]), limit=1)
        assert len(page) == 1 and hits.total == 2

        _, hits = await ids("issizlik orani")
        top = hits.hits[0]
        assert any("<em>" in fragment for fragment in top.highlights.get("title.ascii", []))
        # Long bodies highlight within the bounded offset instead of failing.
        _, hits = await ids("işsizlik", SearchFilters(kinds=["bulletin"]))
        assert any("body" in h.highlights for h in hits.hits)
    finally:
        for name in await store.versions(alias):
            await store.delete(name)
        await store.close()


async def test_configured_url_builds_the_backend_and_service_close_releases_it():
    with pytest.raises(ValueError):
        Settings(opensearch_timeout_seconds=0)
    settings = Settings(
        opensearch_url="http://127.0.0.1:1", opensearch_index="custom", opensearch_timeout_seconds=1
    )
    http = HttpClient(settings)
    catalog = CatalogService(http)
    try:
        assert isinstance(catalog.index, OpenSearchBackend) and catalog.index.alias == "custom"
        assert catalog.index.store._client.timeout.connect == 1
    finally:
        await catalog.close()
        await http.close()
    assert catalog.index.store._client.is_closed


COMPLETE = {"timed_out": False, "_shards": {"total": 1, "successful": 1, "failed": 0}}


async def test_index_hits_are_rechecked_against_live_catalog_filters(service):
    catalog, _ = service
    # Indexed as downloadable and in THEME; since then DF_KAPALI lost its CSV export
    # and DF_TUFE moved to another theme in the live catalog.
    ids = ["flow:DF_KAPALI+V1.0", "flow:DF_TUFE+V1.0", "flow:DF_ISSIZ+V1.0"]
    response = COMPLETE | {
        "hits": {"total": {"value": 3, "relation": "eq"}, "hits": [hit(i) for i in ids]}
    }
    catalog.index = OpenSearchBackend(
        cluster(lambda r: httpx.Response(200, json=response)), "tuik-resources"
    )
    found = await catalog.search_datasets("işsizlik", limit=3, theme=THEME)
    assert [r.id for r in found.results] == ["flow:DF_ISSIZ+V1.0"]
    assert all("csv" in r.available_formats and r.theme == THEME for r in found.results)
    assert (found.total, found.truncated) == (1, False)
    assert found.warnings == [
        "2 indexed dataset(s) no longer match the live catalog's export or theme and were omitted."
    ]
    # Without filters, the record that lost its export is a valid result.
    everything = await catalog.search_datasets("işsizlik", limit=3, downloadable_only=False)
    assert [r.id for r in everything.results] == ids and not everything.warnings


@pytest.mark.parametrize(
    "incomplete",
    [
        {"timed_out": True},
        {"_shards": {"total": 2, "successful": 1, "failed": 1}},
        {"hits": {"total": {"value": 10_000, "relation": "gte"}, "hits": []}},
    ],
)
async def test_incomplete_search_responses_fall_back(service, incomplete):
    catalog, _ = service
    requests = []
    response = COMPLETE | {"hits": {"total": {"value": 0, "relation": "eq"}, "hits": []}}
    response |= incomplete
    catalog.index = OpenSearchBackend(
        cluster(lambda r: httpx.Response(200, json=response), requests), "tuik-resources"
    )
    found = await catalog.search_datasets("işsizlik")
    # An incomplete search is never reported as "no matches".
    assert found.backend == "literal" and [r.id for r in found.results] == ["flow:DF_ISSIZ+V1.0"]
    assert found.warnings[0].startswith("Indexed search is unavailable (")
    assert requests[0].url.params["allow_partial_search_results"] == "false"
