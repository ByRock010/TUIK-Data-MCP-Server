"""Recorded source content with deliberately controlled pagination and failures."""

import importlib.util
import json
import time
from argparse import Namespace
from pathlib import Path

import httpx
import pytest

from tuik_mcp.clients.base import SourceError
from tuik_mcp.clients.crawl import CrawlFlow, CrawlHttpClient, CrawlPress, CrawlSource, ThemeNode
from tuik_mcp.clients.tuik_portal import SearchItem, SearchPage
from tuik_mcp.config import Settings
from tuik_mcp.search.crawl import CrawlState, MetadataCrawler, portal_date, state_bytes

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


class Source:
    def __init__(self):
        self.calls = []
        self.fail = None
        self.date = "2026-09-30T10:00:00"
        self.known = CrawlPress.model_validate(load("press.json")["data"])
        self.other = CrawlPress.model_validate(load("crawl/poultry.json")["data"])

    async def flows(self):
        self.calls.append("flows")
        return [CrawlFlow.model_validate(x) for x in load("crawl/flows.json")]

    async def themes(self):
        if self.fail == "themes":
            raise SourceError("upstream_http", "unavailable", "https://veriportali.tuik.gov.tr/")
        return [ThemeNode.model_validate(x) for x in load("crawl/themes.json")["data"]]

    async def listing(self, page):
        self.calls.append(f"page:{page}")
        if self.fail == "page2" and page == 2:
            raise RuntimeError("interrupted listing")
        press = self.known if page == 1 else self.other
        return SearchPage(
            data=[
                SearchItem(
                    title=press.title,
                    url=f"/tr/press/{press.id}",
                    type=1,
                    date=self.date if page == 1 else "2026-10-13T10:00:00",
                )
            ],
            total=2,
        )

    async def press(self, number):
        self.calls.append(f"press:{number}")
        if self.fail == "detail" and number == str(self.other.id):
            raise RuntimeError("interrupted detail")
        return self.known if number == str(self.known.id) else self.other


async def collect(crawler, **kwargs):
    return [doc async for doc in crawler.documents(**kwargs)]


async def test_full_crawl_documents_relations_categories_and_no_file_fetches():
    source = Source()
    crawler = MetadataCrawler(source)
    documents = await collect(crawler, full=True)
    by_id = {d.id: d for d in documents}
    bulletin = by_id["press:57987"]
    assert bulletin.body and "<p>" not in bulletin.body
    assert bulletin.publication_date.isoformat().endswith("+03:00")
    assert bulletin.theme == ["İstihdam, İşsizlik ve Ücret"]
    assert any(p.startswith("theme-tree:") for p in bulletin.category_path)
    assert bulletin.series_id.startswith("series:press:")
    assert by_id["press:57987:metadata"].parent_id == bulletin.id
    assert by_id["press:57987:metadata"].body
    flow = next(d for d in documents if d.kind == "dataset" and bulletin.id in d.related_ids)
    assert flow.id in bulletin.related_ids and flow.theme
    assert not any(d.id.startswith("resource:") for d in documents)
    assert crawler.report["counts"]["bulletin"] == 2
    assert crawler.report["status"] == "ok" and crawler.next_state is not None
    assert source.calls == ["flows", "page:1", "page:2", "press:57987", "press:58058"]


async def test_incremental_lists_every_page_and_replays_all_cached_documents(tmp_path):
    source = Source()
    first = MetadataCrawler(source)
    full = await collect(first, full=True)
    path = tmp_path / "state.json"
    path.write_bytes(state_bytes(first.next_state))
    state = CrawlState.load(path)
    source.calls.clear()
    second = MetadataCrawler(source, state)
    replay = await collect(second)
    assert {d.id for d in replay} == {d.id for d in full}
    assert source.calls == ["flows", "page:1", "page:2"]
    assert second.report["reused_bulletins"] == 2
    old = next(d for d in full if d.kind == "bulletin")
    new = next(d for d in replay if d.id == old.id)
    assert new.retrieved_at == old.retrieved_at
    source.date = "2026-10-26T10:00:00"
    source.calls.clear()
    changed = MetadataCrawler(source, state)
    await collect(changed)
    assert source.calls[-1] == "press:57987"
    assert changed.report["fetched_bulletins"] == 1


