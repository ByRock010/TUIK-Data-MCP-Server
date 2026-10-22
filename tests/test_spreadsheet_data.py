import io
from pathlib import Path

import httpx
import pytest
from openpyxl import Workbook

from tuik_mcp.clients.base import HttpClient
from tuik_mcp.config import PORTAL, Settings
from tuik_mcp.models.search import TuikSearchResult
from tuik_mcp.server import respond
from tuik_mcp.services.catalog import CatalogService

FILES = Path(__file__).parent / "fixtures/files"
ID = "resource:" + "a" * 64
URL = f"{PORTAL}/api/tr/data/downloads?t=i&p=fixture"


def service(body, **settings):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200, content=body, headers={"content-type": "application/vnd.ms-excel"}
        )

    http = HttpClient(
        Settings(min_interval_seconds=0, download_interval_seconds=0, **settings),
        httpx.MockTransport(handler),
    )
    catalog = CatalogService(http)
    catalog.remember(TuikSearchResult(id=ID, title="Tablo", resource_type="table", source_url=URL))
    return http, catalog, calls


@pytest.mark.parametrize(
    "name,fmt,expected",
    [
        ("cpi-index", "xls", 110),
        ("labour-basic", "xls", 260),
        ("cpi-basket", "xlsx", 752),
    ],
)
async def test_real_file_to_rows_with_provenance_and_cache(name, fmt, expected):
    http, catalog, calls = service((FILES / f"{name}.{fmt}").read_bytes())
    try:
        first = await catalog.data(ID, limit=2)
        assert first.status == "ok" and len(first.rows) == 2
        assert first.format == first.extraction == fmt
        assert first.total_rows == expected and first.truncated
        assert first.sheet in first.sheets and first.table_title and first.unit
        assert first.source_url == URL and first.download_links[0].verified
        second = await catalog.data(ID, limit=1, sheet=first.sheet)
        assert second.rows == first.rows[:1] and second.retrieved_at == first.retrieved_at
        assert len(calls) == 1
        # Choosing another exact filter/limit does not cache a previous caller's row subset.
        column = first.columns[0]
        value = first.rows[0][column]
        filtered = await catalog.data(ID, filters={column: value})
        assert filtered.rows and all(r[column] == value for r in filtered.rows)
        missing = await respond(catalog.data(ID, filters={"not a column": "x"}))
        assert missing["code"] == "invalid_filters" and missing["source_url"] == URL
        invalid_sheet = await respond(catalog.data(ID, sheet="No such sheet"))
        assert invalid_sheet["code"] == "invalid_input"
        assert len(calls) == 1
    finally:
        await http.close()


async def test_second_sheet_selection_and_raw_layout_are_explicit():
    book = Workbook()
    book.active.title = "İlk"
    book.active.append(["Kod", "Ad"])
    book.active.append(["01", "Gıda"])
    second = book.create_sheet("Diğer")
    second.append(["Kod", "Ad"])
    second.append(["02", "Konut"])
    out = io.BytesIO()
    book.save(out)
    http, catalog, calls = service(out.getvalue())
    try:
        data = await catalog.data(ID, sheet="Diğer")
        assert data.sheets == ["İlk", "Diğer"] and data.sheet == "Diğer"
        assert data.layout == "raw" and data.warnings
        assert data.rows[-1] == {"column_1": "02", "column_2": "Konut"}
        empty = await catalog.data(ID, sheet="Diğer", filters={"column_1": "absent"})
        assert empty.total_rows == 0 and not empty.rows and not empty.truncated
        assert len(calls) == 1
    finally:
        await http.close()


async def test_unreadable_and_oversize_workbooks_are_source_errors():
    for body, settings, code in [
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1broken", {}, "parse_failed"),
        ((FILES / "cpi-index.xls").read_bytes(), {"parse_max_cells": 10}, "parse_limit_exceeded"),
    ]:
        http, catalog, _ = service(body, **settings)
        try:
            data = await respond(catalog.data(ID))
            assert data["code"] == code and data["source_url"] == URL
        finally:
            await http.close()


async def test_sheet_does_not_silently_override_csv_or_bulletin_choices(catalog):
    service, calls = catalog
    data = await respond(service.data("flow:DF_ALTINSIZ_DTH+V1.0", sheet="Sheet1"))
    assert data["code"] == "invalid_input"
    assert not any(r.url.path.endswith("/file/csv") for r in calls)
