import importlib.util
import json
import os
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tuik_mcp.clients.base import HttpClient
from tuik_mcp.config import Settings
from tuik_mcp.models.common import now
from tuik_mcp.search.documents import IndexDocument
from tuik_mcp.search.indexer import OpenSearchStore, prepare, rebuild
from tuik_mcp.server import respond
from tuik_mcp.services.catalog import CatalogService


@pytest.fixture
def smoke():
    path = Path(__file__).parents[1] / "scripts/live_smoke.py"
    spec = importlib.util.spec_from_file_location("live_smoke_report", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_success_report_is_utf8_json_and_exits_zero(smoke, monkeypatch, tmp_path, capsys):
    async def run():
        return {"tools": ["search_tuik"], "searches": {"işsizlik": []}}

    monkeypatch.setattr(smoke, "run", run)
    output = tmp_path / "nested/report.json"
    assert smoke.main(["--output", str(output)]) == 0
    report = json.loads(output.read_text())
    assert report["status"] == "ok" and report["checked_at"]
    assert "işsizlik" in report["searches"]
    assert json.loads(capsys.readouterr().out) == report


def test_failed_smoke_still_writes_json_with_nonzero_exit(smoke, monkeypatch, tmp_path, capsys):
    async def run():
        raise AssertionError("upstream contract changed")

    monkeypatch.setattr(smoke, "run", run)
    output = tmp_path / "report.json"
    assert smoke.main(["--output", str(output)]) == 1
    report = json.loads(output.read_text())
    assert report["status"] == "error"
    assert report["error_type"] == "AssertionError"
    assert report["message"] == "upstream contract changed"
    captured = capsys.readouterr()
    assert json.loads(captured.out) == report
    assert "Traceback" in captured.err


def test_original_stdout_usage_still_works(smoke, monkeypatch, capsys):
    async def run():
        return {"tools": []}

    monkeypatch.setattr(smoke, "run", run)
    assert smoke.main([]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


async def test_bulletin_csv_smoke_follows_explicit_dataflow_choice(smoke):
    fixtures = Path(__file__).parent / "fixtures"
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/tr/press/58347":
            return httpx.Response(
                200, json=json.loads((fixtures / "press_dataflows.json").read_text())
            )
        if request.url.path == "/api/tr/dataflows":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "DF_SATIS_SEKLI_SATIS_DURUMU_V3+V1.0",
                        "name": "Konut satışları",
                        "downloadable": True,
                    }
                ],
            )
        if request.url.path.endswith("/file/csv"):
            return httpx.Response(
                200,
                content=(fixtures / "data.csv").read_bytes(),
                headers={"content-type": "text/csv"},
            )
        raise AssertionError(request.url)

    http = HttpClient(Settings(min_interval_seconds=0), httpx.MockTransport(handler))
    service = CatalogService(http)

    async def call(name, arguments):
        operation = {"get_tuik_resource": service.resource, "get_tuik_data": service.data}[name]
        return await respond(operation(**arguments))

    try:
        report = await smoke.bulletin_to_csv(call, "press:58347")
        assert report["selection_status"] == "related_datasets"
        assert report["selected_dataset_id"] == "flow:DF_SATIS_SEKLI_SATIS_DURUMU_V3+V1.0"
        assert report["data"]["total_rows"] == 3 and len(report["data"]["rows"]) == 2
        assert report["data"]["snapshot_id"].startswith("sha256:")
        assert calls == [
            "/api/tr/press/58347",
            "/api/tr/dataflows",
            "/api/tr/dataflows/DF_SATIS_SEKLI_SATIS_DURUMU_V3+V1.0/file/csv",
        ]
    finally:
        await service.close()
        await http.close()


async def test_bulletin_excel_smoke_reads_an_explicit_attachment(smoke, catalog):
    service, calls = catalog

    async def call(name, arguments):
        assert name == "get_tuik_data"
        return await respond(service.data(**arguments))

    detail = (await service.resource("press:57987")).model_dump(mode="json")
    report = await smoke.bulletin_to_excel(call, detail)
    assert report["selected_file_id"].startswith("press:57987:file:")
    assert report["data"]["extraction"] == "xls" and len(report["data"]["rows"]) == 2
    assert report["data"]["source_url"] and report["data"]["retrieved_at"]
    assert sum(request.url.path.endswith("/downloads") for request in calls) == 1


async def test_bulletin_csv_smoke_rejects_an_implicit_dataset_selection(smoke):
    async def call(name, arguments):
        if name == "get_tuik_resource":
            return {"related_datasets": [], "source_url": "https://veriportali.tuik.gov.tr"}
        # Returning rows directly would conceal the required related-dataset choice.
        return {"status": "ok", "rows": [{"a": "1"}]}

    with pytest.raises(AssertionError):
        await smoke.bulletin_to_csv(call, "press:58347")


async def test_smoke_forwards_requested_pacing_to_stdio_server(smoke, monkeypatch):
    captured = []

    @asynccontextmanager
    async def capture(parameters):
        captured.append(parameters)
        raise RuntimeError("stop before networking")
        yield  # pragma: no cover

    monkeypatch.setenv("TUIK_MIN_INTERVAL_SECONDS", "2")
    monkeypatch.setenv("TUIK_DOWNLOAD_INTERVAL_SECONDS", "6")
    monkeypatch.setenv("UNRELATED_SECRET", "do-not-forward")
    monkeypatch.setattr(smoke, "stdio_client", capture)
    with pytest.raises(RuntimeError, match="stop before networking"):
        await smoke.run()
    assert captured[0].env["TUIK_MIN_INTERVAL_SECONDS"] == "2"
    assert captured[0].env["TUIK_DOWNLOAD_INTERVAL_SECONDS"] == "6"
    assert "UNRELATED_SECRET" not in captured[0].env


