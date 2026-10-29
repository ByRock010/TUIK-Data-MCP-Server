"""Exact types, budgets and authenticated snapshot pagination (#52)."""

import asyncio
import io
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pydantic_core
import pytest
from openpyxl import Workbook
from pydantic import ValidationError

from tuik_mcp.clients.base import HttpClient
from tuik_mcp.config import PORTAL, Settings
from tuik_mcp.models.query import TableQuery
from tuik_mcp.models.search import TuikSearchResult
from tuik_mcp.parsers.models import ParsedTable
from tuik_mcp.parsers.query import QueryError, QueryLimits, evaluate
from tuik_mcp.server import respond
from tuik_mcp.services import files
from tuik_mcp.services.catalog import CatalogService

FIXTURES = Path(__file__).parent / "fixtures"
FLOW = "flow:DF_ALTINSIZ_DTH+V1.0"
FILE = "resource:" + "b" * 64
URL = PORTAL + "/api/tr/data/downloads?t=i&p=query"


def run_query(columns, rows, query, filters=None, limits=None):
    table = ParsedTable(
        columns=columns,
        rows=[dict(zip(columns, r, strict=True)) for r in rows],
        total_rows=len(rows),
        extraction="csv",
    )
    return evaluate(table, TableQuery.model_validate(query), filters or {}, limits or QueryLimits())


def test_compound_membership_numeric_range_sort_and_projection():
    columns, rows = run_query(
        ["il", "yıl", "nüfus"],
        [
            ["İstanbul", "2024", "10"],
            ["Ankara", "2024", "2"],
            ["İzmir", "2024", "3"],
            ["Ankara", "2023", "1"],
        ],
        {
            "columns": ["il", "nüfus"],
            "predicates": [
                {"op": "in", "column": "il", "values": ["İstanbul", "Ankara"]},
                {
                    "op": "range",
                    "column": "nüfus",
                    "value_type": "number",
                    "minimum": "2",
                    "maximum": "10",
                },
            ],
            "sort": [{"column": "nüfus", "value_type": "number"}],
        },
        filters={"yıl": "2024"},
    )
    assert columns == ["il", "nüfus"]
    assert rows == [{"il": "Ankara", "nüfus": "2"}, {"il": "İstanbul", "nüfus": "10"}]


@pytest.mark.parametrize(
    "value_type,expected",
    [
        ("text", ["1", "10", "2"]),
        ("number", ["1", "2", "10"]),
    ],
)
def test_numeric_sort_is_not_lexical(value_type, expected):
    _, rows = run_query(
        ["n"],
        [["10"], ["2"], ["1"]],
        {
            "sort": [{"column": "n", "value_type": value_type}],
        },
    )
    assert [r["n"] for r in rows] == expected


def test_missing_markers_are_distinct_from_zero_and_sort_order_is_explicit():
    source = [[""], ["-"], ["0"], ["2"], ["x"], ["1"]]
    _, rows = run_query(
        ["n"],
        source,
        {
            "sort": [
                {
                    "column": "n",
                    "value_type": "number",
                    "direction": "desc",
                    "missing": "first",
                }
            ]
        },
    )
    assert [r["n"] for r in rows] == ["", "-", "x", "2", "1", "0"]
    _, rows = run_query(
        ["n"],
        source,
        {
            "predicates": [
                {
                    "op": "range",
                    "column": "n",
                    "value_type": "number",
                    "minimum": "0",
                    "maximum": "0",
                }
            ]
        },
    )
    assert rows == [{"n": "0"}]
    _, rows = run_query(["n"], source, {"predicates": [{"op": "eq", "column": "n", "value": "-"}]})
    assert rows == [{"n": "-"}]


def test_multikey_sort_retains_source_positions_for_duplicate_values_and_rows():
    _, rows = run_query(
        ["a", "b", "id"],
        [
            ["B", "2", "0"],
            ["A", "2", "1"],
            ["A", "10", "2"],
            ["A", "2", "3"],
            ["A", "2", "3"],
        ],
        {"sort": [{"column": "a"}, {"column": "b", "value_type": "number", "direction": "desc"}]},
    )
    assert [r["id"] for r in rows] == ["2", "1", "3", "3", "0"]


