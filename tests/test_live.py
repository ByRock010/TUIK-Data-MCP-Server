import importlib.util
from pathlib import Path

import pytest

from tuik_mcp.clients.base import HttpClient
from tuik_mcp.config import Settings
from tuik_mcp.services.catalog import CatalogService


@pytest.mark.live
async def test_live_bulletin_related_datasets_to_csv():
    http = HttpClient(Settings())
    try:
        service = CatalogService(http)
        resource = await service.resource("press:58347")
        related = [item for item in resource.related_datasets if item.downloadable]
        assert related and all(item.category_path for item in related)
        response = await service.data(resource.id)
        assert response.status == "related_datasets"
        data = await service.data(related[0].id, limit=2)
        assert data.status == "ok" and data.rows
        assert data.source_url.endswith("/file/csv")
    finally:
        await http.close()


@pytest.mark.live
async def test_live_three_level_bulletin_category_paths():
    http = HttpClient(Settings())
    try:
        service = CatalogService(http)
        resource = await service.resource("press:57954")
        related = [item for item in resource.related_datasets if item.downloadable]
        assert len(related) == 2
        assert all(item.category_path == ["8", "8_1", "8_1_4"] for item in related)
        assert (await service.data(resource.id)).status == "related_datasets"
        for item in related:
            # These exports can exceed the default 10 MiB response bound.
            # Resolve their catalog metadata; the other live test checks CSV rows.
            flow = await service.resource(item.id)
            assert any(link.format == "csv" for link in flow.download_links)
    finally:
        await http.close()


@pytest.mark.live
async def test_live_mcp_workflow():
    script = Path(__file__).parents[1] / "scripts" / "live_smoke.py"
    spec = importlib.util.spec_from_file_location("live_smoke", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = await module.run()
    assert len(report["tools"]) == 6


@pytest.mark.live
async def test_live_back_to_back_bulletin_downloads():
    http = HttpClient(Settings())
    try:
        service = CatalogService(http)
        search = await service.search("Tüketici Fiyat Endeksi")
        for result in search.results:
            if result.resource_type == "bulletin":
                resource = await service.resource(result.id)
                links = [link for link in resource.download_links if link.format == "xls"][:5]
                if len(links) >= 3:
                    break
        else:
            pytest.fail("No bulletin with at least three spreadsheet downloads was found")
        for link in links:
            # A throttle page raises; some links advertised as XLS deliver XLSX archives.
            body, verified = await service.portal.download(link)
            assert verified.verified and body
    finally:
        await http.close()


@pytest.mark.live
async def test_live_cpi_spreadsheet_and_xls_only_bulletin_rows():
    http = HttpClient(Settings.from_env())
    try:
        service = CatalogService(http)
        resource = await service.resource("press:58298")
        link = next(
            link
            for link in resource.download_links
            if link.title == "Tüketici fiyat endeksi ve değişim oranları"
        )
        data = await service.data(link.resource_id, limit=2)
        assert data.status == "ok" and len(data.rows) == 2
        assert data.unit == "2025=100" and data.sheet and data.table_title
        assert data.extraction == "xls" and data.total_rows >= 22
        before = data.retrieved_at
        assert (await service.data(link.resource_id, limit=1)).retrieved_at == before
        # #6's old press:54072 example advertises a first file that now returns 404.
        # This current XLS-only population bulletin exercises the same direct-bulletin path.
        population = await service.data("press:53899", limit=2)
        assert population.status == "ok" and population.rows and population.extraction == "xls"
        assert population.total_rows == 82
        labour = await service.resource("press:57987")
        file = next(
            link
            for link in labour.download_links
            if link.title == "Temel işgücü göstergeleri (15+ yaş)"
        )
        rows = await service.data(file.resource_id, limit=2)
        assert rows.status == "ok" and rows.rows and rows.unit == "15+ yaş - age"
    finally:
        await http.close()
