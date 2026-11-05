"""Offline TÜİK adapter with a mock or real index for MCP search contract tests."""

import json
import sys
from pathlib import Path

import httpx

from tuik_mcp import server
from tuik_mcp.clients.base import HttpClient
from tuik_mcp.search.backend import OpenSearchBackend
from tuik_mcp.search.indexer import OpenSearchStore
from tuik_mcp.services.catalog import CatalogService

FIXTURES = Path(__file__).parent / "fixtures"
MODE = sys.argv[1]
STAMP = "2026-11-02T09:00:00.000Z"


def upstream(request):
    path = request.url.path
    if path.endswith("/file/csv"):
        return httpx.Response(
            200,
            content=(FIXTURES / "data.csv").read_bytes(),
            headers={"content-type": "text/csv"},
        )
    name = (
        "flows"
        if path == "/api/tr/dataflows"
        else "press"
        if path == "/api/tr/press/57987"
        else "unemployment"
        if path.endswith("/search")
        else None
    )
    if name is None:
        raise AssertionError(f"Unexpected upstream request: {request.url}")
    return httpx.Response(200, json=json.loads((FIXTURES / f"{name}.json").read_text()))


def mock_http(settings):
    if MODE != "cluster":
        settings.opensearch_url = None
    settings.min_interval_seconds = 0
    return HttpClient(settings, httpx.MockTransport(upstream))


def mock_cluster(request):
    query = json.loads(request.content)
    sources = [
        {
            "id": "flow:DF_ALTINSIZ_DTH+V1.0",
            "kind": "dataset",
            "title": "Dış Ticaret",
            "source_url": "https://databrowser2.tuik.gov.tr/",
        },
        {
            "id": "press:57987",
            "kind": "bulletin",
            "title": "İşgücü İstatistikleri",
            "source_url": "https://veriportali.tuik.gov.tr/tr/press/57987",
        },
        {
            "id": "press:57987:metadata",
            "kind": "metadata",
            "title": "İşgücü Metaveri",
            "source_url": "https://veriportali.tuik.gov.tr/tr/press/57987",
        },
    ]
    return httpx.Response(
        200,
        json={
            "hits": {
                "total": {"value": 3, "relation": "eq"},
                "hits": [
                    {
                        "_source": source | {"indexed_at": STAMP, "retrieved_at": STAMP},
                        "_score": 2.5,
                        "highlight": {"title": ["<em>İşgücü</em>"]},
                    }
                    for source in sources[: query["size"]]
                ],
            },
            "aggregations": {
                "kind": {
                    "buckets": [{"key": s["kind"], "doc_count": 1} for s in sources],
                    "sum_other_doc_count": 0,
                    "doc_count_error_upper_bound": 0,
                },
                "theme": {
                    "buckets": [],
                    "sum_other_doc_count": 0,
                    "doc_count_error_upper_bound": 0,
                },
                "indexed_at": {"value": 1793610000000, "value_as_string": STAMP},
            },
        },
    )


def fixture_service(http):
    service = CatalogService(http)
    if MODE == "mock-index":
        service.index = OpenSearchBackend(
            OpenSearchStore("http://cluster", transport=httpx.MockTransport(mock_cluster)),
            "fixture",
        )
    elif MODE == "missing-index":
        service.index = OpenSearchBackend(
            OpenSearchStore("http://127.0.0.1:1", timeout=1), "missing"
        )
    return service


if __name__ == "__main__":
    server.HttpClient = mock_http
    server.CatalogService = fixture_service
    server.main()
