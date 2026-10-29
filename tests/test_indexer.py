"""Indexer orchestration offline, plus an opt-in fixture crawl → real cluster check (#16)."""

import json
import os
import uuid
from argparse import Namespace
from datetime import UTC, datetime

import httpx
import pytest
from test_metadata_crawl import Source

from tuik_mcp.search import indexer
from tuik_mcp.search.crawl import MetadataCrawler
from tuik_mcp.search.documents import IndexDocument
from tuik_mcp.search.index_cli import main, run
from tuik_mcp.search.indexer import (
    IndexerError,
    OpenSearchStore,
    prepare,
    rebuild,
    update,
)
from tuik_mcp.search.schema import SCHEMA_VERSION

ALIAS = "tuik-resources"


class FakeStore:
    """In-memory stand-in for the REST operations OpenSearchStore performs."""

    def __init__(self):
        self.indices: dict[str, dict[str, dict]] = {}
        self.aliases: dict[str, list[str]] = {}
        self.schema: dict[str, int] = {}
        self.reject: set[str] = set()
        self.lost_swap_response = False

    async def exists(self, name):
        return name in self.indices or name in self.aliases

    async def alias_targets(self, alias):
        return sorted(self.aliases.get(alias, []))

    async def versions(self, alias):
        return {
            name: int(name.rsplit("-v", 1)[1])
            for name in self.indices
            if name.startswith(alias + "-v")
        }

    async def schema_version(self, index):
        return self.schema[index]

    async def create(self, name):
        assert name not in self.indices
        self.indices[name], self.schema[name] = {}, SCHEMA_VERSION

    async def delete(self, name):
        self.indices.pop(name, None)

    async def refresh(self, index):
        pass

    async def count(self, index):
        return len(self.indices[index])

    async def swap(self, alias, index, previous):
        assert sorted(previous) == sorted(self.aliases.get(alias, []))
        self.aliases[alias] = [index]
        if self.lost_swap_response:
            raise IndexerError("connection reset after the alias moved")

    async def sources(self, index, ids):
        return {i: self.indices[index][i] for i in ids if i in self.indices[index]}

    async def ids(self, index):
        return set(self.indices[index])

    async def bulk(self, index, payloads):
        failures = []
        for payload in payloads:
            if payload["id"] in self.reject:
                failures.append({"id": payload["id"], "status": 400, "type": "x", "reason": "x"})
            else:
                self.indices[index][payload["id"]] = json.loads(json.dumps(payload))
        return failures


@pytest.fixture(autouse=True)
def default_alias(monkeypatch):
    monkeypatch.setenv("TUIK_OPENSEARCH_INDEX", ALIAS)


def arguments(tmp_path, **overrides):
    values = dict(
        full=False,
        incremental=False,
        dry_run=False,
        state=tmp_path / "state.json",
        report=tmp_path / "report.json",
    )
    return Namespace(**(values | overrides))


async def crawl_payloads():
    documents = [d async for d in MetadataCrawler(Source()).documents(full=True)]
    return prepare(documents, datetime(2026, 10, 27, tzinfo=UTC))


async def test_prepare_shares_indexed_at_and_rejects_duplicates_or_empty():
    payloads, kinds = await crawl_payloads()
    assert {p["indexed_at"] for p in payloads} == {"2026-10-27T00:00:00Z"}
    assert kinds == {"dataset": 4, "bulletin": 2, "metadata": 2}
    documents = [d async for d in MetadataCrawler(Source()).documents(full=True)]
    with pytest.raises(IndexerError, match="Duplicate"):
        prepare(documents + documents[:1], datetime.now(UTC))
    with pytest.raises(IndexerError, match="no documents"):
        prepare([], datetime.now(UTC))


