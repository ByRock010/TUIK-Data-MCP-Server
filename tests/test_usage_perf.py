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