@pytest.mark.parametrize("failure", ["page2", "detail"])
async def test_failure_never_advances_state(failure):
    source = Source()
    source.fail = failure
    state = CrawlState()
    crawler = MetadataCrawler(source, state)
    with pytest.raises(RuntimeError):
        await collect(crawler)
    assert state.records == {} and crawler.next_state is None
    assert crawler.report["status"] == "error"


async def test_partial_and_abandoned_iteration_do_not_advance_state():
    source = Source()
    crawler = MetadataCrawler(source)
    docs = await collect(crawler, page_limit=1, max_bulletins=1)
    assert any(d.kind == "bulletin" for d in docs)
    assert crawler.report["partial"] and crawler.next_state is None
    crawler = MetadataCrawler(source)
    iterator = crawler.documents()
    await anext(iterator)
    await iterator.aclose()
    assert crawler.next_state is None and crawler.report["status"] == "incomplete"


async def test_theme_outage_is_explicit_and_retried_on_cached_runs():
    source = Source()
    source.fail = "themes"
    crawler = MetadataCrawler(source)
    await collect(crawler)
    assert crawler.report["theme_tree"] == "upstream_http" and crawler.report["warnings"]
    source.fail = None
    recovered = MetadataCrawler(source, crawler.next_state)
    docs = await collect(recovered)
    assert recovered.report["theme_tree"] == "ok"
    assert next(d for d in docs if d.id == "press:57987").theme


async def test_repeated_pages_are_not_reported_as_a_complete_crawl():
    source = Source()
    original = source.listing

    async def repeated(page):
        return await original(1)

    source.listing = repeated
    crawler = MetadataCrawler(source)
    with pytest.raises(ValueError, match="repeated"):
        await collect(crawler)
    assert crawler.next_state is None


async def test_overlapping_pages_deduplicate_ids_without_confusing_source_row_total():
    source = Source()
    original = source.listing
    first, second = await original(1), await original(2)

    async def overlap(page):
        return SearchPage(data=first.data + second.data if page == 1 else first.data, total=3)

    source.listing = overlap
    crawler = MetadataCrawler(source)
    await collect(crawler)
    assert crawler.report["listing_rows"] == 3
    assert crawler.report["listed_bulletins"] == 2
    assert crawler.report["duplicate_listing_rows"] == 1
    assert crawler.next_state is not None and len(crawler.next_state.records) == 2


async def test_new_adapter_uses_only_recorded_json_endpoints_and_bulletin_filter(monkeypatch):
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        path = request.url.path
        if path.endswith("/dataflows"):
            return httpx.Response(200, json=load("crawl/flows.json"))
        if path.endswith("/statistical-themes"):
            return httpx.Response(200, json=load("crawl/themes.json"))
        if path.endswith("/search"):
            body = json.loads(request.content)
            assert body["text"] == "" and body["typeIds"] == [1] and body["archive"] is False
            return httpx.Response(200, json=load("crawl/listing.json"))
        if path.endswith("/press/57987"):
            return httpx.Response(200, json=load("press.json"))
        raise AssertionError(f"Unexpected request: {request.url}")

    http = CrawlHttpClient(Settings(min_interval_seconds=0), httpx.MockTransport(handler))
    assert http.settings.min_interval_seconds == 2

    async def no_wait():
        pass

    monkeypatch.setattr(http, "_pace", no_wait)
    try:
        source = CrawlSource(http)
        assert await source.flows()
        assert await source.themes()
        assert (await source.listing(1)).data
        assert (await source.press("57987")).id == 57987
    finally:
        await http.close()
    assert len(calls) == 4


