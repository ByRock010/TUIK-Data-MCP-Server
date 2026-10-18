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