@pytest.mark.parametrize(
    "kind,fmt,source,minimum,maximum,expected",
    [
        (
            "date",
            None,
            ["2025-01-02", "2024-12-31", "2025-01-01"],
            "2024-12-31",
            "2025-01-01",
            ["2024-12-31", "2025-01-01"],
        ),
        ("period", "year", ["2025", "2023", "2024"], "2023", "2024", ["2023", "2024"]),
        (
            "period",
            "month",
            ["2025M01", "2024-12", "2024-02"],
            "2024-12",
            "2025-01",
            ["2024-12", "2025M01"],
        ),
        ("period", "quarter", ["2025-Q1", "2024Q4"], "2024-Q4", "2025-Q1", ["2024Q4", "2025-Q1"]),
        ("period", "half", ["2025-H1", "2024-S2"], "2024-H2", "2025-S1", ["2024-S2", "2025-H1"]),
        (
            "period",
            "week",
            ["2025-W01", "2024W52"],
            "2024-W52",
            "2025-W01",
            ["2024W52", "2025-W01"],
        ),
    ],
)
def test_inclusive_date_and_declared_period_ranges(kind, fmt, source, minimum, maximum, expected):
    options = {"value_type": kind} | ({"period_format": fmt} if fmt else {})
    _, rows = run_query(
        ["t"],
        [[s] for s in source],
        {
            "predicates": [
                {"op": "range", "column": "t", "minimum": minimum, "maximum": maximum, **options}
            ],
            "sort": [{"column": "t", **options}],
        },
    )
    assert [r["t"] for r in rows] == expected


def test_decimal_comma_is_observed_but_ambiguous_notation_needs_explicit_format():
    _, rows = run_query(
        ["n"], [["1,5"], ["10,2"], ["2"]], {"sort": [{"column": "n", "value_type": "number"}]}
    )
    assert [r["n"] for r in rows] == ["1,5", "2", "10,2"]
    with pytest.raises(QueryError, match="decimal_separator"):
        run_query(
            ["n"], [["1.234"], ["5.678"]], {"sort": [{"column": "n", "value_type": "number"}]}
        )
    _, rows = run_query(
        ["n"],
        [["5.678"], ["1.234"]],
        {
            "sort": [
                {
                    "column": "n",
                    "value_type": "number",
                    "decimal_separator": ".",
                }
            ]
        },
    )
    assert rows[0]["n"] == "1.234"


@pytest.mark.parametrize(
    "values,options",
    [
        (["1.2", "1,2"], {"value_type": "number"}),
        (["1.234,56"], {"value_type": "number", "decimal_separator": ","}),
        (["NaN"], {"value_type": "number"}),
        (["01/02/2024"], {"value_type": "date"}),
        (["2024-02-30"], {"value_type": "date"}),
        (["2024-Q1"], {"value_type": "period", "period_format": "month"}),
        (["2021-W53"], {"value_type": "period", "period_format": "week"}),
    ],
)
def test_incompatible_values_are_actionable_validation_errors(values, options):
    with pytest.raises(QueryError) as error:
        run_query(["n"], [[v] for v in values], {"sort": [{"column": "n", **options}]})
    assert error.value.code == "invalid_query"


@pytest.mark.parametrize(
    "query",
    [
        {"columns": ["unknown"]},
        {"columns": ["a"], "predicates": [{"op": "eq", "column": "unknown", "value": "1"}]},
        {"columns": ["a"], "sort": [{"column": "unknown"}]},
    ],
)
def test_all_referenced_columns_are_validated_even_outside_projection(query):
    with pytest.raises(QueryError) as error:
        run_query(["a"], [["1"]], query)
    assert error.value.code == "invalid_columns"


def test_legacy_overlap_and_reversed_or_missing_range_bounds_are_rejected():
    with pytest.raises(QueryError, match="either legacy"):
        run_query(
            ["a"],
            [["1"]],
            {"predicates": [{"op": "eq", "column": "a", "value": "1"}]},
            filters={"a": "1"},
        )
    for bounds in [{"minimum": "2", "maximum": "1"}, {"minimum": "-"}]:
        with pytest.raises(QueryError):
            run_query(
                ["a"],
                [["1"]],
                {"predicates": [{"op": "range", "column": "a", "value_type": "number", **bounds}]},
            )


