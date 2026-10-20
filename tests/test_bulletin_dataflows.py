import asyncio
import copy
import json
from pathlib import Path

import httpx
import pytest

from tuik_mcp.clients.base import HttpClient, SourceError
from tuik_mcp.clients.tuik_portal import TuikPortal
from tuik_mcp.config import Settings
from tuik_mcp.server import respond
from tuik_mcp.services.catalog import CatalogService

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
async def bulletin():
    payload = json.loads((FIXTURES / "press_dataflows.json").read_text())
    # Retain one real downloadable catalog identity and one unavailable identity.
    entries = payload["data"]["statisticalTables"]
    flows = [
        {
            "id": "DF_SATIS_SEKLI_SATIS_DURUMU_V3+V1.0",
            "name": entries[0]["title"],
            "downloadable": True,
        },
        {"id": "DF_SATIS_SEKLI_DURUMU_ILILCE_V3+V1.0", "name": entries[1]["title"]},
    ]
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/api/tr/press/58347":
            return httpx.Response(200, json=payload)
        if request.url.path == "/api/tr/dataflows":
            return httpx.Response(200, json=flows)
        if request.url.path.endswith("/file/csv"):
            return httpx.Response(
                200,
                content=(FIXTURES / "data.csv").read_bytes(),
                headers={"content-type": "text/csv"},
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    http = HttpClient(Settings(min_interval_seconds=0), httpx.MockTransport(handler))
    try:
        yield CatalogService(http), calls, payload, flows, http
    finally:
        await http.close()


async def test_real_bulletin_links_catalog_and_csv_in_fresh_session(bulletin):
    service, calls, _, _, http = bulletin
    resource = await service.resource("press:58347")
    assert resource.download_links == [] and len(resource.related_datasets) == 8
    first, unavailable, missing = resource.related_datasets[:3]
    assert first.id == "flow:DF_SATIS_SEKLI_SATIS_DURUMU_V3+V1.0"
    assert first.category_path == ["9", "9_4"]
    assert first.downloadable and first.available_formats == ["csv", "json", "xml"]
    assert not unavailable.downloadable and unavailable.available_formats == []
    assert not missing.downloadable and missing.available_formats == []
    assert first.publisher and first.retrieved_at and first.source_url
    response = await respond(service.data("press:58347"))
    assert response["status"] == "related_datasets"
    assert response["related_datasets"][0]["id"] == first.id
    # Discovery must never download all of the bulletin's files.
    assert [call.url.path for call in calls] == ["/api/tr/press/58347", "/api/tr/dataflows"]
    data = await CatalogService(http).data(first.id, limit=1)
    assert data.status == "ok" and len(data.rows) == 1 and data.truncated
    assert data.source_url.endswith("/file/csv")


async def test_filters_are_not_silently_applied_to_arbitrary_related_dataset(bulletin):
    service, calls, _, _, _ = bulletin
    data = await service.data("press:58347", filters={"Yıl": "2025"})
    assert data.status == "unsupported_filters" and data.related_datasets
    assert data.rows == [] and len(calls) == 2


async def test_both_lists_deduplicate_and_retain_category_path(bulletin):
    service, _, payload, _, _ = bulletin
    entry = copy.deepcopy(payload["data"]["statisticalTables"][0])
    entry["url"] = entry["url"].replace("categories/9/9_4/", "")
    payload["data"]["tables"] = [entry]
    resource = await service.resource("press:58347")
    assert len(resource.related_datasets) == 8
    assert resource.related_datasets[0].category_path == ["9", "9_4"]


async def test_existing_bulletin_keeps_download_links_and_unavailable_dataset_references(catalog):
    service, calls = catalog
    # The existing bulletin fixture has real links absent from the tiny catalog.
    resource = await service.resource("press:57987")
    assert len(resource.related_datasets) == 3
    assert all(not item.downloadable for item in resource.related_datasets)
    assert resource.download_links
    data = await service.data(resource.id)
    assert data.status == "download_available"
    assert data.download_links[0].verified and data.download_links[0].format == "xls"
    assert data.related_datasets == resource.related_datasets


async def test_no_dataflow_links_does_not_fetch_catalog(bulletin):
    service, calls, payload, _, _ = bulletin
    payload["data"]["statisticalTables"] = []
    data = await service.data("press:58347")
    assert data.status == "not_structured" and data.related_datasets == []
    assert len(calls) == 1


async def test_malformed_links_do_not_hide_valid_bulletin_datasets(bulletin):
    service, _, payload, _, _ = bulletin
    payload["data"]["tables"] = [
        {"type": "dataflow", "url": None, "title": "bad"},
        {"type": "dataflow", "url": 123, "title": "bad"},
        {"type": "xls", "url": ["bad"], "title": "bad"},
        {"type": "dataflow", "title": "missing URL"},
    ]
    payload["data"]["statisticalTables"][0]["title"] = ["unexpected title shape"]
    resource = await service.resource("press:58347")
    assert len(resource.related_datasets) == 8
    assert resource.related_datasets[0].title == "DF_SATIS_SEKLI_SATIS_DURUMU_V3"
    assert resource.related_datasets[0].downloadable


async def test_catalog_failure_preserves_bulletin_and_is_not_cached(bulletin):
    service, calls, _, flows, _ = bulletin
    original = flows[0]["id"]
    flows[0]["id"] = "bad/id"
    degraded = await service.resource("press:58347")
    assert degraded.title and degraded.source_url and degraded.related_datasets
    assert all(not item.downloadable for item in degraded.related_datasets)
    assert "malformed_response" in degraded.notes and "unconfirmed" in degraded.notes
    flows[0]["id"] = original
    assert (await service.resource("press:58347")).related_datasets[0].downloadable
    assert len(calls) == 4


async def test_catalog_timeout_preserves_metadata_and_xls_fallback(catalog, monkeypatch):
    service, _ = catalog
    original = service.portal.flows

    async def unavailable():
        raise SourceError(
            "timeout", "Timed out", "https://veriportali.tuik.gov.tr/api/tr/dataflows"
        )

    monkeypatch.setattr(service.portal, "flows", unavailable)
    resource = await service.resource("press:57987")
    assert resource.title and resource.metadata and resource.download_links
    assert "timeout" in resource.notes and "unconfirmed" in resource.notes
    data = await service.data(resource.id)
    assert data.status == "download_available" and data.download_links[0].verified
    assert all(not item.downloadable for item in data.related_datasets)
    monkeypatch.setattr(service.portal, "flows", original)
    recovered = await service.resource(resource.id)
    assert "unconfirmed" not in (recovered.notes or "")
    assert recovered is not resource


async def test_unavailable_datasets_without_files_are_not_structured(bulletin):
    service, _, _, flows, _ = bulletin
    flows.clear()
    data = await service.data("press:58347")
    assert data.status == "not_structured" and data.related_datasets
    assert all(not item.downloadable for item in data.related_datasets)


async def test_concurrent_bulletin_details_share_catalog(bulletin):
    service, calls, _, _, _ = bulletin
    await asyncio.gather(service.resource("press:58347"), service.resource("press:58347:metadata"))
    assert sum(call.url.path == "/api/tr/dataflows" for call in calls) == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.test/#/tr/tuik/TR,DF_TEST,1.0",
        "https://databrowser2.tuik.gov.tr.evil.test/#/tr/tuik/TR,DF_TEST,1.0",
        "https://www.tuik.gov.tr/#/tr/tuik/TR,DF_TEST,1.0",
        "https://databrowser2.tuik.gov.tr/#/tr/tuik/TR,DF_TEST,../1",
        "https://databrowser2.tuik.gov.tr/#/tr/tuik/TR,DF_TEST,1.0/extra",
        "https://databrowser2.tuik.gov.tr/#/tr/tuik/categories/9/../TR,DF_TEST,1.0",
        "http://databrowser2.tuik.gov.tr/#/tr/tuik/TR,DF_TEST,1.0",
    ],
)
def test_invalid_or_unapproved_dataflow_links_are_omitted(url):
    assert TuikPortal.related_dataset({"type": "dataflow", "url": url, "title": "test"}) is None


def test_old_link_format_and_untrusted_title_are_normalized():
    dataset = TuikPortal.related_dataset(
        {
            "type": "dataflow",
            "title": "<script>bad()</script> İşgücü",
            "url": "https://databrowser2.tuik.gov.tr/#/tr/tuik/TR,DF_TEST,1.0",
        }
    )
    assert dataset.id == "flow:DF_TEST+V1.0" and dataset.category_path == []
    assert dataset.title == "İşgücü"


@pytest.mark.parametrize("path", ["8/", "8/8_1/", "8/8_1/8_1_4/", "8/8_1/8_1_4/8_1_4_2/"])
def test_dataflow_links_keep_every_category_level(path):
    dataset = TuikPortal.related_dataset(
        {
            "type": "dataflow",
            "url": "https://databrowser2.tuik.gov.tr/#/tr/tuik/categories/"
            + path
            + "TR,DF_UCRETLI_CALISAN_ISTATISTIKLERI_C,1.0",
        }
    )
    assert dataset is not None
    assert dataset.id == "flow:DF_UCRETLI_CALISAN_ISTATISTIKLERI_C+V1.0"
    assert dataset.category_path == path.strip("/").split("/")