async def test_full_build_swaps_alias_commits_state_and_dry_run_writes_nothing(tmp_path, capsys):
    store = FakeStore()
    args = arguments(tmp_path, full=True)
    assert await run(args, source=Source(), store=store) == 0
    report = json.loads(args.report.read_text())
    assert json.loads(capsys.readouterr().out) == report
    assert report["status"] == "ok" and report["state_committed"]
    assert report["index"] == ALIAS + "-v1" and report["previous_indices"] == []
    assert report["written"] == report["documents"] == 8
    assert report["counts"] == {"dataset": 4, "bulletin": 2, "metadata": 2}
    assert report["crawl"]["status"] == "ok" and report["indexed_at"]
    assert store.aliases[ALIAS] == [ALIAS + "-v1"] and len(store.indices[ALIAS + "-v1"]) == 8
    state = args.state.read_bytes()

    preview = arguments(tmp_path, full=True, dry_run=True, report=tmp_path / "preview.json")
    assert await run(preview, source=Source(), store=store) == 0
    planned = json.loads(preview.report.read_text())
    assert planned["index"] == ALIAS + "-v2" and planned["previous_indices"] == [ALIAS + "-v1"]
    assert "written" not in planned and not planned["state_committed"]
    assert set(store.indices) == {ALIAS + "-v1"} and args.state.read_bytes() == state

    assert await run(arguments(tmp_path, full=True), source=Source(), store=store) == 0
    assert store.aliases[ALIAS] == [ALIAS + "-v2"]
    # The previous build is retained for rollback; nothing is deleted implicitly.
    assert set(store.indices) == {ALIAS + "-v1", ALIAS + "-v2"}


async def test_rejected_document_discards_build_and_keeps_alias_and_state(tmp_path):
    store = FakeStore()
    assert await run(arguments(tmp_path, full=True), source=Source(), store=store) == 0
    state = (tmp_path / "state.json").read_bytes()
    store.reject = {"press:57987"}
    args = arguments(tmp_path, full=True)
    assert await run(args, source=Source(), store=store) == 1
    report = json.loads(args.report.read_text())
    assert report["status"] == "error" and not report["state_committed"]
    assert report["failure_count"] == 1 and report["failures"][0]["id"] == "press:57987"
    assert store.aliases[ALIAS] == [ALIAS + "-v1"] and ALIAS + "-v2" not in store.indices
    assert args.state.read_bytes() == state


async def test_build_served_by_the_alias_is_never_deleted_after_a_lost_response():
    store = FakeStore()
    store.lost_swap_response = True
    payloads, _ = await crawl_payloads()
    with pytest.raises(IndexerError, match="connection reset"):
        await rebuild(store, ALIAS, payloads)
    assert store.aliases[ALIAS] == [ALIAS + "-v1"] and ALIAS + "-v1" in store.indices


async def test_concrete_index_named_like_the_alias_is_refused():
    store = FakeStore()
    store.indices[ALIAS] = {}
    payloads, _ = await crawl_payloads()
    with pytest.raises(IndexerError, match="concrete index"):
        await rebuild(store, ALIAS, payloads)
    assert set(store.indices) == {ALIAS}


async def test_incremental_requires_one_served_index_with_the_current_schema():
    store = FakeStore()
    payloads, _ = await crawl_payloads()
    with pytest.raises(IndexerError, match="full build"):
        await update(store, ALIAS, payloads)
    await rebuild(store, ALIAS, payloads)
    store.schema[ALIAS + "-v1"] = SCHEMA_VERSION + 1
    with pytest.raises(IndexerError, match="schema version"):
        await update(store, ALIAS, payloads)
    store.schema[ALIAS + "-v1"] = SCHEMA_VERSION
    store.aliases[ALIAS].append("other")
    with pytest.raises(IndexerError, match="several"):
        await update(store, ALIAS, payloads)


