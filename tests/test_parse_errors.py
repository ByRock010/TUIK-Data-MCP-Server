import json
from pathlib import Path

import httpx
import pytest

from tuik_mcp.clients.base import HttpClient, SourceError
from tuik_mcp.config import PORTAL, Settings
from tuik_mcp.parsers.errors import InvalidFiltersError, ParseError, UnsupportedFormatError
from tuik_mcp.server import respond
from tuik_mcp.services import parsing
from tuik_mcp.services.catalog import CatalogService

FIXTURES = Path(__file__).parent / "fixtures"
FLOW = "flow:DF_ALTINSIZ_DTH+V1.0"
SOURCE = f"{PORTAL}/api/tr/data/downloads?t=i&p=x"


@pytest.mark.parametrize(
    "error,fmt,code",
    [
        (InvalidFiltersError("no column"), "xlsx", "invalid_filters"),
        (UnsupportedFormatError("no parser"), "zip", "unsupported_download"),
        (ParseError("unreadable"), "xlsx", "parse_failed"),
        (ParseError("unreadable"), "pdf", "parse_failed"),
        (ParseError("bad csv"), "csv", "malformed_response"),
    ],
)
def test_parser_errors_become_source_errors(monkeypatch, error, fmt, code):
    def parse_table(*args, **kwargs):
        raise error

    monkeypatch.setattr(parsing, "parse_table", parse_table)
    with pytest.raises(SourceError) as raised:
        parsing.parse_source(b"file", fmt, SOURCE)
    assert (raised.value.code, raised.value.source_url) == (code, SOURCE)


@pytest.fixture
async def csv_service():
    body = {"value": b"a;b\n1;2\n"}

    def handler(request):
        if request.url.path == "/api/tr/dataflows":
            return httpx.Response(200, json=json.loads((FIXTURES / "flows.json").read_text()))
        return httpx.Response(200, content=body["value"], headers={"content-type": "text/csv"})

    http = HttpClient(Settings(min_interval_seconds=0, attempts=1), httpx.MockTransport(handler))
    yield CatalogService(http), body
    await http.close()


async def test_tool_responses_report_file_problems_as_source_errors(csv_service, monkeypatch):
    service, body = csv_service
    response = await respond(service.data(FLOW, {"missing": "1"}))
    assert response["code"] == "invalid_filters"
    assert response["source_url"].endswith("/file/csv")
    body["value"] = b"a;b\n1;2;3\n"
    # A new service: the first response cached the valid CSV.
    service = CatalogService(service.portal.http)
    response = await respond(service.data(FLOW))
    assert response["code"] == "malformed_response"
    assert response["source_url"].endswith("/file/csv")

    def parse_table(*args, **kwargs):
        raise ParseError("unreadable")

    # Even an untranslated parser error is not reported as caller input.
    monkeypatch.setattr(parsing, "parse_source", parse_table)
    response = await respond(CatalogService(service.portal.http).data(FLOW))
    assert response["status"] == "error" and response["code"] == "parse_failed"


@pytest.mark.parametrize(
    "call",
    [
        lambda service: service.data(FLOW, limit=0),
        lambda service: service.data(FLOW, filters={str(n): "x" for n in range(21)}),
        lambda service: service.resource("https://example.org/file.xls"),
    ],
    ids=["limit", "filters-shape", "resource-id"],
)
async def test_real_input_errors_stay_invalid_input(csv_service, call):
    service, _ = csv_service
    response = await respond(call(service))
    assert response["code"] == "invalid_input"