@pytest.mark.parametrize(
    "query",
    [
        {"columns": []},
        {"columns": ["a", "a"]},
        {"columns": ["a"] * 51},
        {"sort": [{"column": "a"}] * 6},
        {"sort": [{"column": "a"}] * 2},
        {"predicates": [{"op": "eq", "column": "a", "value": "1"}] * 21},
        {"predicates": [{"op": "in", "column": "a", "values": []}]},
        {"predicates": [{"op": "in", "column": "a", "values": ["x"] * 101}]},
        {"predicates": [{"op": "eq", "column": "a", "value": 1}]},
        {"predicates": [{"op": "range", "column": "a", "value_type": "number"}]},
        {"sort": [{"column": "a", "value_type": "period"}]},
        {"sort": [{"column": "a", "decimal_separator": ","}]},
        {"sql": "select *"},
    ],
)
def test_public_query_schema_rejects_unbounded_or_ambiguous_inputs(query):
    with pytest.raises(ValidationError):
        TableQuery.model_validate(query)


def service(body, kind="csv", **settings):
    calls, content = [], [body]

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/tr/dataflows":
            return httpx.Response(200, json=json.loads((FIXTURES / "flows.json").read_text()))
        types = {
            "csv": "text/csv",
            "xls": "application/vnd.ms-excel",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "pdf": "application/pdf",
        }
        return httpx.Response(200, content=content[0], headers={"content-type": types[kind]})

    http = HttpClient(
        Settings(min_interval_seconds=0, download_interval_seconds=0, attempts=1, **settings),
        httpx.MockTransport(handler),
    )
    catalog = CatalogService(http)
    catalog.remember(
        TuikSearchResult(id=FILE, title="Tablo", resource_type="table", source_url=URL)
    )
    return http, catalog, calls, content


async def pages(catalog, resource, query, limit=2, **kwargs):
    first = await catalog.data(resource, query=query, limit=limit, **kwargs)
    result, page = list(first.rows), first
    count = 1
    while page.has_more:
        assert page.next_cursor
        page = await catalog.data(resource, cursor=page.next_cursor, limit=limit)
        assert (
            page.snapshot_id,
            page.source_url,
            page.retrieved_at,
            page.sheet,
            page.total_rows,
        ) == (
            first.snapshot_id,
            first.source_url,
            first.retrieved_at,
            first.sheet,
            first.total_rows,
        )
        result.extend(page.rows)
        count += 1
        assert count <= first.total_rows + 1
    assert page.next_cursor is None and not page.truncated
    return first, result, count


async def test_csv_query_pages_are_exact_complete_and_never_redownload_or_reparse(monkeypatch):
    http, catalog, calls, content = service(
        b"n;city\n10;Ankara\n2;Izmir\n2;Izmir\n1;Ankara\n3;Ankara\n"
    )
    parses, original = [], files.parse_source
    monkeypatch.setattr(
        files, "parse_source", lambda *a, **k: parses.append(1) or original(*a, **k)
    )
    query = {"columns": ["city", "n"], "sort": [{"column": "n", "value_type": "number"}]}
    try:
        first = await catalog.data(FLOW, query=query, limit=2)
        content[0] = b"n;city\n999;Changed\n"
        next_page = await catalog.data(FLOW, cursor=first.next_cursor, query=query, limit=2)
        replay = await catalog.data(FLOW, cursor=first.next_cursor, limit=2)
        assert next_page == replay
        last = await catalog.data(FLOW, cursor=next_page.next_cursor, limit=2)
        assert [r["n"] for r in first.rows + next_page.rows + last.rows] == [
            "1",
            "2",
            "2",
            "3",
            "10",
        ]
        assert last.total_rows == 5 and not last.has_more and last.next_cursor is None
        assert first.retrieved_at == last.retrieved_at and first.snapshot_id == last.snapshot_id
        _, rows, count = await pages(catalog, FLOW, query)
        assert len(rows) == 5 and count == 3
        assert len(parses) == 1 and len(calls) == 2  # catalog + one CSV download
    finally:
        await catalog.close()
        await http.close()


