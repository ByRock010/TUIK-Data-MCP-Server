"""Offline subprocess fixture for real MCP query/continuation protocol tests."""

import httpx

from tuik_mcp import server
from tuik_mcp.clients.base import HttpClient

downloads = 0


def handler(request):
    global downloads
    if request.url.path == "/api/tr/dataflows":
        return httpx.Response(
            200,
            json=[
                {
                    "id": "DF_QUERY+V1.0",
                    "name": "Query fixture",
                    "downloadable": True,
                }
            ],
        )
    if request.url.path.endswith("/file/csv"):
        downloads += 1
        # A second download would silently switch the cursor to different content.
        body = (
            "n;city\n2;Ankara\n10;İstanbul\n1;Ankara\n"
            if downloads == 1
            else "n;city\n999;Changed\n"
        )
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/csv"})
    raise AssertionError(f"Unexpected network call: {request.url}")


def mock_http(settings):
    return HttpClient(settings, httpx.MockTransport(handler))


if __name__ == "__main__":
    server.HttpClient = mock_http
    server.main()
