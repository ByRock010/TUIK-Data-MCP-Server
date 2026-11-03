"""File content indexing for passage-level search (#22), offline and on a real cluster."""

import json
import os
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from test_indexer import FakeStore, arguments
from test_metadata_crawl import Source

from tuik_mcp.clients.base import HttpClient, SourceError
from tuik_mcp.config import PORTAL, Settings
from tuik_mcp.search.backend import OpenSearchBackend
from tuik_mcp.search.content import (
    MAX_ATTEMPTS,
    ContentIndexer,
    ContentLimits,
    ContentState,
    latest_per_series,
)
from tuik_mcp.search.documents import IndexDocument, chunk_id, file_id, table_id
from tuik_mcp.search.index_cli import main, run
from tuik_mcp.search.indexer import OpenSearchStore, prepare, rebuild
from tuik_mcp.services.catalog import CatalogService

FIXTURES = Path(__file__).parent / "fixtures"
PRESS = json.loads((FIXTURES / "press.json").read_text())
FILES = {
    "Metodolojik açıklama dokümanı": ("labour-methodology.pdf", "application/pdf"),
    "Temel işgücü göstergeleri (15+ yaş)": ("labour-basic.xls", "application/vnd.ms-excel"),
    "Mevsim etkisinden arındırılmış temel işgücü göstergeleri (15+ yaş)": (
        "labour-adjusted.xls",
        "application/vnd.ms-excel",
    ),
}
TITLES = {
    parse_qs(urlsplit(entry["url"]).query)["p"][0]: entry["title"]
    for entry in PRESS["data"]["tables"]
}
KEY = "press:57987:file:"
PDF = KEY + "metodolojik-açıklama-dokümanı~2ae5cab4cd"
BASIC = KEY + "temel-işgücü-göstergeleri-15-yaş~8c14b63e77"
ADJUSTED = KEY + "mevsim-etkisinden-arındırılmış-temel-işgücü-göstergeleri-15-yaş~35fc30e1b3"
THEME = "İstihdam, İşsizlik ve Ücret"


def bulletin(number, published, series, **extra):
    return IndexDocument(
        id=f"press:{number}",
        kind="bulletin",
        title=f"Bülten {number}",
        publication_date=published,
        series_id=series,
        source_url=f"{PORTAL}/tr/press/{number}",
        **extra,
    )


class Upstream:
    """Recorded labour bulletin 57987 with its methodology PDF and two XLS tables."""

    def __init__(self):
        self.requests = []
        self.missing = set()

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if path == "/api/tr/dataflows":
            return httpx.Response(200, json=json.loads((FIXTURES / "flows.json").read_text()))
        if path == "/api/tr/press/57987":
            return httpx.Response(200, json=PRESS)
        if path.endswith("/search"):
            return httpx.Response(
                200, json=json.loads((FIXTURES / "unemployment.json").read_text())
            )
        if path.endswith("/downloads"):
            title = TITLES.get(request.url.params.get("p"))
            if title in FILES and title not in self.missing:
                name, media = FILES[title]
                body = (FIXTURES / "files" / name).read_bytes()
                return httpx.Response(200, content=body, headers={"content-type": media})
        return httpx.Response(404)

    def downloads(self):
        return sum(r.url.path.endswith("/downloads") for r in self.requests)


def client(upstream):
    return HttpClient(
        Settings(min_interval_seconds=0, download_interval_seconds=0, attempts=1),
        httpx.MockTransport(upstream),
    )


@pytest.fixture
async def content():
    upstream = Upstream()
    http = client(upstream)
    yield CatalogService(http), upstream
    await http.close()