@pytest.mark.parametrize(
    "name,kind,query",
    [
        (
            "labour-basic.xls",
            "xls",
            {
                "columns": ["Yıllar - Years [column 1]"],
                "predicates": [
                    {"op": "in", "column": "Yıllar - Years [column 1]", "values": ["2005", "2006"]}
                ],
                "sort": [
                    {
                        "column": "Yıllar - Years [column 1]",
                        "value_type": "period",
                        "period_format": "year",
                    }
                ],
            },
        ),
        ("cpi-basket.xlsx", "xlsx", {"columns": ["Kod Code"], "sort": [{"column": "Kod Code"}]}),
    ],
)
async def test_real_xls_xlsx_queries_paginate_without_losing_provenance(name, kind, query):
    http, catalog, calls, _ = service((FIXTURES / "files" / name).read_bytes(), kind)
    try:
        whole = await catalog.data(FILE, query=query, limit=1000)
        first, result, count = await pages(catalog, FILE, query, limit=37)
        assert result == whole.rows and len(result) == whole.total_rows and count >= 1
        assert first.sheet and first.table_title and first.format == kind
        assert first.source_url == URL and first.snapshot_id.startswith("sha256:")
        assert len(calls) == 1
        unknown = await respond(catalog.data(FILE, query={}, sheet="absent"))
        assert unknown["code"] == "invalid_input"
    finally:
        await catalog.close()
        await http.close()


@pytest.mark.parametrize(
    "body,expected_pages",
    [(b"a;b\n", 1), (b"a;b\n1;x\n2;y\n", 1), (b"a;b\n1;x\n2;y\n3;z\n4;w\n", 2)],
)
async def test_empty_tables_and_exact_page_boundaries(body, expected_pages):
    http, catalog, _, _ = service(body)
    try:
        first, rows, count = await pages(catalog, FLOW, {})
        assert count == expected_pages and len(rows) == first.total_rows
        none = await catalog.data(
            FLOW, query={"predicates": [{"op": "eq", "column": "a", "value": "absent"}]}
        )
        assert none.rows == [] and none.total_rows == 0 and not none.has_more
    finally:
        await http.close()


@pytest.mark.parametrize(
    "case,expected",
    [
        ("tampered", "invalid_cursor"),
        ("malformed", "invalid_cursor"),
        ("restart", "invalid_cursor"),
        ("resource", "cursor_mismatch"),
        ("query", "cursor_mismatch"),
        ("filters", "cursor_mismatch"),
        ("sheet", "cursor_mismatch"),
        ("ttl", "cursor_expired"),
        ("file_ttl", "cursor_expired"),
        ("file_eviction", "cursor_expired"),
        ("query_eviction", "cursor_expired"),
    ],
)
async def test_cursor_expiry_eviction_tampering_and_mismatches_never_download(
    monkeypatch, case, expected
):
    clock = [0.0]
    monkeypatch.setattr("tuik_mcp.cache.time", SimpleNamespace(monotonic=lambda: clock[0]))
    http, catalog, calls, _ = service(b"a;b\n1;x\n2;y\n3;z\n", query_cursor_ttl_seconds=10)
    try:
        if case == "file_ttl":
            await catalog.data(FLOW, limit=1)
            clock[0] = 3595  # Query state is fresh, retained file is nearly expired.
        first = await catalog.data(FLOW, query={}, limit=1)
        kwargs = {"cursor": first.next_cursor}
        resource = FLOW
        if case == "tampered":
            kwargs["cursor"] = first.next_cursor[:-1] + (
                "0" if first.next_cursor[-1] != "0" else "1"
            )
        elif case == "malformed":
            kwargs["cursor"] = "bad"
        elif case == "restart":
            catalog = CatalogService(http)
        elif case == "resource":
            resource = "flow:OTHER+V1.0"
        elif case == "query":
            kwargs["query"] = {"columns": ["a"]}
        elif case == "filters":
            kwargs["filters"] = {"a": "2"}
        elif case == "sheet":
            kwargs["sheet"] = "Other"
        elif case in {"ttl", "file_ttl"}:
            clock[0] = 11 if case == "ttl" else 3601
        else:
            cache = catalog.files.cache if case == "file_eviction" else catalog.queries.cache
            cache.max_bytes = 1
            cache.set("evict", b"x")
        error = await respond(catalog.data(resource, **kwargs))
        assert error["code"] == expected and "restart" in error["message"]
        assert len(calls) == 2
    finally:
        await http.close()


