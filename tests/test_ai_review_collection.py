"""Exercise the live recorder's protocol and failure checkpoints without network."""

import importlib.util
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "ai_review_collection", Path(__file__).parents[1] / "scripts/collect_ai_review.py"
)
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


@pytest.fixture
def fake_session(monkeypatch):
    calls = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def initialize(self):
            pass

        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(name="get_tuik_resource")])

        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            response = {"status": "ok", "source_url": "https://veriportali.tuik.gov.tr/"}
            if name == "get_tuik_resource":
                response["id"] = arguments["resource_id"]
            if name == "search_tuik":
                response["results"] = []
            return SimpleNamespace(isError=False, structuredContent=response)

    @asynccontextmanager
    async def stdio(params):
        yield None, None

    monkeypatch.setattr(collector, "stdio_client", stdio)
    monkeypatch.setattr(collector, "ClientSession", lambda *args: Session())
    monkeypatch.setattr(collector.subprocess, "check_output", lambda *args, **kwargs: "revision")
    return calls, Session


async def test_collector_keeps_live_rankings_separate_and_records_every_probe(
    tmp_path, fake_session, monkeypatch
):
    calls, _ = fake_session
    resource_id = "flow:DF_TEST+V1.0"
    title_id = "title:unknown:" + "a" * 64
    pool = {
        "queries": [{"id": "q1", "text": "işsizlik"}],
        "documents": {resource_id: {"id": resource_id}, title_id: {"id": title_id}},
    }
    input_path, output = tmp_path / "pool.json", tmp_path / "evidence.json"
    input_path.write_text(json.dumps(pool))
    before = input_path.read_bytes()
    monkeypatch.setattr(collector, "PROBES", (resource_id,))
    assert await collector.collect(input_path, output) == 0
    assert input_path.read_bytes() == before
    report = json.loads(output.read_text())
    assert report["status"] == "partial"  # Evaluation-only identity is explicitly skipped.
    assert len(report["calls"]) == 4
    assert title_id in report["skipped"] and title_id not in report["resources"]
    assert calls[0] == ("search_tuik", {"query": "işsizlik", "limit": 10, "live": True})
    assert report["probes"][resource_id] == {"inspection": 2, "rows": 3}
    assert not {"grades", "judge_a", "judge_b"} & set(report)
    assert not output.with_suffix(".json.tmp").exists()


async def test_collector_stops_after_repeated_failures_and_retains_diagnostics(
    tmp_path, fake_session, monkeypatch
):
    _, session = fake_session

    async def unavailable(self, *args):
        raise RuntimeError("Synthetic unavailable MCP transport")

    monkeypatch.setattr(session, "call_tool", unavailable)
    input_path, output = tmp_path / "pool.json", tmp_path / "evidence.json"
    input_path.write_text(
        json.dumps(
            {
                "queries": [{"id": f"q{i}", "text": "işsizlik"} for i in range(6)],
                "documents": {},
            }
        )
    )
    assert await collector.collect(input_path, output) == 1
    report = json.loads(output.read_text())
    assert report["status"] == "error" and len(report["calls"]) == 3
    assert all(call["outcome"] == "error" for call in report["calls"])
    assert "three consecutive" in report["error"] and report["finished_at"]


def test_collector_refuses_to_overwrite_existing_evidence(tmp_path):
    output = tmp_path / "evidence.json"
    output.write_text("keep")
    with pytest.raises(SystemExit):
        collector.main(["--pool", str(tmp_path / "missing.json"), "--output", str(output)])
    assert output.read_text() == "keep"