def test_content_ids_locators_and_parents_are_validated():
    passage = IndexDocument(
        id=chunk_id(PDF, 9, 1),
        kind="document_chunk",
        title="Metodolojik açıklama dokümanı",
        page=9,
        parent_id=PDF,
        source_url=f"{PORTAL}/tr/press/57987",
    )
    assert passage.id == PDF + "#p9.1"
    table = IndexDocument(
        id=table_id(BASIC, 1),
        kind="table",
        title="Temel işgücü göstergeleri",
        sheet="Tablo",
        parent_id=BASIC,
        source_url=f"{PORTAL}/tr/press/57987",
    )
    assert table.id == BASIC + "#t1"
    base = dict(title="x", source_url=f"{PORTAL}/tr/press/57987")
    for invalid in [
        dict(id=chunk_id(PDF, 9, 1), kind="document_chunk", page=8, parent_id=PDF),
        dict(id=chunk_id(PDF, 9, 1), kind="table", page=9, parent_id=PDF),
        dict(id=chunk_id(PDF, 9, 1), kind="document_chunk", page=9, parent_id=BASIC),
        dict(id=table_id(BASIC, 1), kind="table", parent_id=BASIC),
        dict(id="press:57987", kind="bulletin", page=3),
        dict(id="press:57987:metadata", kind="metadata", parent_id="press:57987", sheet="x"),
        dict(id="flow:DF_X+V1.0#t1", kind="table", sheet="x", parent_id="flow:DF_X+V1.0"),
    ]:
        with pytest.raises(ValueError):
            IndexDocument(**base, **invalid)
    for value in ["publication:yayin~0123456789", "report:rapor~0123456789", PDF]:
        assert file_id(value) == value
    for value in ["press:57987", "flow:DF_X+V1.0", "press:57987:metadata"]:
        with pytest.raises(ValueError):
            file_id(value)


def test_latest_bulletin_per_series_newest_first():
    documents = [
        bulletin(100, date(2026, 8, 1), "series:100"),
        bulletin(200, date(2026, 9, 1), "series:100"),
        bulletin(300, datetime(2026, 7, 1, 10, tzinfo=UTC), "series:300"),
        bulletin(400, None, None),
    ]
    assert [d.id for d in latest_per_series(documents)] == ["press:200", "press:300", "press:400"]


async def test_newest_bulletin_files_become_page_and_sheet_documents(content):
    service, upstream = content
    metadata = [
        bulletin(57987, date(2026, 9, 30), "series:1", theme=[THEME]),
        bulletin(57000, date(2026, 8, 29), "series:1"),
    ]
    indexer = ContentIndexer(service)
    documents = await indexer.documents(metadata)
    report = indexer.report
    # Six recorded bulletin files are unavailable here; they are reported, not fatal.
    assert report["downloaded"] == 3 and len(report["failures"]) == 6
    assert all(f["id"].startswith(KEY) and f["code"] for f in report["failures"])
    assert report["documents"] == {"document_chunk": 15, "table": 2}
    assert "press:57000" not in str(upstream.requests)  # Older bulletins of a series are skipped.
    by_id = {d.id: d for d in documents}
    passage = by_id[chunk_id(PDF, 9, 1)]
    assert passage.page == 9 and passage.parent_id == PDF and "Referans Hafta" in passage.body
    assert passage.title == "Metodolojik açıklama dokümanı"
    assert passage.description.startswith("Bülten 57987 — Ek 1:")
    assert passage.theme == [THEME] and passage.related_ids == ["press:57987"]
    assert passage.source_url == f"{PORTAL}/tr/press/57987" and passage.formats == ["pdf"]
    table = by_id[table_id(BASIC, 1)]
    assert table.sheet == "Tablo" and table.title == "Temel işgücü göstergeleri, 2005-2026"
    assert table.title_en == "Main labour force indicators, 2005-2026"
    assert "İşgücüne katılma oranı Labour force participation rate (%)" in table.body
    assert "[column" not in table.body and "column_2" not in table.body
    assert "TÜİK, İşgücü İstatistikleri, Ağustos 2026" in table.body
    state = indexer.next_state
    assert set(state.files) == {PDF, BASIC, ADJUSTED}
    assert len(state.bulletins["press:57987"].file_ids) == 9


