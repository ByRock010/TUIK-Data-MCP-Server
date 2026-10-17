import json
from pathlib import Path

import httpx
import pytest

from tuik_mcp.clients.base import HttpClient, SourceError
from tuik_mcp.clients.tuik_portal import TuikPortal, throttle_wait
from tuik_mcp.config import PORTAL, Settings
from tuik_mcp.models.dataset import DownloadLink
from tuik_mcp.server import respond
from tuik_mcp.services.catalog import CatalogService

FIXTURES = Path(__file__).parent / "fixtures"
THROTTLE = (FIXTURES / "download_throttle.html").read_bytes()
XLS = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"test fixture"
TABLE = f"{PORTAL}/api/tr/data/downloads?t=i&p=fixture"
EXPORT = f"{PORTAL}/api/tr/dataflows/DF_ALTINSIZ_DTH%2BV1.0/file/csv"


def throttle_page():
    return httpx.Response(200, content=THROTTLE, headers={"content-type": "text/html"})


def xls_file():
    return httpx.Response(200, content=XLS, headers={"content-type": "application/vnd.ms-excel"})


@pytest.fixture
def waits(monkeypatch):
    delays = []

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr("tuik_mcp.clients.tuik_portal.asyncio.sleep", sleep)
    # Request pacing records zero delays; only real waits matter here.
    return lambda: [delay for delay in delays if delay > 0]


@pytest.mark.parametrize(
    ("body", "default", "expected"),
    [
        (THROTTLE, 8, 6.0),
        (THROTTLE.replace(b"let time = 5;", b"let time = 0;"), 8, 1.0),
        (THROTTLE.replace(b"let time = 5;", b"let time = 120;"), 8, 60.0),
        (THROTTLE.replace(b"let time = 5;", b""), 8, 8.0),
        (b"<!DOCTYPE html><title>Sayfa bulunamad\xc4\xb1</title>", 8, None),
        (b"saniye sonra tekrar dosya indirebilirsiniz", 8, None),
        (XLS, 8, None),
    ],
)
def test_throttle_wait(body, default, expected):
    assert throttle_wait(body, default) == expected


async def test_throttle_page_waits_for_countdown_then_retries(waits):
    calls = []

    def handler(request):
        calls.append(request)
        return throttle_page() if len(calls) == 1 else xls_file()

    http = HttpClient(
        Settings(min_interval_seconds=0, download_interval_seconds=0),
        httpx.MockTransport(handler),
    )
    try:
        body, link = await TuikPortal(http).download(DownloadLink(title="Tablo", source_url=TABLE))
        assert body == XLS and link.format == "xls" and link.verified
        assert len(calls) == 2
        assert waits() == [pytest.approx(6, abs=0.5)]
    finally:
        await http.close()


async def test_persistent_throttle_is_reported(waits):
    calls = []

    def handler(request):
        calls.append(request)
        return throttle_page()

    http = HttpClient(Settings(min_interval_seconds=0), httpx.MockTransport(handler))
    try:
        with pytest.raises(SourceError) as error:
            await TuikPortal(http).download(DownloadLink(title="Tablo", source_url=TABLE))
        assert error.value.code == "download_throttled"
        assert "about 6 seconds" in str(error.value)
        assert error.value.source_url == TABLE
        assert len(calls) == 3
    finally:
        await http.close()


async def test_table_downloads_are_spaced_but_exports_are_not(waits):
    def handler(request):
        if request.url.path.endswith("/file/csv"):
            return httpx.Response(200, content=b"a;b\n1;2\n", headers={"content-type": "text/csv"})
        return xls_file()

    http = HttpClient(Settings(min_interval_seconds=0), httpx.MockTransport(handler))
    portal = TuikPortal(http)
    try:
        for url in (EXPORT, EXPORT, TABLE):
            await portal.download(DownloadLink(title="Veri", source_url=url))
        assert waits() == []
        await portal.download(DownloadLink(title="Tablo", source_url=TABLE))
        assert waits() == [pytest.approx(6, abs=0.5)]
    finally:
        await http.close()


async def test_other_html_is_still_unsupported(waits):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            content=b"<!DOCTYPE html><title>Hata</title>",
            headers={"content-type": "text/html"},
        )

    http = HttpClient(Settings(min_interval_seconds=0), httpx.MockTransport(handler))
    try:
        with pytest.raises(SourceError) as error:
            await TuikPortal(http).download(DownloadLink(title="Tablo", source_url=TABLE))
        assert error.value.code == "unsupported_download" and len(calls) == 1
    finally:
        await http.close()


async def test_tool_response_reports_throttling(waits):
    def handler(request):
        if request.url.path.endswith("/search"):
            return httpx.Response(
                200, json=json.loads((FIXTURES / "unemployment.json").read_text())
            )
        return throttle_page()

    http = HttpClient(Settings(min_interval_seconds=0, attempts=2), httpx.MockTransport(handler))
    try:
        service = CatalogService(http)
        result = await service.search("işsizlik")
        response = await respond(service.data(result.results[0].id))
        assert response["status"] == "error" and response["code"] == "download_throttled"
        assert response["source_url"].startswith(f"{PORTAL}/api/tr/data/downloads")
    finally:
        await http.close()
