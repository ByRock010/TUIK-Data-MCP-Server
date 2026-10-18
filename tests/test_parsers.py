import asyncio
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from tuik_mcp.clients.base import SourceError
from tuik_mcp.clients.tuik_portal import TuikPortal
from tuik_mcp.parsers import DocumentChunk, DocumentPage, ParsedTable, parse_document, parse_table
from tuik_mcp.parsers.errors import InvalidFiltersError, ParseError, UnsupportedFormatError


def test_real_csv_fixture_and_bounded_filtering():
    body = (Path(__file__).parent / "fixtures/data.csv").read_bytes()
    full = parse_table(body, "csv")[0]
    assert full.extraction == "csv" and full.total_rows == 3
    column = full.columns[0]
    value = full.rows[0][column]
    filtered = parse_table(body, "csv", limit=1, filters={column: value})[0]
    assert filtered.rows == [full.rows[0]]
    assert filtered.total_rows == sum(row[column] == value for row in full.rows)
    assert parse_table(body, "csv", filters={column: "absent"})[0].total_rows == 0


@pytest.mark.parametrize("delimiter", [",", ";", "\t"])
def test_csv_preserves_bom_turkish_decimals_and_missing_markers(delimiter):
    body = (
        "\ufeffYıl" + delimiter + "Değer\n2025" + delimiter + '"42,0"\n2026' + delimiter + "..\n"
    ).encode()
    table = parse_table(body, "csv", limit=1)[0]
    assert table.columns == ["Yıl", "Değer"]
    assert table.rows == [{"Yıl": "2025", "Değer": "42,0"}]
    assert table.total_rows == 2


@pytest.mark.parametrize(
    "body",
    [b"", b"a;a\n1;2\n", b"a;\n1;2\n", b"a;b\n1;2;3\n", b"a;b\n1\n", b"a;b\n\xff;2\n"],
)
def test_malformed_csv_is_explicit(body):
    with pytest.raises(ParseError):
        parse_table(body, "csv")
    with pytest.raises(SourceError) as error:
        TuikPortal.csv_rows(body, "https://veriportali.tuik.gov.tr/test", 10, {})
    assert error.value.code == "malformed_response"


def test_unknown_filter_keeps_adapter_error_and_source():
    body = b"a;b\n1;2\n"
    with pytest.raises(InvalidFiltersError):
        parse_table(body, "csv", filters={"unknown": "1"})
    with pytest.raises(SourceError) as error:
        TuikPortal.csv_rows(body, "https://veriportali.tuik.gov.tr/test", 10, {"unknown": "1"})
    assert error.value.code == "invalid_filters"
    assert error.value.source_url.endswith("/test")


@pytest.mark.parametrize("fmt", ["xls", "xlsx", "pdf", "zip", "CSV"])
def test_unimplemented_parsers_are_explicit(fmt):
    with pytest.raises(UnsupportedFormatError):
        parse_table(b"arbitrary", fmt)
    with pytest.raises(UnsupportedFormatError):
        parse_document(b"arbitrary", fmt)


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_parser_limit(limit):
    with pytest.raises(ValueError, match="positive integer"):
        parse_table(b"a;b\n1;2\n", "csv", limit=limit)


def test_shared_models_validate_and_do_not_share_collections():
    first = ParsedTable(extraction="xls", unit="2025=100", notes=["(*) preliminary"])
    second = ParsedTable(extraction="csv")
    first.columns.append("Yıl")
    assert second.columns == []
    assert first.model_dump()["unit"] == "2025=100"
    assert DocumentChunk(page=2, text="passage").page == 2
    with pytest.raises(ValidationError):
        DocumentPage(page=0, text="text")
    with pytest.raises(ValidationError):
        ParsedTable(extraction="html")


async def test_service_offloads_csv_parsing_and_keeps_loop_responsive(catalog, monkeypatch):
    service, _ = catalog
    loop_thread = threading.get_ident()
    started, release = threading.Event(), threading.Event()
    original = service.portal.csv_rows

    def parse(*args):
        assert threading.get_ident() != loop_thread
        started.set()
        assert release.wait(timeout=5)
        return original(*args)

    monkeypatch.setattr(service.portal, "csv_rows", parse)
    task = asyncio.create_task(service.data("flow:DF_ALTINSIZ_DTH+V1.0", limit=1))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        # This coroutine must run while the parser is waiting in its worker thread.
        assert not task.done()
    finally:
        release.set()
    assert (await task).status == "ok"
