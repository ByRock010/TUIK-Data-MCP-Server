"""Catalog discovery contracts; synthetic records below are test-only."""

import asyncio

import httpx
import pytest
from pydantic import ValidationError

from tuik_mcp.clients.base import HttpClient, SourceError
from tuik_mcp.config import PORTAL, Settings
from tuik_mcp.services.catalog import CatalogService


@pytest.fixture
async def datasets():
    records = [
        {
            "id": "DF_TEST_A+V1.0",
            "name": "İşsizlik",
            "downloadable": False,
            "category": {"name": "İstihdam, İşsizlik ve Ücret"},
        },
        {
            "id": "DF_TEST_B+V1.0",
            "name": "Genç İşsizlik Oranı",
            "downloadable": True,
            "category": {"name": "İstihdam, İşsizlik ve Ücret"},
        },
        {
            "id": "DF_TEST_C+V1.0",
            "name": "İşsizlik",
            "downloadable": True,
            "category": {"name": "İstihdam, İşsizlik ve Ücret"},
        },
        {
            "id": "DF_TEST_D+V1.0",
            "name": "Gençlerin durumu",
            "downloadable": True,
            "description": "Genç nüfusun işsizlik göstergeleri",
            "category": {"name": "Nüfus ve Demografi"},
        },
        {
            "id": "DF_TEST_E+V1.0",
            "name": "İl Nüfusları",
            "downloadable": True,
            "category": {"name": "Nüfus ve Demografi"},
        },
    ]
    calls = []

    def handler(request):
        assert request.url.path == "/api/tr/dataflows"
        calls.append(request)
        return httpx.Response(200, json=records)

    http = HttpClient(Settings(min_interval_seconds=0), httpx.MockTransport(handler))
    try:
        yield CatalogService(http), calls, http, records
    finally:
        await http.close()


async def test_discovery_metadata_and_csv_pipeline(catalog):
    service, calls = catalog
    found = await service.search_datasets("ALTIN HARİÇ DIŞ TİCARET HADLERİ")
    assert found.total == 1 and not found.truncated
    record = found.results[0]
    assert record.id == "flow:DF_ALTINSIZ_DTH+V1.0"
    assert record.available_formats == ["csv", "json", "xml"]
    assert all(not link.verified for link in record.download_links)
    assert found.source_url == PORTAL + "/api/tr/dataflows"
    assert record.source_url.startswith("https://databrowser2.tuik.gov.tr/")
    assert record.retrieved_at == found.retrieved_at
    # Serialization must retain export fields even though results inherit SearchResult.
    assert found.model_dump()["results"][0]["available_formats"] == record.available_formats
    detail = await service.resource(record.id)
    assert detail.retrieved_at == record.retrieved_at
    data = await service.data(record.id, limit=2)
    assert data.status == "ok" and len(data.rows) == 2
    assert [r.url.path for r in calls] == [
        "/api/tr/dataflows",
        "/api/tr/dataflows/DF_ALTINSIZ_DTH+V1.0/file/csv",
    ]


async def test_turkish_matching_downloadability_ranking_and_count(datasets):
    service, _, _, _ = datasets
    result = await service.search_datasets("  İŞSİZLİK  ", limit=2)
    assert result.total == 3 and result.truncated
    assert [r.id for r in result.results] == ["flow:DF_TEST_C+V1.0", "flow:DF_TEST_B+V1.0"]
    unfiltered = await service.search_datasets("işsizlik", downloadable_only=False)
    assert unfiltered.total == 4
    unavailable = next(r for r in unfiltered.results if r.id == "flow:DF_TEST_A+V1.0")
    assert unavailable.available_formats == [] and unavailable.download_links == []
    assert (await service.data(unavailable.id)).status == "not_structured"


async def test_all_terms_and_exact_major_theme(datasets):
    service, _, _, _ = datasets
    result = await service.search_datasets("İŞSİZLİK GENÇ")
    assert result.total == 2
    result = await service.search_datasets("işsizlik", theme="  NÜFUS VE DEMOGRAFİ  ")
    assert [r.id for r in result.results] == ["flow:DF_TEST_D+V1.0"]
    assert (await service.search_datasets("işsizlik", theme="Nüfus")).total == 0
    assert (await service.search_datasets("unmatched")).results == []
    assert (await service.search_datasets("Nüfus ve Demografi")).total == 2


async def test_catalog_is_shared_and_ids_work_in_new_session(datasets):
    service, calls, http, _ = datasets
    results = await asyncio.gather(*[service.search_datasets("işsizlik") for _ in range(4)])
    assert len(calls) == 1
    assert len({r.retrieved_at for r in results}) == 1
    record = results[0].results[0]
    record.title = "client modification"
    assert (await service.resource(record.id)).title == "İşsizlik"
    fresh = CatalogService(http)
    detail = await fresh.resource(record.id)
    assert detail.title == "İşsizlik" and len(calls) == 2
    with pytest.raises(SourceError) as exc:
        await fresh.resource("flow:DF_REMOVED+V1.0")
    assert exc.value.code == "not_found"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"query": " "},
        {"query": "x" * 201},
        {"query": "a\x00b"},
        {"query": "işsizlik", "limit": 51},
        {"query": "işsizlik", "theme": " "},
        {"query": "işsizlik", "downloadable_only": "false"},
    ],
)
async def test_validation_happens_before_http(datasets, kwargs):
    service, calls, _, _ = datasets
    with pytest.raises(ValidationError):
        await service.search_datasets(**kwargs)
    assert calls == []


@pytest.mark.parametrize(
    "update",
    [
        {"id": "../../bad"},
        {"name": ""},
        {"downloadable": "true"},
        {"category": {"name": []}},
    ],
)
async def test_malformed_catalog_is_reported_and_not_cached(datasets, update):
    service, calls, _, records = datasets
    original = records[0].copy()
    records[0].update(update)
    with pytest.raises(SourceError) as exc:
        await service.search_datasets("işsizlik")
    assert exc.value.code == "malformed_response"
    assert exc.value.source_url.endswith("/api/tr/dataflows")
    records[0] = original
    assert (await service.search_datasets("işsizlik")).total == 3
    assert len(calls) == 2


async def test_empty_catalog_has_provenance(datasets):
    service, _, _, records = datasets
    records.clear()
    result = await service.search_datasets("nüfus")
    assert result.results == [] and result.total == 0 and not result.truncated
    assert result.source_url and result.retrieved_at
