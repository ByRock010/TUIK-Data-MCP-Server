"""The MCP vs web vs closed-book benchmark helpers (scripts/mcp_benchmark.py)."""

import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "mcp_benchmark", Path(__file__).parents[1] / "scripts" / "mcp_benchmark.py"
)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


@pytest.mark.parametrize(
    ("token", "spelling"),
    [
        ("8,4", "8.4"),
        ("193.793", "193 bin 793"),
        ("2.967", "2 967"),
        ("31.130", "31 milyar 130"),
        ("25,97", "25 milyar 97"),
    ],
)
def test_number_forms_cover_tuik_spellings(token, spelling):
    assert spelling in benchmark.number_forms(token)


def test_traceable_ignores_years_and_dates_and_reports_computed_values():
    answer = "27.10.2026 bülteni: 2026'da işsizlik %8,4; 2.967 / 35.412 = %8,38."
    evidence = "İşsizlik oranı 8,4 ... işsiz 2 967 bin, işgücü 35 412 bin"
    result = benchmark.traceable(answer, evidence)
    assert result == {"total": 4, "found": 3, "missing": ["8,38"]}


def test_summarize_run_extracts_calls_errors_cost_and_answer(tmp_path):
    events = [
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "a", "name": "mcp__tuik__search_tuik",
             "input": {"query": "işsizlik"}},
            {"type": "tool_use", "id": "b", "name": "mcp__tuik__get_tuik_data",
             "input": {"resource_id": "x"}},
        ]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "a",
             "content": [{"type": "text", "text": "oran 8,4"}]},
            {"type": "tool_result", "tool_use_id": "b",
             "content": '{"status":"error","code":"invalid_filters"}'},
        ]}},
        {"type": "result", "result": "Oran %8,4 oldu.", "num_turns": 3, "duration_ms": 2500,
         "total_cost_usd": 0.12, "stop_reason": "end_turn", "is_error": False},
    ]  # fmt: skip
    path = tmp_path / "mcp-P01.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\nnot json\n")
    run = benchmark.summarize_run(path)
    assert (run["condition"], run["id"]) == ("mcp", "P01")
    assert [c["name"] for c in run["tool_calls"]] == [
        "mcp__tuik__search_tuik",
        "mcp__tuik__get_tuik_data",
    ]
    assert run["tool_errors"] == 1 and run["cost_usd"] == 0.12 and run["duration_s"] == 2.5
    assert run["numbers"] == {"total": 1, "found": 1, "missing": []}


def test_conditions_expose_only_their_tools(tmp_path):
    mcp = benchmark.condition_args("mcp", tmp_path)
    web = benchmark.condition_args("web", tmp_path)
    closed = benchmark.condition_args("closed", tmp_path)
    assert mcp[mcp.index("--tools") + 1] == "" and "mcp__tuik__*" in mcp
    assert web[web.index("--tools") + 1] == "WebSearch,WebFetch"
    assert closed[closed.index("--tools") + 1] == "" and "--allowedTools" not in closed
    assert str(tmp_path / "empty-mcp.json") in web and str(tmp_path / "empty-mcp.json") in closed


def test_unknown_condition_is_rejected():
    with pytest.raises(SystemExit):
        benchmark.main(["run", "--prompts", "p.json", "--out", "o", "--conditions", "mcp,gpt"])