@pytest.mark.parametrize(
    "settings",
    [
        {"query_max_rows": 1},
        {"query_max_cells": 2},
        {"query_max_sort_rows": 1},
        {"query_max_snapshot_bytes": 1024},
        {"query_cache_bytes": 1024},
    ],
)
async def test_budget_failures_never_claim_a_partial_total(settings):
    body = ("a;b\n" + "".join(f"{n};{'v' * 200}\n" for n in range(10))).encode()
    http, catalog, _, _ = service(body, **settings)
    try:
        error = await respond(catalog.data(FLOW, query={"sort": [{"column": "a"}]}, limit=1))
        assert error["code"] in {"query_limit_exceeded", "parse_limit_exceeded"}
        assert "total_rows" not in error
    finally:
        await http.close()


async def test_utf8_output_budget_shortens_pages_without_skipping_rows():
    body = ("a;b\n" + "".join(f"{n};{'ş' * 150}\n" for n in range(12))).encode()
    http, catalog, _, _ = service(body, query_max_output_bytes=4096)
    try:
        page = await catalog.data(FLOW, query={}, limit=12)
        assert 0 < len(page.rows) < 12
        rows = []
        while True:
            assert len(pydantic_core.to_json(page.model_dump(mode="json"), indent=2)) <= 4096
            rows.extend(page.rows)
            if not page.has_more:
                break
            page = await catalog.data(FLOW, cursor=page.next_cursor, limit=12)
        assert [r["a"] for r in rows] == [str(n) for n in range(12)]
    finally:
        await http.close()


async def test_oversized_single_row_is_explicit_and_source_errors_keep_their_codes():
    http, catalog, _, _ = service(
        ("a;b\n1;" + "ş" * 3000 + "\n").encode(), query_max_output_bytes=2048
    )
    try:
        error = await respond(catalog.data(FLOW, query={}))
        assert error["code"] == "query_limit_exceeded"
        # Projection permits the query without changing source strings.
        data = await catalog.data(FLOW, query={"columns": ["a"]})
        assert data.rows == [{"a": "1"}]
    finally:
        await http.close()
    http, catalog, _, _ = service(b"a;a\n1;2\n")
    try:
        error = await respond(catalog.data(FLOW, query={}))
        assert error["code"] == "malformed_response" and error["source_url"].endswith("csv")
    finally:
        await http.close()


async def test_legacy_first_rows_and_explicit_query_with_legacy_filters_remain_compatible():
    http, catalog, _, _ = service(b"a;b\n1;x\n2;y\n3;x\n")
    try:
        legacy = await catalog.data(FLOW, filters={"b": "x"}, limit=1)
        assert legacy.rows == [{"a": "1", "b": "x"}] and legacy.truncated
        assert legacy.next_cursor is None
        first = await catalog.data(FLOW, filters={"b": "x"}, query={}, limit=1)
        second = await catalog.data(FLOW, cursor=first.next_cursor, filters={"b": "x"}, query={})
        assert first.total_rows == second.total_rows == 2 and second.rows[0]["a"] == "3"
    finally:
        await http.close()


