import base64
import hashlib
import io
import json
import sys
from pathlib import Path
from zipfile import ZipFile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openpyxl import load_workbook
from pydantic import AnyUrl
from test_observations import FIXTURES, FLOW, LABOUR, labour, labour_catalog

from tuik_mcp.observations import UNAVAILABLE
from tuik_mcp.server import respond
from tuik_mcp.workbooks import MIME_TYPE, PERCENT_FORMAT, build


async def test_stdio_export_retrieves_the_full_selection_and_manifest(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).with_name("search_stdio_server.py")), "portal"],
        env={"TUIK_ARTIFACT_DIR": str(tmp_path)},
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
        await client.initialize()
        selection = {"resource_id": FLOW, "period_from": "2015-01", "period_to": "2015-03"}
        page = await client.call_tool("get_tuik_observations", selection | {"limit": 1})
        assert page.structuredContent["has_more"]
        digest = page.structuredContent["snapshot_id"]
        export = await client.call_tool("export_tuik_workbook", selection | {"snapshot_id": digest})
        assert not export.isError
        data = export.structuredContent
        assert data["status"] == "ok" and data["selected_rows"] == 3
        assert data["snapshot_id"] == digest
        links = {item.name: item for item in export.content if item.type == "resource_link"}
        assert set(links) == {"source.csv", "tuik-observations.xlsx"}
        workbook_link = links["tuik-observations.xlsx"]
        (payload,) = (await client.read_resource(workbook_link.uri)).contents
        assert payload.mimeType == workbook_link.mimeType == MIME_TYPE
        xlsx = base64.b64decode(payload.blob)
        manifest_uri = AnyUrl(data["artifact"]["manifest_uri"])
        (report,) = (await client.read_resource(manifest_uri)).contents
        manifest = json.loads(report.text)
        assert (
            manifest["inputs"][0]["sha256"]
            == hashlib.sha256((FIXTURES / "data.csv").read_bytes()).hexdigest()
        )
        assert manifest["inputs"][0]["selection"]["range_coverage"]["complete"]
        assert manifest["inputs"][0]["selection"]["recipe"]["version"] == 1
        published = {entry["name"]: entry for entry in manifest["files"]}
        assert published["tuik-observations.xlsx"]["sha256"] == hashlib.sha256(xlsx).hexdigest()

    with ZipFile(io.BytesIO(xlsx)) as archive:
        assert not any(
            "externalLink" in name or "vbaProject" in name for name in archive.namelist()
        )
        assert all(
            b'TargetMode="External"' not in archive.read(name)
            for name in archive.namelist()
            if name.endswith(".rels")
        )
    workbook = load_workbook(io.BytesIO(xlsx))
    assert workbook.sheetnames == ["Analysis", "Normalized Data", "Raw Data", "Sources and Methods"]
    rows = workbook["Normalized Data"]
    assert rows.freeze_panes == "A2" and "NormalizedObservations" in rows.tables
    assert [rows[f"A{i}"].value for i in range(2, 5)] == ["2015-01", "2015-02", "2015-03"]
    expected = [98.4622068329301, 101.664854746884, 96.4516474099525]
    assert [rows[f"H{i}"].value for i in range(2, 5)] == expected
    raw = workbook["Raw Data"]
    assert [raw[f"B{i}"].value for i in range(2, 5)] == [
        "98,4622068329301",
        "101,664854746884",
        "96,4516474099525",
    ]
    assert [raw[f"D{i}"].value for i in range(2, 5)] == [2, 3, 4]
    summary = workbook["Analysis"]
    assert [summary[f"{col}2"].value for col in "DEFGH"] == [3, 3, 0, min(expected), max(expected)]
    (chart,) = summary._charts
    assert (chart.x_axis.axPos, chart.y_axis.axPos) == ("b", "l")
    assert chart.x_axis.tickLblPos == chart.y_axis.tickLblPos == "nextTo"
    assert chart.x_axis.delete is chart.y_axis.delete is False
    (series,) = chart.series
    assert series.smooth is False
    assert [point.v for point in series.val.numRef.numCache.pt] == expected
    assert [point.v for point in series.cat.strRef.strCache.pt] == ["2015-01", "2015-02", "2015-03"]
    sources = dict(workbook["Sources and Methods"].values)
    assert sources["Manifest URI"] == str(manifest_uri)
    assert sources["Snapshot"] == digest and sources["Coverage complete"] == "Yes"