async def test_incremental_upserts_only_new_or_changed_and_reports_stale_ids():
    store = FakeStore()
    payloads, _ = await crawl_payloads()
    await rebuild(store, ALIAS, payloads)
    served = store.indices[ALIAS + "-v1"]
    del served["press:58058"]
    served["press:57987"]["title"] = "Outdated title"
    served["press:1"] = {"id": "press:1"}
    # A later snapshot: indexed_at alone never counts as a change.
    later, _ = prepare(
        [IndexDocument.model_validate(p) for p in payloads], datetime(2027, 1, 1, tzinfo=UTC)
    )

    plan = await update(store, ALIAS, later, dry_run=True)
    assert (plan["created"], plan["updated"], plan["unchanged"]) == (1, 1, 6)
    assert plan["stale_count"] == 1 and plan["stale_ids"] == ["press:1"]
    assert "written" not in plan and served["press:57987"]["title"] == "Outdated title"

    result = await update(store, ALIAS, later)
    assert result["written"] == 2 and served["press:57987"]["title"] != "Outdated title"
    assert served["press:58058"]["indexed_at"] == "2027-01-01T00:00:00Z"
    assert served["press:1"] == {"id": "press:1"}  # Removal belongs to a full build.
    unchanged = next(i for i in served if i.startswith("flow:"))
    assert served[unchanged]["indexed_at"] == "2026-10-27T00:00:00Z"


async def test_incremental_cli_reuses_state_and_failures_keep_state(tmp_path):
    store = FakeStore()
    assert await run(arguments(tmp_path, full=True), source=Source(), store=store) == 0
    source = Source()
    args = arguments(tmp_path)
    assert await run(args, source=source, store=store) == 0
    report = json.loads(args.report.read_text())
    assert report["mode"] == "incremental" and report["crawl"]["reused_bulletins"] == 2
    assert not any(call.startswith("press:") for call in source.calls)
    # Catalog retrieval time refreshes datasets; reused bulletin details are unchanged.
    assert (report["created"], report["updated"], report["unchanged"]) == (0, 4, 4)
    state = args.state.read_bytes()
    store.reject = {"flow:DF_ISGUCU_AYLIK_TEMEL_ISGUCU_C+V1.0"}
    assert await run(args, source=Source(), store=store) == 1
    assert json.loads(args.report.read_text())["failure_count"] == 1
    assert args.state.read_bytes() == state


async def test_incomplete_crawl_or_missing_cluster_writes_nothing(tmp_path, monkeypatch):
    store = FakeStore()
    source = Source()
    source.fail = "detail"
    args = arguments(tmp_path, full=True)
    assert await run(args, source=source, store=store) == 1
    report = json.loads(args.report.read_text())
    assert report["crawl"]["status"] == "error" and not store.indices
    assert not args.state.exists() and not report["state_committed"]

    monkeypatch.delenv("TUIK_OPENSEARCH_URL", raising=False)
    assert await run(args, source=Source()) == 1
    assert "TUIK_OPENSEARCH_URL" in json.loads(args.report.read_text())["error"]["message"]


async def test_unusable_alias_fails_before_the_crawl(tmp_path):
    store = FakeStore()
    source = Source()
    assert await run(arguments(tmp_path), source=source, store=store) == 1
    report = json.loads((tmp_path / "report.json").read_text())
    assert "run a full build first" in report["error"]["message"]
    assert source.calls == [] and "crawl" not in report
    store.indices[ALIAS] = {}
    assert await run(arguments(tmp_path, full=True), source=source, store=store) == 1
    assert source.calls == [] and set(store.indices) == {ALIAS}


def test_cli_rejects_conflicting_modes_and_shared_paths(tmp_path):
    with pytest.raises(SystemExit):
        main(["--full", "--incremental"])
    with pytest.raises(SystemExit):
        main(["--state", str(tmp_path / "x.json"), "--report", str(tmp_path / "x.json")])


