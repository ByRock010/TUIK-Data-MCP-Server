"""chart_tuik_data: PNG charts of retained CSV/Excel rows (#72)."""

import base64
import builtins
import json
import sys
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tuik_mcp import charts
from tuik_mcp.clients.base import HttpClient, SourceError
from tuik_mcp.config import Settings
from tuik_mcp.parsers.query import QueryError
from tuik_mcp.services.catalog import CatalogService

sys.path.insert(0, str(Path(__file__).parent))
from test_inspect_data import FILES, FIXTURES, FLOW, ID, service  # noqa: E402

pytest.importorskip("matplotlib")

PNG = b"\x89PNG\r\n\x1a\n"
X = ["Yıllar - Years [column 1]", "column_2"]
RATE = "İşsizlik oranı Unemployment rate (%) [column {}]"


def long_csv(rows):
    return ("Sektör;Zaman;Gözlem Değeri\n" + "".join(f"{s};{t};{v}\n" for s, t, v in rows)).encode()


async def test_xls_line_chart_reads_the_snapshot_and_summarizes_recent_points():
    http, catalog, calls = service((FILES / "labour-adjusted.xls").read_bytes(), "xls")
    try:
        result, image = await catalog.chart(ID, X, [RATE.format(10)], last=36)
        assert image.startswith(PNG) and len(image) > 10_000
        assert (result.status, result.image, result.format, result.sheet) == (
            "ok",
            "image/png",
            "xls",
            "Tablo",
        )
        assert (result.width_px, result.height_px) == (1100, 616)
        assert result.title.startswith("Mevsim etkisinden arındırılmış")
        assert result.unit and result.snapshot_id.startswith("sha256:")
        assert result.matched_rows == 260 and result.x_points == 36
        (line,) = result.series
        assert line.name == "İşsizlik oranı Unemployment rate (%)" and line.points == 36
        assert (line.last.x, line.last.value) == ("2026 Ağustos - August", 7.8)
        assert line.minimum.value <= line.last.value <= line.maximum.value
        # The chart and get_tuik_data share one download.
        rows = await catalog.data(ID, limit=1)
        assert rows.snapshot_id == result.snapshot_id and len(calls) == 1
    finally:
        await http.close()


async def test_side_by_side_headers_are_disambiguated_or_named_by_labels():
    http, catalog, _ = service((FILES / "labour-adjusted.xls").read_bytes(), "xls")
    try:
        y = [RATE.format(10), RATE.format(21)]
        result, _ = await catalog.chart(ID, X, y, last=12)
        assert [s.name for s in result.series] == y
        assert any("pass labels" in warning for warning in result.warnings)
        named, image = await catalog.chart(ID, X, y, "bar", last=6, labels=["Toplam", "Erkek"])
        assert image.startswith(PNG) and [s.name for s in named.series] == ["Toplam", "Erkek"]
        assert [s.column for s in named.series] == y
    finally:
        await http.close()


async def test_long_csv_splits_series_and_applies_predicates_and_last():
    body = long_csv(
        [
            (s, f"2026-{m:02d}", f"{base + m},5")
            for s, base in [("Gıda", 90), ("Enerji", 80)]
            for m in range(1, 7)
        ]
    )
    http, catalog, _ = service(body, "csv")
    try:
        result, image = await catalog.chart(
            FLOW,
            ["Zaman"],
            ["Gözlem Değeri"],
            series="Sektör",
            query={
                "predicates": [
                    {
                        "op": "range",
                        "column": "Zaman",
                        "value_type": "period",
                        "period_format": "month",
                        "minimum": "2026-02",
                    }
                ]
            },
            last=3,
        )
        assert image.startswith(PNG) and result.format == "csv" and result.sheets == []
        assert result.matched_rows == 10 and result.x_points == 3
        food, energy = result.series
        assert (food.name, food.points, food.first.x, food.last.value) == (
            "Gıda",
            3,
            "2026-04",
            96.5,
        )
        assert (energy.name, energy.last.value) == ("Enerji", 86.5)
    finally:
        await http.close()


async def test_placeholders_and_text_become_gaps_never_zero():
    body = long_csv(
        [
            ("Gıda", "2026-01", "10,5"),
            ("Gıda", "2026-02", "-"),
            ("Gıda", "2026-03", ""),
            ("Gıda", "2026-04", "n/a"),
            ("Gıda", "2026-05", "12,5%"),
        ]
    )
    http, catalog, _ = service(body, "csv")
    try:
        result, _ = await catalog.chart(FLOW, ["Zaman"], ["Gözlem Değeri"])
        (line,) = result.series
        assert (line.points, line.missing, line.invalid) == (2, 2, 1)
        assert line.minimum.value == 10.5 and line.last.value == 12.5
        assert any("1 non-numeric" in warning for warning in result.warnings)
    finally:
        await http.close()


