"""The general MCP search contract: metadata index, explicit portal, honest fallbacks."""

import json
from datetime import date

import httpx
import pytest

from tuik_mcp.models.search import IndexedSearchInput
from tuik_mcp.search.backend import OpenSearchBackend, SearchFilters, build_query
from tuik_mcp.search.indexer import OpenSearchStore

STAMP = "2026-11-02T09:00:00.000Z"
FACETS = {
    "kind": {
        "buckets": [{"key": "bulletin", "doc_count": 7}],
        "sum_other_doc_count": 0,
        "doc_count_error_upper_bound": 0,
    },
    "theme": {
        "buckets": [{"key": "İstihdam", "doc_count": 6}],
        "sum_other_doc_count": 1,
        "doc_count_error_upper_bound": 2,
    },
    "indexed_at": {"value": 1793610000000, "value_as_string": STAMP},
}


def response():
    return {
        "hits": {
            "total": {"value": 7, "relation": "eq"},
            "hits": [
                {
                    "_score": 5.0,
                    "_source": {
                        "id": "press:57987",
                        "kind": "bulletin",
                        "title": "İşgücü İstatistikleri",
                        "source_url": "https://veriportali.tuik.gov.tr/tr/press/57987",
                        "theme": ["İstihdam", "Ücret"],
                        "retrieved_at": "2026-10-29T12:00:00Z",
                        "indexed_at": STAMP,
                        "downloadable": True,
                        "formats": ["xls"],
                    },
                    "highlight": {"title": ["<em>İşgücü</em> İstatistikleri"]},
                }
            ],
        },
        "aggregations": FACETS,
    }


async def test_discovery_filters_and_full_match_facets_preserve_provenance(catalog):
    service, calls = catalog
    requests = []

    def cluster(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response())

    service.index = OpenSearchBackend(
        OpenSearchStore("http://cluster", transport=httpx.MockTransport(cluster)), "fixture"
    )
    try:
        result = await service.discover(
            " işsizliğin ",
            limit=1,
            kind="bulletin",
            theme="İstihdam",
            published_after="2026-01-01",
            published_before="2026-11-02",
            downloadable_only=True,
        )
        assert result.query == "işsizliğin" and result.source == "index"
        assert result.backend == "opensearch" and result.limit == 1
        assert result.total == 7 and result.truncated and not result.warnings
        assert result.indexed_at == STAMP
        first = result.results[0]
        assert (first.id, first.resource_type, first.score) == ("press:57987", "bulletin", 5)
        assert first.highlights == {"title": ["<em>İşgücü</em> İstatistikleri"]}
        assert first.themes == ["İstihdam", "Ücret"] and first.theme == "İstihdam"
        assert first.retrieved_at.isoformat() == "2026-10-29T12:00:00+00:00"
        assert first.indexed_at == STAMP and first.formats == ["xls"] and first.downloadable
        assert result.facets.kind.counts == {"bulletin": 7}
        assert result.facets.theme.counts == {"İstihdam": 6}
        assert result.facets.theme.truncated and result.facets.theme.count_error_upper_bound == 2
        assert not calls  # No live lookup of every hit, and no eager catalog dependency.
        assert requests[0]["query"]["bool"]["filter"] == [
            {"terms": {"kind": ["bulletin"]}},
            {"terms": {"theme": ["İstihdam"]}},
            {"term": {"downloadable": True}},
            {"range": {"publication_date": {"gte": "2026-01-01", "lte": "2026-11-02"}}},
        ]
    finally:
        await service.close()


async def test_general_and_bulletin_fallback_totals_are_honest(catalog):
    service, _ = catalog
    general = await service.discover("işsizlik", limit=2)
    assert general.source == "portal" and general.backend == "portal"
    assert general.total == 553 and general.truncated and len(general.results) == 2
    # Existing portal description and result fields remain available.
    assert (
        general.results[0].description
        == (await service.search("işsizlik", 2)).results[0].description
    )
    assert general.facets is None and general.indexed_at is None
    assert "not configured" in general.warnings[0]
    filtered = await service.discover(
        "işsizlik",
        kind="bulletin",
        theme="İstihdam",
        published_after="2026-01-01",
        downloadable_only=True,
    )
    assert filtered.total is None and filtered.truncated is None
    assert all(r.resource_type == "bulletin" for r in filtered.results)
    assert "theme, downloadable, published_after" in filtered.warnings[1]