async def test_original_file_expiry_cannot_be_extended_by_redownloading_identical_bytes(
    monkeypatch,
):
    clock = [0.0]
    monkeypatch.setattr("tuik_mcp.cache.time", SimpleNamespace(monotonic=lambda: clock[0]))
    http, catalog, calls, _ = service(b"a;b\n1;x\n2;y\n3;z\n")
    try:
        await catalog.data(FLOW, limit=1)
        clock[0] = 3595
        first = await catalog.data(FLOW, query={}, limit=1)
        clock[0] = 3601
        fresh = await catalog.data(FLOW, limit=1)  # same bytes, new retained file lifetime
        assert first.snapshot_id == fresh.snapshot_id
        count = len(calls)
        error = await respond(catalog.data(FLOW, cursor=first.next_cursor))
        assert error["code"] == "cursor_expired" and len(calls) == count
    finally:
        await http.close()


async def test_selected_sheet_and_hidden_filter_columns_are_bound_to_the_cursor():
    book = Workbook()
    book.active.title = "First"
    book.active.append(["n", "city"])
    book.active.append(["1", "A"])
    other = book.create_sheet("Second")
    for row in [["n", "city"], ["2", "B"], ["3", "B"]]:
        other.append(row)
    body = io.BytesIO()
    book.save(body)
    http, catalog, _, _ = service(body.getvalue(), "xlsx")
    try:
        query = {
            "columns": ["column_1"],
            "predicates": [
                {"op": "eq", "column": "column_2", "value": "B"},
            ],
        }
        first = await catalog.data(FILE, query=query, sheet="Second", limit=1)
        assert first.rows == [{"column_1": "2"}] and first.sheets == ["First", "Second"]
        last = await catalog.data(FILE, cursor=first.next_cursor, sheet="Second", limit=1)
        assert last.rows == [{"column_1": "3"}] and not last.has_more
        assert first.sheet == last.sheet == "Second"
    finally:
        await http.close()


async def test_cancelled_query_keeps_the_shared_worker_and_query_slot(monkeypatch):
    from tuik_mcp.services import query as query_service

    started, release = threading.Event(), threading.Event()
    evaluations = []
    original = query_service.evaluate

    def blocking(*args):
        evaluations.append(1)
        if len(evaluations) == 1:
            started.set()
            assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(query_service, "evaluate", blocking)
    http, catalog, _, _ = service(b"a;b\n1;x\n2;y\n")
    try:
        first = asyncio.create_task(catalog.data(FLOW, query={}, limit=1))
        assert await asyncio.to_thread(started.wait, 3)
        first.cancel()
        await asyncio.sleep(0)
        first.cancel()
        second = asyncio.create_task(catalog.data(FLOW, query={}, limit=1))
        await asyncio.sleep(0.02)
        assert len(evaluations) == 1 and not first.done() and not second.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert (await second).rows == [{"a": "1", "b": "x"}]
    finally:
        release.set()
        await http.close()


async def test_non_table_queries_keep_explicit_format_and_related_choice_semantics():
    http, catalog, _, _ = service((FIXTURES / "files/ict-publication.pdf").read_bytes(), "pdf")
    try:
        error = await respond(catalog.data(FILE, query={}))
        assert error["code"] == "unsupported_query" and error["source_url"] == URL
        assert (await catalog.data(FILE)).status == "download_available"
    finally:
        await http.close()

    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/tr/press/58347":
            return httpx.Response(
                200, json=json.loads((FIXTURES / "press_dataflows.json").read_text())
            )
        assert request.url.path == "/api/tr/dataflows"
        return httpx.Response(
            200,
            json=[
                {
                    "id": "DF_SATIS_SEKLI_SATIS_DURUMU_V3+V1.0",
                    "name": "Sales",
                    "downloadable": True,
                }
            ],
        )

    http = HttpClient(Settings(min_interval_seconds=0), httpx.MockTransport(handler))
    try:
        result = await CatalogService(http).data("press:58347", query={})
        assert (
            result.status == "related_datasets" and result.rows == [] and result.next_cursor is None
        )
        assert calls == ["/api/tr/press/58347", "/api/tr/dataflows"]
    finally:
        await http.close()


def test_partial_parsed_tables_cannot_claim_a_complete_query_count():
    partial = ParsedTable(columns=["a"], rows=[{"a": "1"}], total_rows=2, extraction="csv")
    with pytest.raises(QueryError, match="complete table"):
        evaluate(partial, TableQuery(), {}, QueryLimits())
