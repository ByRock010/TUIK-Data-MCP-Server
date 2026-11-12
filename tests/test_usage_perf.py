"""The usage and performance harness's own logic (scripts/usage_perf.py)."""

import importlib.util
from collections import Counter
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "usage_perf", Path(__file__).parents[1] / "scripts" / "usage_perf.py"
)
perf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(perf)

TOOLS = {
    "search_tuik",
    "search_tuik_datasets",
    "get_tuik_resource",
    "get_tuik_data",
    "inspect_tuik_data",
    "chart_tuik_data",
    "list_tuik_themes",
    "read_tuik_document",
    "search_tuik_classifications",
}


def test_steps_have_unique_ids_and_cover_every_tool():
    steps = perf.steps()
    assert max(Counter(step.id for step in steps).values()) == 1
    assert {step.tool for step in steps} == TOOLS
    assert {step.group for step in steps} == {"discovery", "bulletin", "csv", "excel", "pdf"}


def test_upstream_requests_are_counted_by_destination():
    lines = [
        'INFO HTTP Request: POST http://localhost:9200/tuik-resources/_search "HTTP/1.1 200 OK"',
        'INFO HTTP Request: GET https://veriportali.tuik.gov.tr/api/tr/dataflows "HTTP/1.1 200 OK"',
        'INFO HTTP Request: GET https://siniflama.tuik.gov.tr/x "HTTP/1.1 200 OK"',
        "INFO Processing request of type CallToolRequest",
    ]
    assert perf.count_requests(lines, "localhost:9200") == {"tuik": 2, "index": 1}
    assert perf.count_requests(lines, None) == {"tuik": 3, "index": 0}


def test_checks_fail_with_a_reason_and_pass_silently():
    perf.error("not_found")({"status": "error", "code": "not_found"}, {})
    with pytest.raises(AssertionError, match="expected error not_found, got error/malformed"):
        perf.error("not_found")({"status": "error", "code": "malformed_response"}, {})
    rows = {"status": "ok", "rows": [{}], "response_bytes": 64 * 1024}
    perf.within_budget(rows, {})
    with pytest.raises(AssertionError, match="65537 bytes"):
        perf.within_budget(rows | {"response_bytes": 64 * 1024 + 1}, {})


def test_table_marks_failures_skips_and_earlier_medians():
    step = {"id": "a", "group": "csv", "tool": "get_tuik_data"}
    cold = {"ms": 12.4, "bytes": 100, "requests": {"tuik": 1, "index": 0}, "ok": False}
    warm = perf.spread([3.0, 1.0, 2.0])
    assert warm == {"n": 3, "median_ms": 2.0, "min_ms": 1.0, "max_ms": 3.0}
    report = {"steps": [step | {"cold": cold, "warm": warm}, {"id": "b", "tool": "search_tuik"}]}
    before = {"steps": [step | {"cold": cold, "warm": warm | {"median_ms": 9.5}}]}
    lines = perf.table(report, before).splitlines()
    assert lines[0].endswith("| Warm before |") and lines[1].count("---") == 9
    assert lines[2] == "| a | get_tuik_data | FAIL | 12 | 2.0 | 3.0 | 100 | 1 TÜİK, 0 index | 9.5 |"
    assert "| b | search_tuik | skipped |" in lines[3]


def entry(step_id, warm_ok=True):
    cold = {"ms": 5.0, "bytes": 10, "requests": {"tuik": 0, "index": 0}, "ok": True}
    warm = perf.spread([1.0, 2.0]) | {"ok": warm_ok, "failure": None if warm_ok else "boom"}
    return {"id": step_id, "group": "csv", "tool": "get_tuik_data", "cold": cold, "warm": warm}


def test_a_failed_warm_repeat_fails_the_step_the_summary_the_table_and_the_exit_code(
    monkeypatch,
):
    steps = [entry("fine"), entry("cache_regression", warm_ok=False)]
    summary = perf.summarize(steps)
    assert summary["passed"] == 1 and summary["failed"] == ["cache_regression"]
    rows = perf.table({"steps": steps}).splitlines()
    assert "| fine | get_tuik_data | ok |" in rows[2]
    assert "| cache_regression | get_tuik_data | FAIL |" in rows[3]

    async def fake_run(args):
        return {
            "steps": steps,
            "summary": summary,
            "startup": {},
            "rss_after_cold_mb": None,
            "rss_after_warm_mb": None,
        }

    monkeypatch.setattr(perf, "run", fake_run)
    assert perf.main([]) == 1
    steps[1] = entry("cache_regression")
    fake_summary = perf.summarize(steps)
    monkeypatch.setattr(perf, "run", lambda args: _ok(steps, fake_summary))
    assert perf.main([]) == 0


async def _ok(steps, summary):
    return {
        "steps": steps,
        "summary": summary,
        "startup": {},
        "rss_after_cold_mb": None,
        "rss_after_warm_mb": None,
    }


@pytest.mark.parametrize("group", ["excel", "pdf"])
def test_focused_excel_and_pdf_runs_keep_the_bulletin_they_read_links_from(group):
    plan = perf.select(perf.steps(), [group])
    ids = [step.id for step in plan]
    assert ids[0] == "bulletin" and {s.group for s in plan} == {"bulletin", group}
    # Every selected step can resolve its arguments from the earlier selected results.
    done = {"bulletin": {"download_links": [
        {"format": "xls", "resource_id": "press:1:file:a"},
        {"format": "pdf", "resource_id": "press:1:file:b"},
    ]}}  # fmt: skip
    for step in plan:
        if callable(step.arguments):
            step.arguments(done)


def test_other_selections_and_the_full_plan_are_unchanged():
    everything = perf.steps()
    assert perf.select(everything, None) == everything
    assert {s.group for s in perf.select(everything, ["csv"])} == {"csv"}
    both = [s.id for s in perf.select(everything, ["excel", "pdf"])]
    assert both.count("bulletin") == 1
