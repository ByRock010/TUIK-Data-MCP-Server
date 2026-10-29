import importlib.util
import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from tuik_mcp.clients.base import HttpClient
from tuik_mcp.config import Settings
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
