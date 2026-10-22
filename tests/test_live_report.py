import importlib.util
import json
from pathlib import Path

import pytest


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