async def test_cached_content_is_reused_without_requests_and_budget_defers_the_rest(content):
    service, upstream = content
    metadata = [bulletin(57987, date(2026, 9, 30), "series:1")]
    first = ContentIndexer(service, limits=ContentLimits(max_downloads=2))
    documents = await first.documents(metadata)
    assert first.report["pending_files"] == 7 and upstream.downloads() == 2
    # Files follow the bulletin's order: the PDF, then the seasonally adjusted table.
    assert {d.parent_id for d in documents} == {PDF, ADJUSTED}
    state = ContentState.model_validate_json(first.next_state.model_dump_json())
    upstream.requests.clear()
    cached = ContentIndexer(service, state)
    again = await cached.documents(metadata, download=False)
    assert again == documents and upstream.requests == []
    assert cached.report["reused"] == 2 and cached.report["pending_files"] == 7
    resumed = ContentIndexer(service, state, ContentLimits(max_downloads=1))
    await resumed.documents(metadata)
    # The next file in order is unavailable upstream: one attempt, reported, then deferred.
    assert resumed.report["reused"] == 2 and upstream.downloads() == 1
    assert len(resumed.report["failures"]) == 1 and resumed.report["pending_files"] == 6


async def test_failing_files_cannot_starve_later_files_and_retries_are_bounded(content):
    service, upstream = content
    metadata = [bulletin(57987, date(2026, 9, 30), "series:1")]
    state, runs = ContentState(), []
    # Bulletin order: PDF, ADJUSTED, three unavailable files, BASIC, three unavailable.
    for _ in range(3):
        indexer = ContentIndexer(service, state, ContentLimits(max_downloads=2))
        await indexer.documents(metadata)
        state = ContentState.model_validate_json(indexer.next_state.model_dump_json())
        runs.append(indexer.report)
    assert runs[0]["downloaded"] == 2 and set(state.files) >= {PDF, ADJUSTED}
    assert [len(run["failures"]) for run in runs[1:]] == [2, 1]
    # Run 3 skips the two failures of run 2 and reaches BASIC.
    assert BASIC in state.files and runs[2]["downloaded"] == 1
    assert sorted(f.attempts for f in state.failures.values()) == [1, 1, 1]
    for _ in range(8):
        indexer = ContentIndexer(service, state, ContentLimits(max_downloads=2))
        await indexer.documents(metadata)
        state = indexer.next_state
    assert {f.attempts for f in state.failures.values()} == {MAX_ATTEMPTS}
    assert len(state.failures) == 6 and set(state.files) == {PDF, ADJUSTED, BASIC}
    upstream.requests.clear()
    settled = ContentIndexer(service, state)
    assert len(await settled.documents(metadata)) == 17
    assert settled.report["given_up"] == 6 and upstream.requests == []
    # A republished bulletin gets a fresh file list, and its failures a fresh start.
    republished = [bulletin(57987, date(2026, 10, 1), "series:1")]
    fresh = ContentIndexer(service, state, ContentLimits(max_downloads=1))
    await fresh.documents(republished)
    assert fresh.report["file_lists_fetched"] == 1 and fresh.report["given_up"] == 0
    assert [f.attempts for f in fresh.next_state.failures.values()] == [1]
    assert fresh.report["pending_files"] == 5


async def test_deferred_explicit_refreshes_keep_cached_files(content):
    service, _ = content
    first = ContentIndexer(service)
    cached = await first.documents([], on_demand=[PDF, BASIC])
    assert set(first.next_state.files) == {PDF, BASIC}
    # One download: the PDF refreshes, BASIC's refresh waits but stays indexed.
    limited = ContentIndexer(service, first.next_state, ContentLimits(max_downloads=1))
    again = await limited.documents([], on_demand=[PDF, BASIC])
    assert {d.id for d in again} == {d.id for d in cached}
    assert set(limited.next_state.files) == {PDF, BASIC}
    assert limited.next_state.files[BASIC].on_demand and limited.report["pending_refreshes"] == 1
    idle = ContentIndexer(service, limited.next_state)
    assert len(await idle.documents([], on_demand=[PDF, BASIC], download=False)) == len(cached)
    assert idle.report["pending_refreshes"] == 2 and set(idle.next_state.files) == {PDF, BASIC}