async def test_missing_confidential_raw_strings_and_excel_percentage(tmp_path):
    rate = {"İşgücü Göstergeleri": "İşsizlik Oranı (%)"}
    confidential = {"Gözlem Değeri": "", "Gizlilik Durumu": "Gizli veri"}
    http, catalog = labour_catalog(
        labour(
            {**rate, "Zaman": "2026-01", "Gözlem Değeri": "-2,8"},
            {**rate, "Zaman": "2026-02", "Gözlem Değeri": "-"},
            {**rate, "Zaman": "2026-03", **confidential},
        ),
        artifact_dir=str(tmp_path),
    )
    try:
        resource = LABOUR.resource_ids[0]
        observations = await catalog.observations(resource)
        assert [o.status for o in observations.observations] == ["valid", "missing", "confidential"]
        first = observations.observations[0]
        first.geography = first.geography.model_copy(
            update={"code": "01", "label": "Adana Şehir", "level": "province"}
        )
        bad_text = ["=2+2", "+HYPERLINK", "-1+2", "@SUM", "\t=1", "\x00=4", "#N/A"]
        hostile = [
            first.model_copy(update={"value": None, "status": "invalid", "value_text": raw})
            for raw in bad_text
        ]
        note = "Rakamlar yuvarlanmıştır.\nKapsam değişmiştir."
        output = build(
            [*observations.observations, *hostile],
            [("Source note", note)],
            "test-manifest",
            complete_range=False,
        )
        workbook = load_workbook(io.BytesIO(output))
        normal = workbook["Normalized Data"]
        assert (normal["D2"].data_type, normal["D2"].value) == ("s", "01")
        assert normal["C2"].value == "Adana Şehir"
        assert (normal["H2"].data_type, normal["H2"].value) == ("n", -0.028)
        assert normal["H2"].number_format == PERCENT_FORMAT
        assert [normal[f"I{i}"].value for i in (3, 4)] == ["missing", "confidential"]
        assert all(normal[f"H{i}"].value is None for i in range(3, 12))
        raw = workbook["Raw Data"]
        assert raw["B2"].value == "-2,8" and raw["B3"].value == "-"
        assert [(raw[f"B{i}"].data_type, raw[f"B{i}"].value) for i in range(5, 12)] == [
            ("s", raw_value) for raw_value in [*bad_text[:5], r"\u0000=4", "#N/A"]
        ]
        methods = workbook["Sources and Methods"]
        assert methods["B2"].value == note
        assert methods.row_dimensions[2].height == 36
    finally:
        await http.close()


async def test_rejected_export_has_no_partial_artifact(tmp_path, monkeypatch):
    rate = {"İşgücü Göstergeleri": "İşsizlik Oranı (%)", "Gözlem Değeri": "8,4"}
    http, catalog = labour_catalog(
        labour({**rate, "Zaman": "2026-01"}, {**rate, "Zaman": "2026-02"}),
        artifact_dir=str(tmp_path),
    )
    try:
        resource = LABOUR.resource_ids[0]
        held = UNAVAILABLE[0]
        unavailable = await respond(catalog.workbooks.export(held.flows[0] + "+V1.0"))
        assert unavailable["code"] == "unavailable"
        assert held.reason in unavailable["message"]
        mismatch = await respond(
            catalog.workbooks.export(resource, snapshot_id="sha256:" + "0" * 64)
        )
        assert mismatch["code"] == "snapshot_changed"
        backward = await respond(
            catalog.workbooks.export(resource, period_from="2026-02", period_to="2026-01")
        )
        assert backward["code"] == "invalid_input"
        empty = await respond(catalog.workbooks.export(resource, period_to="2025-12"))
        assert empty["code"] == "empty_selection"
        monkeypatch.setattr("tuik_mcp.services.workbooks.MAX_WORKBOOK_ROWS", 1)
        too_large = await respond(catalog.workbooks.export(resource))
        assert too_large["code"] == "workbook_row_limit"
        assert not list(tmp_path.iterdir())
    finally:
        await http.close()