@pytest.mark.parametrize("header,wait", [(None, 30), ("bad", 30), ("90", 90)])
async def test_crawl_429_retains_cooldown_before_next_request(header, wait):
    http = CrawlHttpClient(Settings())
    before = time.monotonic()
    try:
        await http._cool_down(
            httpx.Response(429, headers={"retry-after": header} if header else {})
        )
        assert http._next_request >= before + wait
    finally:
        await http.close()


async def test_real_response_hook_applies_cooldown_even_on_last_attempt():
    http = CrawlHttpClient(
        Settings(attempts=1), httpx.MockTransport(lambda request: httpx.Response(429))
    )
    before = time.monotonic()
    try:
        with pytest.raises(SourceError):
            await http.json("GET", "https://veriportali.tuik.gov.tr/api/tr/dataflows")
        assert http._next_request >= before + 30
    finally:
        await http.close()


async def test_complete_listing_reconciles_removed_ids_but_count_mismatch_fails():
    source = Source()
    original = source.listing
    first = MetadataCrawler(source)
    await collect(first)

    async def single(page):
        batch = await original(1)
        batch.total = 1
        return batch

    source.listing = single
    smaller = MetadataCrawler(source, first.next_state)
    await collect(smaller)
    assert smaller.report["missing_from_listing"] == ["press:58058"]
    assert set(smaller.next_state.records) == {"press:57987"}

    async def empty(page):
        return SearchPage(data=[], total=2)

    source.listing = empty
    broken = MetadataCrawler(source, first.next_state)
    with pytest.raises(SourceError) as exc:
        await collect(broken)
    assert exc.value.code == "incomplete_listing" and broken.next_state is None


def test_source_dates_and_corrupt_state_are_explicit(tmp_path):
    assert portal_date("2026-10-26").isoformat() == "2026-10-26"
    assert portal_date("2026-10-26T10:00:00").isoformat().endswith("+03:00")
    assert portal_date("2026-10-26T07:00:00Z").utcoffset().total_seconds() == 0
    path = tmp_path / "state.json"
    path.write_text('{"version": 99, "records": {}}')
    with pytest.raises(ValueError, match="version"):
        CrawlState.load(path)


def test_cli_refuses_state_advancement_for_partial_or_overlapping_outputs(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "crawl_metadata", Path(__file__).parents[1] / "scripts/crawl_metadata.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for args in [
        ["--max-pages", "1"],
        [],
        ["--dry-run", "--state", str(tmp_path / "report.json")],
    ]:
        with pytest.raises(SystemExit):
            module.main(args + ["--report", str(tmp_path / "report.json")])


async def test_cli_commits_only_success_and_preserves_state_and_output_on_failure(
    monkeypatch, tmp_path
):
    spec = importlib.util.spec_from_file_location(
        "crawl_metadata_runner", Path(__file__).parents[1] / "scripts/crawl_metadata.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = Source()

    class Http:
        async def close(self):
            pass

    monkeypatch.setattr(module, "CrawlHttpClient", lambda *args: Http())
    monkeypatch.setattr(module, "CrawlSource", lambda http: source)
    args = Namespace(
        state=tmp_path / "state.json",
        output=tmp_path / "docs.jsonl",
        report=tmp_path / "report.json",
        ipv4=False,
        full=True,
        dry_run=False,
        max_pages=None,
        max_bulletins=None,
    )
    assert await module.run(args) == 0
    state, output = args.state.read_bytes(), args.output.read_bytes()
    assert json.loads(args.report.read_text())["state_committed"]
    source.fail = "detail"
    assert await module.run(args) == 1
    assert args.state.read_bytes() == state and args.output.read_bytes() == output
    assert json.loads(args.report.read_text())["state_committed"] is False
    source.fail = None
    args.dry_run, args.max_pages = True, 1
    assert await module.run(args) == 0
    assert args.state.read_bytes() == state
    assert json.loads(args.report.read_text())["partial"]
    args.state.write_text("invalid JSON")
    assert await module.run(args) == 1
    assert json.loads(args.report.read_text())["status"] == "error"
