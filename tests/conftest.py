import json
from pathlib import Path

import httpx
import pytest

from tuik_mcp.clients.base import HttpClient
from tuik_mcp.config import Settings
from tuik_mcp.services.catalog import CatalogService

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.fixture
async def catalog():
    calls = []

    def handler(request):
        calls.append(request)
        path = request.url.path
        if path.endswith("/search"):
            body = json.loads(request.content)
            if body["text"] == "no matches":
                return httpx.Response(
                    200, json={"isError": False, "data": {"data": [], "total": 0}}
                )
            return httpx.Response(200, json=fixture("unemployment"))
        if path == "/api/tr/press/57987":
            return httpx.Response(200, json=fixture("press"))
        if path == "/api/tr/dataflows":
            return httpx.Response(200, json=fixture("flows"))
        if path.endswith("/file/csv"):
            return httpx.Response(
                200,
                content=(FIXTURES / "data.csv").read_bytes(),
                headers={"content-type": "text/csv"},
            )
        if path.endswith("/downloads"):
            return httpx.Response(
                200,
                content=b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"test fixture",
                headers={"content-type": "application/vnd.ms-excel"},
            )
        if path.endswith("/statistical-themes"):
            return httpx.Response(200, json=fixture("themes"))
        if path.endswith("/GetSiniflamalarSahibi"):
            return httpx.Response(200, json=fixture("classifications"))
        raise AssertionError(f"Unexpected request: {request.url}")

    http = HttpClient(Settings(min_interval_seconds=0, attempts=1), httpx.MockTransport(handler))
    yield CatalogService(http), calls
    await http.close()