async def test_dataset_only_fallback_uses_literal_and_explicit_live_skips_index(catalog):
    service, _ = catalog
    dataset = await service.discover("Altın Hariç Dış Ticaret Hadleri", kind="dataset")
    assert dataset.source == "catalog" and dataset.backend == "literal"
    assert dataset.results[0].id.startswith("flow:")

    # A configured but broken index must not be contacted for explicit portal search.
    def unexpected(request):
        raise AssertionError("Explicit live search contacted the index")

    service.index = OpenSearchBackend(
        OpenSearchStore("http://cluster", transport=httpx.MockTransport(unexpected)), "fixture"
    )
    try:
        live = await service.discover("işsizlik", live=True)
        assert live.source == "portal" and len(live.results) == 10
        assert not any("fallback" in w or "unavailable (" in w for w in live.warnings)
    finally:
        await service.close()


@pytest.mark.parametrize("failure", ["missing", "timeout", "malformed_facets"])
async def test_failed_index_falls_back_through_the_general_tool(catalog, failure):
    service, _ = catalog

    def cluster(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if failure == "missing":
            return httpx.Response(404, json={"error": "index_not_found_exception"})
        return httpx.Response(200, json=response() | {"aggregations": {"kind": {}}})

    service.index = OpenSearchBackend(
        OpenSearchStore("http://cluster", transport=httpx.MockTransport(cluster)), "missing"
    )
    try:
        result = await service.discover("işsizlik", kind="bulletin")
        assert result.source == "portal" and result.results
        assert result.facets is None and result.indexed_at is None
        assert "unavailable" in result.warnings[0] and "live portal" in result.warnings[0]
    finally:
        await service.close()


@pytest.mark.parametrize(
    "arguments",
    [
        {"query": " "},
        {"query": "x\nx"},
        {"limit": 0},
        {"limit": True},
        {"kind": "publication"},
        {"theme": " "},
        {"published_after": "2026-02-30"},
        {"published_before": "20261102"},
        {"published_after": 1793577600},
        {"published_before": "2026-11-02T01:00:00Z"},
        {"published_after": "2026-11-02", "published_before": "2026-01-01"},
    ],
)
async def test_invalid_discovery_arguments_fail_before_any_network(catalog, arguments):
    service, calls = catalog
    with pytest.raises(ValueError):
        await service.discover(**({"query": "işsizlik"} | arguments))
    assert not calls


def test_date_ranges_and_bounded_facets():
    with pytest.raises(ValueError):
        SearchFilters(published_after=date(2026, 2, 1), published_before=date(2026, 1, 1))
    assert IndexedSearchInput(query="x", published_after="2026-11-02").published_after == date(
        2026, 11, 2
    )
    aggs = build_query("x", SearchFilters(), 1)["aggs"]
    assert aggs["kind"]["terms"] == {"field": "kind", "size": 5}
    assert aggs["theme"]["terms"] == {"field": "theme", "size": 50}


async def test_theme_filter_without_results_says_how_themes_match(catalog):
    service, _ = catalog
    empty = response()
    empty["hits"] = {"total": {"value": 0, "relation": "eq"}, "hits": []}
    service.index = OpenSearchBackend(
        OpenSearchStore(
            "http://cluster",
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=empty)),
        ),
        "fixture",
    )
    try:
        wrong = await service.discover("nüfus", theme="nufus")
        assert wrong.total == 0 and "list_tuik_themes" in wrong.warnings[-1]
        assert "'nufus'" in wrong.warnings[-1]
        plain = await service.discover("nüfus")
        assert plain.total == 0 and plain.warnings == []
    finally:
        await service.close()