async def test_on_demand_files_are_sticky_and_keep_cache_when_a_refresh_fails(content):
    service, upstream = content
    indexer = ContentIndexer(service)
    documents = await indexer.documents([], on_demand=[PDF])
    assert indexer.report["downloaded"] == 1 and len(documents) == 15
    # Without crawled metadata the file still cites its bulletin page.
    assert documents[0].source_url == f"{PORTAL}/tr/press/57987" and documents[0].theme == []
    state = indexer.next_state
    assert state.files[PDF].on_demand
    later = ContentIndexer(service, state)
    assert len(await later.documents([], download=False)) == 15
    # A later run (new process, no snapshot cache) cannot download the file any more.
    upstream.missing.add("Metodolojik açıklama dokümanı")
    http = client(upstream)
    try:
        refresh = ContentIndexer(CatalogService(http), later.next_state)
        kept = await refresh.documents([], on_demand=[PDF])
    finally:
        await http.close()
    assert len(kept) == 15 and refresh.report["failures"][0]["id"] == PDF
    assert refresh.next_state.files[PDF].on_demand


async def test_a_failed_file_list_refresh_keeps_the_indexed_files(content):
    service, _ = content
    metadata = [bulletin(57987, date(2026, 9, 30), "series:1")]
    first = ContentIndexer(service, limits=ContentLimits(max_downloads=1))
    documents = await first.documents(metadata)
    assert len(documents) == 15
    # The bulletin was republished (new date), so its file list must be fetched again.
    republished = [bulletin(57987, date(2026, 10, 1), "series:1")]
    waiting = ContentIndexer(service, first.next_state)
    assert await waiting.documents(republished, download=False) == documents
    assert waiting.report["pending_bulletins"] == 1

    async def unavailable(resource_id):
        raise SourceError("upstream_http", "unavailable", f"{PORTAL}/tr/press/57987")

    service.resource = unavailable
    failed = ContentIndexer(service, first.next_state)
    assert await failed.documents(republished) == documents
    assert failed.report["failures"][0]["id"] == "press:57987"
    # The old list keeps its old date, so the next run retries the refresh.
    assert failed.next_state.bulletins["press:57987"].published == "2026-09-30"


async def test_long_pdfs_are_capped_and_reported(content):
    service, _ = content
    indexer = ContentIndexer(service, limits=ContentLimits(max_pdf_pages=4))
    documents = await indexer.documents([], on_demand=[PDF])
    assert max(d.page for d in documents) <= 4 and indexer.report["page_limited"] == [PDF]


async def test_discover_returns_the_file_id_with_its_page_or_sheet(content):
    service, _ = content
    sources = [
        {
            "id": chunk_id(PDF, 9, 1),
            "kind": "document_chunk",
            "title": "Metodolojik açıklama dokümanı",
            "parent_id": PDF,
            "page": 9,
            "source_url": f"{PORTAL}/tr/press/57987",
        },
        {
            "id": table_id(BASIC, 1),
            "kind": "table",
            "title": "Temel işgücü göstergeleri, 2005-2026",
            "parent_id": BASIC,
            "sheet": "Tablo",
            "source_url": f"{PORTAL}/tr/press/57987",
        },
    ]
    requests = []

    def cluster(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "hits": {
                    "total": {"value": 2, "relation": "eq"},
                    "hits": [{"_source": s, "_score": 1.0} for s in sources],
                }
            },
        )

    service.index = OpenSearchBackend(
        OpenSearchStore("http://cluster", transport=httpx.MockTransport(cluster)), "fixture"
    )
    found = await service.discover("referans hafta", kind="document_chunk")
    assert requests[0]["query"]["bool"]["filter"] == [{"terms": {"kind": ["document_chunk"]}}]
    passage, table = found.results
    assert (passage.id, passage.page, passage.resource_type) == (PDF, 9, "document_chunk")
    assert (table.id, table.sheet, table.resource_type) == (BASIC, "Tablo", "table")
    # The returned file ID resolves in a session that never searched.
    assert (await service.resource(PDF)).download_links[0].format == "pdf"
    service.index = None
    fallback = await service.discover("referans hafta", kind="document_chunk")
    assert fallback.backend == "portal" and fallback.results == []
    assert "searchable only in the content index" in fallback.warnings[-1]