@pytest.mark.parametrize("descending_sort", [False, True])
async def test_last_keeps_the_most_recent_periods_whatever_the_row_order(descending_sort):
    # Newest-first source rows, or ascending rows sorted descending by query.
    months = ["2026-01", "2026-02", "2026-03"]
    rows = [("Gıda", month, str(n)) for n, month in enumerate(months, start=1)]
    body = long_csv(rows if descending_sort else rows[::-1])
    query = (
        {
            "sort": [
                {
                    "column": "Zaman",
                    "value_type": "period",
                    "period_format": "month",
                    "direction": "desc",
                }
            ]
        }
        if descending_sort
        else None
    )
    http, catalog, _ = service(body, "csv")
    try:
        result, _ = await catalog.chart(FLOW, ["Zaman"], ["Gözlem Değeri"], query=query, last=2)
        (line,) = result.series
        assert result.x_order == "chronological" and result.x_points == 2
        assert (line.first.x, line.last.x, line.last.value) == ("2026-02", "2026-03", 3.0)
        assert any("chronologically" in warning for warning in result.warnings)
    finally:
        await http.close()


def test_named_month_labels_are_ordered_and_other_labels_keep_row_order():
    rows = [
        {"Yıl": "2026", "Ay": month, "Değer": str(n)}
        for n, month in enumerate(["Mart - March", "Şubat - February (r)", "Ocak - January"])
    ]
    plot = charts.build(["Yıl", "Ay", "Değer"], rows, ["Yıl", "Ay"], ["Değer"], last=2)
    assert plot.order == "chronological"
    assert plot.labels == ["2026 Şubat - February (r)", "2026 Mart - March"]
    names = [{"Ad": name, "Değer": str(n)} for n, name in enumerate(["B", "A", "C"])]
    plot = charts.build(["Ad", "Değer"], names, ["Ad"], ["Değer"], last=2)
    assert plot.order == "source" and plot.labels == ["A", "C"]
    assert any("not necessarily the most recent" in warning for warning in plot.warnings)


@pytest.mark.parametrize("split", [False, True])
def test_gap_counts_and_warnings_cover_only_the_plotted_window(split):
    values = ["-", "n/a", "10", "20"]
    rows = [
        {"Sektör": "Gıda", "Zaman": f"2026-0{n}", "Değer": value}
        for n, value in enumerate(values, start=1)
    ]
    columns = ["Sektör", "Zaman", "Değer"]
    options = {"series": "Sektör"} if split else {}
    plot = charts.build(columns, rows, ["Zaman"], ["Değer"], last=2, **options)
    (line,) = charts.summarize(plot)
    assert (line.points, line.missing, line.invalid) == (2, 0, 0)
    assert not any("non-numeric" in warning for warning in plot.warnings)
    whole = charts.build(columns, rows, ["Zaman"], ["Değer"], **options)
    (line,) = charts.summarize(whole)
    assert (line.points, line.missing, line.invalid) == (2, 1, 1)
    assert any("1 non-numeric" in warning for warning in whole.warnings)


@pytest.mark.parametrize(
    "arguments,code",
    [
        ({"x": ["Yok"], "y": ["Gözlem Değeri"]}, "invalid_columns"),
        ({"x": ["Zaman"], "y": ["Gözlem Değeri", "Sektör"], "series": "Sektör"}, "invalid_query"),
        ({"x": ["Zaman"], "y": ["Sektör"]}, "invalid_query"),
        ({"x": ["Zaman"], "y": ["Gözlem Değeri"], "filters": {"Sektör": "Yok"}}, "invalid_query"),
        ({"x": ["Sektör"], "y": ["Gözlem Değeri"], "series": "Zaman"}, "chart_limit_exceeded"),
        (
            {"x": ["Zaman"], "y": ["Gözlem Değeri"], "series": "Sektör", "labels": ["A"]},
            "invalid_query",
        ),
    ],
)
async def test_unusable_requests_report_source_error_codes(arguments, code):
    body = long_csv(
        [("Gıda" if m % 2 else "Enerji", f"2025-{m:02d}", str(m)) for m in range(1, 13)]
    )
    http, catalog, _ = service(body, "csv")
    try:
        with pytest.raises(SourceError) as error:
            await catalog.chart(FLOW, **arguments)
        assert error.value.code == code
    finally:
        await http.close()


