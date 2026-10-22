import asyncio
import io
import threading
import zipfile
from pathlib import Path

import httpx
import pytest
from openpyxl import Workbook

from tuik_mcp.cache import BytesTTLCache
from tuik_mcp.clients.base import HttpClient, SourceError
from tuik_mcp.clients.tuik_portal import TuikPortal
from tuik_mcp.config import PORTAL, Settings
from tuik_mcp.models.dataset import DownloadLink
from tuik_mcp.parsers import parse_table
from tuik_mcp.parsers.errors import ParseLimitError
from tuik_mcp.parsers.limits import ParseLimits
from tuik_mcp.services import files
from tuik_mcp.services.files import FileStore
from tuik_mcp.services.parsing import parse_source

FILES = Path(__file__).parent / "fixtures/files"
URL = f"{PORTAL}/api/tr/data/downloads?t=i&p=test"


def workbook(sheets=1):
    book = Workbook()
    for index in range(sheets):
        sheet = book.active if index == 0 else book.create_sheet()
        sheet.append(["Yıl", "Değer"])
        sheet.append([2025, 42.0])
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def rewrite(body, transform):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(body)) as original, zipfile.ZipFile(output, "w") as edited:
        for member in original.infolist():
            edited.writestr(member.filename, transform(member.filename, original.read(member)))
    return output.getvalue()


@pytest.mark.parametrize("fmt,name", [("xls", "labour-basic"), ("xlsx", "cpi-basket")])
def test_real_workbooks_reject_cell_budget_before_table_allocation(fmt, name):
    body = (FILES / f"{name}.{fmt}").read_bytes()
    with pytest.raises(ParseLimitError, match="cells"):
        parse_table(body, fmt, budgets=ParseLimits(max_cells=10))
    with pytest.raises(SourceError) as raised:
        parse_source(body, fmt, URL, budgets=ParseLimits(max_cells=10))
    assert (raised.value.code, raised.value.source_url) == ("parse_limit_exceeded", URL)


def test_xlsx_sheet_and_inflated_size_budgets():
    with pytest.raises(ParseLimitError, match="sheets"):
        parse_table(workbook(2), "xlsx", budgets=ParseLimits(max_sheets=1))
    with pytest.raises(ParseLimitError, match="uncompressed"):
        parse_table(workbook(), "xlsx", budgets=ParseLimits(max_uncompressed_bytes=100))


def test_forged_xlsx_dimensions_do_not_hide_data_or_allocate_huge_grid():
    body = workbook()
    undersized = rewrite(body, lambda name, data: data.replace(b'ref="A1:B2"', b'ref="A1:A1"'))
    assert parse_table(undersized, "xlsx")[0].rows == parse_table(body, "xlsx")[0].rows
    # A far-away actual row would cause openpyxl to emit millions of empty cells.
    sparse = rewrite(
        body,
        lambda name, data: (
            data.replace(b'r="2"', b'r="1048576"')
            .replace(b"A2", b"A1048576")
            .replace(b"B2", b"B1048576")
            if name.startswith("xl/worksheets/")
            else data
        ),
    )
    with pytest.raises(ParseLimitError, match="cells"):
        parse_table(sparse, "xlsx")


def test_byte_cache_eviction_expiry_and_oversize(monkeypatch):
    clock = [0]
    monkeypatch.setattr("tuik_mcp.cache.time.monotonic", lambda: clock[0])
    cache = BytesTTLCache(6)
    cache.set("a", b"aaa", ttl=10)
    cache.set("b", b"bb", ttl=20)
    assert cache.get("a") == b"aaa"
    cache.set("c", b"cc", ttl=20)
    assert cache.get("b") is None
    assert cache.size_bytes == 5
    clock[0] = 11
    assert cache.get("a") is None
    cache.set("large", b"x" * 7)
    assert cache.get("large") is None and cache.size_bytes == 2


async def test_cache_shares_download_and_parse_without_sharing_mutable_results(monkeypatch):
    calls = []
    body = (FILES / "cpi-index.xls").read_bytes()

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200, content=body, headers={"content-type": "application/vnd.ms-excel"}
        )

    http = HttpClient(
        Settings(min_interval_seconds=0, download_interval_seconds=0), httpx.MockTransport(handler)
    )
    store = FileStore(TuikPortal(http))
    link = DownloadLink(title="TÜFE", source_url=URL)
    parses = []
    original = files.parse_source

    def parse(*args, **kwargs):
        parses.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(files, "parse_source", parse)
    try:
        first, second = await asyncio.gather(
            store.download("press:1:file:x", link), store.download("press:1:file:x", link)
        )
        a, b = await asyncio.gather(
            store.tables("press:1:file:x", first), store.tables("press:1:file:x", second)
        )
        assert len(calls) == len(parses) == 1
        assert first.retrieved_at == second.retrieved_at
        a[0].rows.clear()
        assert b[0].rows
        assert (await store.tables("press:1:file:x", first))[0].rows
        # Content hash separates a later revision of the same stable identity.
        revised = await store.download("press:2:file:x", link)
        assert revised.digest == first.digest
        await store.tables("press:2:file:x", revised)
        assert len(parses) == 2
    finally:
        await http.close()


async def test_cancelled_worker_finishes_before_parse_slot_is_released():
    started, release = threading.Event(), threading.Event()

    def worker():
        started.set()
        assert release.wait(3)
        return 42

    task = asyncio.create_task(files.finish_worker(worker))
    assert await asyncio.to_thread(started.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