async def test_store_chunks_bulk_requests_and_reads_cluster_responses(monkeypatch):
    monkeypatch.setattr(indexer, "BULK_MAX_DOCUMENTS", 2)
    requests = []

    def handler(request):
        requests.append(request)
        path = request.url.path
        if path == "/_bulk":
            lines = request.content.decode().splitlines()
            assert request.headers["content-type"] == "application/x-ndjson"
            items = []
            for action in lines[::2]:
                doc_id = json.loads(action)["index"]["_id"]
                if doc_id == "press:2":
                    items.append(
                        {
                            "index": {
                                "_id": doc_id,
                                "status": 400,
                                "error": {"type": "document_parsing_exception", "reason": "bad"},
                            }
                        }
                    )
                else:
                    items.append({"index": {"_id": doc_id, "status": 201, "result": "created"}})
            return httpx.Response(200, json={"errors": True, "items": items})
        if path == "/_alias/tuik-resources":
            return httpx.Response(404, json={"error": "alias [tuik-resources] missing"})
        if path == "/_cat/indices/tuik-resources-v*":
            names = ["tuik-resources-v2", "tuik-resources-v10", "tuik-resources-v01", "other"]
            return httpx.Response(200, json=[{"index": name} for name in names])
        if path == "/served/_search":
            after = json.loads(request.content).get("search_after")
            hits = [] if after else [{"_id": "press:1", "sort": ["press:1"]}]
            return httpx.Response(200, json={"hits": {"hits": hits}})
        return httpx.Response(500, text="unexpected")

    store = OpenSearchStore("http://cluster", transport=httpx.MockTransport(handler))
    try:
        failures = await store.bulk("v1", [{"id": f"press:{n}"} for n in range(1, 4)])
        assert len([r for r in requests if r.url.path == "/_bulk"]) == 2
        assert failures == [
            {"id": "press:2", "status": 400, "type": "document_parsing_exception", "reason": "bad"}
        ]
        assert await store.alias_targets("tuik-resources") == []
        assert await store.versions("tuik-resources") == {
            "tuik-resources-v2": 2,
            "tuik-resources-v10": 10,
        }
        assert await store.ids("served") == {"press:1"}
        with pytest.raises(IndexerError, match="HTTP 500"):
            await store.count("served")
    finally:
        await store.close()


URL = os.getenv("TUIK_OPENSEARCH_URL")


@pytest.mark.opensearch
@pytest.mark.skipif(not URL, reason="Set TUIK_OPENSEARCH_URL")
async def test_fixture_crawl_is_searchable_through_a_swapped_alias(tmp_path, monkeypatch):
    alias = "tuik-index-test-" + uuid.uuid4().hex
    monkeypatch.setenv("TUIK_OPENSEARCH_INDEX", alias)
    store = OpenSearchStore(URL)
    client = httpx.AsyncClient(base_url=URL.rstrip("/") + "/", trust_env=False, timeout=30)

    async def search(text):
        response = await client.post(
            f"{alias}/_search",
            json={"query": {"multi_match": {"query": text, "fields": ["title", "title.ascii"]}}},
        )
        response.raise_for_status()
        return [hit["_id"] for hit in response.json()["hits"]["hits"]]

    try:
        args = arguments(tmp_path, full=True)
        assert await run(args, source=Source(), store=store) == 0
        assert await store.alias_targets(alias) == [alias + "-v1"]
        # ASCII typing reaches Turkish titles through the packaged analyzers.
        assert "press:58058" in await search("kumes hayvanciligi")
        assert "flow:DF_ISGUCU_AYLIK_TEMEL_ISGUCU_C+V1.0" in await search("işgücü göstergeleri")

        incremental = arguments(tmp_path, report=tmp_path / "incremental.json")
        assert await run(incremental, source=Source(), store=store) == 0
        report = json.loads(incremental.report.read_text())
        assert (report["created"], report["updated"], report["unchanged"]) == (0, 4, 4)
        assert report["index"] == alias + "-v1" and report["stale_count"] == 0

        assert await run(arguments(tmp_path, full=True), source=Source(), store=store) == 0
        assert await store.alias_targets(alias) == [alias + "-v2"]
        assert await store.exists(alias + "-v1")

        # The cluster rejects a keyword term over its byte limit: the build is discarded.
        payloads, _ = await crawl_payloads()
        payloads[0]["theme"] = ["x" * 40_000]
        with pytest.raises(IndexerError) as rejected:
            await rebuild(store, alias, payloads)
        assert rejected.value.failures[0]["id"] == payloads[0]["id"]
        assert await store.alias_targets(alias) == [alias + "-v2"]
        assert not await store.exists(alias + "-v3")
        assert await store.count(alias) == 8
    finally:
        for name in await store.versions(alias):
            await store.delete(name)
        await client.aclose()
        await store.close()