async def test_ambiguous_numbers_require_an_explicit_decimal_separator():
    body = long_csv([("Gıda", "2026-01", "1.234"), ("Gıda", "2026-02", "2.345")])
    http, catalog, _ = service(body, "csv")
    try:
        with pytest.raises(SourceError, match="decimal_separator") as error:
            await catalog.chart(FLOW, ["Zaman"], ["Gözlem Değeri"])
        assert error.value.code == "invalid_query"
        result, _ = await catalog.chart(FLOW, ["Zaman"], ["Gözlem Değeri"], decimal_separator=".")
        assert result.series[0].last.value == 2.345
    finally:
        await http.close()


@pytest.mark.parametrize(
    "arguments",
    [
        {"x": [], "y": ["a"]},
        {"x": ["a"], "y": ["b"] * 7},
        {"x": ["a"], "y": ["b"], "chart": "pie"},
        {"x": ["a"], "y": ["b"], "last": 0},
        {"x": ["a"], "y": ["b"], "decimal_separator": ";"},
        {"x": ["a"], "y": ["b"], "labels": [""]},
    ],
)
async def test_invalid_arguments_fail_before_any_download(arguments):
    http, catalog, calls = service(b"", "csv")
    try:
        with pytest.raises(ValueError):
            await catalog.chart(FLOW, **arguments)
        assert calls == []
    finally:
        await http.close()


async def test_bulletin_with_related_datasets_returns_a_choice_without_an_image():
    payload = json.loads((FIXTURES / "press_dataflows.json").read_text())
    title = payload["data"]["statisticalTables"][0]["title"]
    flows = [{"id": "DF_SATIS_SEKLI_SATIS_DURUMU_V3+V1.0", "name": title, "downloadable": True}]
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/tr/press/58347":
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json=flows)

    http = HttpClient(Settings(min_interval_seconds=0), httpx.MockTransport(handler))
    try:
        result, image = await CatalogService(http).chart("press:58347", ["a"], ["b"])
        assert result.status == "related_datasets" and result.related_datasets and image is None
        assert not any("/file/" in path or "downloads" in path for path in calls)
    finally:
        await http.close()


def test_missing_matplotlib_is_an_actionable_error(monkeypatch):
    real_import = builtins.__import__

    def without_matplotlib(name, *args, **kwargs):
        if name.startswith("matplotlib"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_matplotlib)
    plot = charts.Plot(labels=["a"], lines=[charts.Line("b", "b", [1.0], ["ok"])])
    with pytest.raises(QueryError, match=r"tuik-mcp\[viz\]") as error:
        charts.render(plot, "line", "t", None, "f")
    assert error.value.code == "visualization_unavailable"


@pytest.mark.parametrize(
    "value,text",
    [(35412.0, "35.412"), (8.4, "8,4"), (1234.5, "1.234,5"), (0.0, "0"), (-2.25, "-2,25")],
)
def test_axis_numbers_use_turkish_notation(value, text):
    assert charts.turkish_number(value) == text


async def test_stdio_tool_returns_png_image_and_structured_summary():
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).with_name("search_stdio_server.py")), "portal"],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tool = next(
                t for t in (await session.list_tools()).tools if t.name == "chart_tuik_data"
            )
            assert tool.inputSchema["properties"]["chart"]["enum"] == ["line", "bar"]
            assert tool.inputSchema["required"] == ["resource_id", "x", "y"]
            result = await session.call_tool(
                "chart_tuik_data",
                {"resource_id": FLOW, "x": ["Zaman (2015-01)"], "y": ["Gözlem Değeri"]},
            )
            assert not result.isError
            image, text = result.content
            assert image.type == "image" and image.mimeType == "image/png"
            assert base64.b64decode(image.data).startswith(PNG)
            summary = result.structuredContent
            assert json.loads(text.text) == summary
            assert summary["status"] == "ok" and summary["series"][0]["points"] == 3
            assert summary["source_url"].endswith("/file/csv")

            error = await session.call_tool(
                "chart_tuik_data", {"resource_id": FLOW, "x": ["Yok"], "y": ["Gözlem Değeri"]}
            )
            assert [c.type for c in error.content] == ["text"]
            assert error.structuredContent["status"] == "error"
            assert error.structuredContent["code"] == "invalid_columns"