async def test_indexed_smoke_follows_exact_hit_through_resource_and_verified_csv(smoke, catalog):
    from tuik_mcp.search.backend import OpenSearchBackend

    service, requests = catalog
    resource_id = "flow:DF_ALTINSIZ_DTH+V1.0"
    stamp = "2026-11-03T10:00:00Z"

    def cluster(request):
        return httpx.Response(
            200,
            json={
                "hits": {
                    "total": {"value": 1, "relation": "eq"},
                    "hits": [
                        {
                            "_score": 4.0,
                            "_source": {
                                "id": resource_id,
                                "kind": "dataset",
                                "title": "Altın Hariç Dış Ticaret Hadleri",
                                "source_url": "https://veriportali.tuik.gov.tr/",
                                "indexed_at": stamp,
                                "retrieved_at": stamp,
                                "downloadable": True,
                                "formats": ["csv"],
                            },
                        }
                    ],
                },
                "aggregations": {
                    "indexed_at": {"value_as_string": stamp},
                    **{
                        key: {
                            "buckets": [],
                            "sum_other_doc_count": 0,
                            "doc_count_error_upper_bound": 0,
                        }
                        for key in ("kind", "theme")
                    },
                },
            },
        )

    service.index = OpenSearchBackend(
        OpenSearchStore("http://cluster", transport=httpx.MockTransport(cluster)), "fixture"
    )
    calls = []

    async def call(name, arguments):
        calls.append((name, arguments))
        operation = {
            "search_tuik": service.discover,
            "get_tuik_resource": service.resource,
            "get_tuik_data": service.data,
        }[name]
        return await respond(operation(**arguments))

    try:
        report = await smoke.indexed_to_data(call, "altin haric dis ticaret hadleri")
        assert [name for name, _ in calls] == ["search_tuik", "get_tuik_resource", "get_tuik_data"]
        assert all(args["resource_id"] == resource_id for _, args in calls[1:])
        assert report["data"]["resource_id"] == resource_id
        assert len(report["data"]["rows"]) == 2 and report["data"]["snapshot_id"]
        assert not any(request.url.path.endswith("/search") for request in requests)
    finally:
        await service.close()


@pytest.mark.parametrize("backend,source", [("literal", "catalog"), ("portal", "portal")])
async def test_indexed_smoke_rejects_successful_fallback_without_downloading(
    smoke, backend, source
):
    calls = []

    async def call(name, arguments):
        calls.append(name)
        return {"backend": backend, "source": source, "results": [{"id": "flow:DF_X+V1.0"}]}

    with pytest.raises(AssertionError):
        await smoke.indexed_to_data(call, "nufus")
    assert calls == ["search_tuik"]


async def test_indexed_only_requires_cluster_before_starting_stdio(smoke, monkeypatch):
    monkeypatch.delenv("TUIK_OPENSEARCH_URL", raising=False)
    with pytest.raises(ValueError, match="requires TUIK_OPENSEARCH_URL"):
        await smoke.run(indexed_only=True)


def test_cli_forwards_indexed_mode_and_writes_failure_report(smoke, monkeypatch, tmp_path):
    async def run(**options):
        assert options == {"indexed_only": True, "index_query": "nufus"}
        raise AssertionError("index unavailable; literal fallback rejected")

    monkeypatch.setattr(smoke, "run", run)
    output = tmp_path / "indexed.json"
    assert smoke.main(["--indexed-only", "--index-query", "nufus", "--output", str(output)]) == 1
    report = json.loads(output.read_text())
    assert report["status"] == "error" and "fallback rejected" in report["message"]


@pytest.mark.opensearch
@pytest.mark.skipif(not os.getenv("TUIK_OPENSEARCH_URL"), reason="Set TUIK_OPENSEARCH_URL")
async def test_indexed_smoke_through_real_stdio_and_cluster(smoke):
    alias = "tuik-release-smoke-test-" + uuid.uuid4().hex
    store = OpenSearchStore(os.environ["TUIK_OPENSEARCH_URL"])
    resource_id = "flow:DF_ALTINSIZ_DTH+V1.0"
    document = IndexDocument(
        id=resource_id,
        kind="dataset",
        title="Altın Hariç Dış Ticaret Hadleri",
        downloadable=True,
        formats=["csv"],
        source_url="https://veriportali.tuik.gov.tr/",
    )
    try:
        payloads, _ = prepare([document], now())
        await rebuild(store, alias, payloads)
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(Path(__file__).with_name("search_stdio_server.py")), "cluster"],
            env={
                "TUIK_OPENSEARCH_URL": os.environ["TUIK_OPENSEARCH_URL"],
                "TUIK_OPENSEARCH_INDEX": alias,
            },
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                async def call(name, arguments):
                    result = await session.call_tool(name, arguments)
                    assert not result.isError, result.content
                    return result.structuredContent

                report = await smoke.indexed_to_data(call, "altin haric dis ticaret hadleri")
                assert report["search"]["backend"] == "opensearch"
                assert report["resource"]["id"] == report["data"]["resource_id"] == resource_id
                assert any(
                    link["format"] == "csv" and link["verified"]
                    for link in report["data"]["download_links"]
                )
    finally:
        try:
            for name in await store.versions(alias):
                await store.delete(name)
        finally:
            await store.close()