async def test_cli_indexes_content_and_full_rebuilds_keep_it_from_state(tmp_path, content):
    service, upstream = content
    store = FakeStore()
    args = arguments(tmp_path, full=True, content=True)
    assert await run(args, source=Source(), store=store, content_service=service) == 0
    report = json.loads(args.report.read_text())
    assert report["content"]["downloaded"] == 3 and report["state_committed"]
    (index,) = store.aliases["tuik-resources"]
    assert {PDF, BASIC, ADJUSTED} <= {d.get("parent_id") for d in store.indices[index].values()}
    assert report["counts"]["document_chunk"] == 15 and report["counts"]["table"] == 2
    upstream.requests.clear()
    rebuilt = arguments(tmp_path, full=True)
    assert await run(rebuilt, source=Source(), store=store, content_service=service) == 0
    (index,) = store.aliases["tuik-resources"]
    assert sum(d["kind"] == "document_chunk" for d in store.indices[index].values()) == 15
    assert upstream.requests == []  # Content came from the state: no downloads.
    dry = arguments(tmp_path, content=True, dry_run=True, content_state=tmp_path / "new.json")
    assert await run(dry, source=Source(), store=store, content_service=service) == 0
    assert not dry.content_state.exists()


def test_cli_validates_content_options(tmp_path):
    state = str(tmp_path / "state.json")
    for argv in [
        ["--content-file", "press:57987"],
        ["--max-content-files", "1001"],
        ["--state", state, "--content-state", state],
    ]:
        with pytest.raises(SystemExit):
            main(argv)


URL = os.getenv("TUIK_OPENSEARCH_URL")


@pytest.mark.opensearch
@pytest.mark.skipif(not URL, reason="Set TUIK_OPENSEARCH_URL")
async def test_real_cluster_finds_the_methodology_page_and_the_bulletin_table(content):
    service, _ = content
    metadata = [bulletin(57987, date(2026, 9, 30), "series:1", theme=[THEME])]
    documents = metadata + await ContentIndexer(service).documents(metadata)
    alias = "tuik-content-test-" + uuid.uuid4().hex
    store = OpenSearchStore(URL)
    try:
        payloads, _ = prepare(documents, datetime(2026, 11, 2, 9, tzinfo=UTC))
        await rebuild(store, alias, payloads)
        service.index = OpenSearchBackend(OpenSearchStore(URL), alias)
        # A methodology term returns the right PDF page.
        for query, page in [("CATI telefonla görüşme", 4), ("anket referans ay ve haftaları", 9)]:
            found = await service.discover(query, kind="document_chunk", limit=3)
            assert found.backend == "opensearch" and found.results[0].highlights
            assert (found.results[0].id, found.results[0].page) == (PDF, page), query
        # Known limit: the Ek 2 diagram repeats defined terms and outranks page 6.
        definition = await service.discover("zamana bağlı eksik istihdam", kind="document_chunk")
        assert (PDF, 6) in {(r.id, r.page) for r in definition.results[:3]}
        # A table concept returns the right bulletin table, with its sheet.
        adjusted = await service.discover(
            "mevsim etkisinden arındırılmış işgücü göstergeleri", kind="table"
        )
        assert (adjusted.results[0].id, adjusted.results[0].sheet) == (ADJUSTED, "Tablo")
        participation = await service.discover("işgücüne katılma oranı", kind="table")
        assert {r.id for r in participation.results} == {BASIC, ADJUSTED}
        # Without a kind, file content stays out of general results.
        general = await service.discover("referans haftası")
        assert all(r.resource_type not in {"table", "document_chunk"} for r in general.results)
        assert set(general.facets.kind.counts) <= {"dataset", "bulletin", "metadata"}
    finally:
        try:
            if service.index is not None:
                await service.index.close()
            for name in await store.versions(alias):
                await store.delete(name)
        finally:
            await store.close()
