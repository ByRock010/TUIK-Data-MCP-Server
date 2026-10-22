import gzip

import httpx
import pytest

from tuik_mcp.clients.base import HttpClient, SourceError
from tuik_mcp.clients.tuik_portal import TuikPortal
from tuik_mcp.config import PORTAL, Settings
from tuik_mcp.services.catalog import CatalogService
from tuik_mcp.services.parsing import parse_source


async def test_timeout_retries(monkeypatch):
    calls, delays = [], []

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr("tuik_mcp.clients.base.asyncio.sleep", sleep)

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("timeout", request=request)

    http = HttpClient(Settings(min_interval_seconds=0), httpx.MockTransport(handler))
    try:
        with pytest.raises(SourceError) as error:
            await http.json("GET", PORTAL)
        assert error.value.code == "upstream_timeout"
        assert error.value.source_url == PORTAL
        assert len(calls) == 3 and 0.5 in delays and 1.0 in delays
    finally:
        await http.close()


@pytest.mark.parametrize("status", [429, 503])
async def test_retryable_status_recovers(status, monkeypatch):
    calls = []

    async def sleep(delay):
        return None

    monkeypatch.setattr("tuik_mcp.clients.base.asyncio.sleep", sleep)

    def handler(request):
        calls.append(request)
        return httpx.Response(status if len(calls) == 1 else 200, json={"ok": True})

    http = HttpClient(Settings(min_interval_seconds=0), httpx.MockTransport(handler))
    try:
        assert await http.json("GET", PORTAL) == {"ok": True}
        assert len(calls) == 2
    finally:
        await http.close()


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        b"{}",
        b'{"isError":true}',
        b'{"isError":false,"data":{"data":[{"title":"missing url"}],"total":1}}',
    ],
)
async def test_malformed_upstream(payload):
    http = HttpClient(
        Settings(min_interval_seconds=0),
        httpx.MockTransport(lambda _: httpx.Response(200, content=payload)),
    )
    try:
        with pytest.raises(SourceError) as error:
            await TuikPortal(http).search_page("nüfus", 1)
        assert error.value.code == "malformed_response"
    finally:
        await http.close()


async def test_blocked_redirect_and_oversize():
    for response, code in [
        (httpx.Response(302, headers={"location": "http://127.0.0.1/secret"}), "unsafe_redirect"),
        (httpx.Response(200, content=b"x" * 2048), "response_too_large"),
        (httpx.Response(403), "upstream_http"),
    ]:
        http = HttpClient(
            Settings(min_interval_seconds=0, max_response_bytes=1024),
            httpx.MockTransport(lambda _, r=response: r),
        )
        try:
            with pytest.raises(SourceError) as error:
                await http.request("GET", PORTAL)
            assert error.value.code == code
        finally:
            await http.close()


async def test_compressed_response():
    http = HttpClient(
        Settings(min_interval_seconds=0),
        httpx.MockTransport(
            lambda _: httpx.Response(
                200, content=gzip.compress(b'{"ok":true}'), headers={"content-encoding": "gzip"}
            )
        ),
    )
    try:
        assert await http.json("GET", PORTAL) == {"ok": True}
    finally:
        await http.close()


async def test_unapproved_result_is_omitted():
    http = HttpClient(
        Settings(min_interval_seconds=0),
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "isError": False,
                    "data": {
                        "data": [{"type": 1, "title": "bad", "url": "https://evil.test"}],
                        "total": 1,
                    },
                },
            )
        ),
    )
    try:
        result = await CatalogService(http).search("test")
        assert not result.results and result.warnings
    finally:
        await http.close()


@pytest.mark.parametrize("body", [b"a;a\n1;2\n", b"a;b\n1;2;3\n", b"a;b\n1\n", b"\xff\xff"])
def test_malformed_csv(body):
    with pytest.raises(SourceError):
        parse_source(body, "csv", PORTAL, limit=10)
