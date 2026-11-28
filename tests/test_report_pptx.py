import json
from io import BytesIO
from zipfile import ZipFile

import pytest
from test_observations import labour_catalog
from test_reports import DATA, EVIDENCE, read, report_spec, summary

from tuik_mcp.server import reply, respond


async def test_powerpoint_preserves_editable_data_notes_and_artifact(tmp_path):
    pptx = pytest.importorskip("pptx")
    spec = report_spec(
        summary({"evidence": ["rate", "gap"]}),
        {"kind": "table", "table": {"rows": ["rate", "gap", "unemployed"]}},
        {"kind": "chart", "chart": {"series": ["trend"], "type": "line"}},
        {"kind": "chart", "chart": {"series": ["trend"], "type": "bar"}},
        title="İşgücü görünümü",
        evidence=[
            item for item in EVIDENCE if item["key"] in {"rate", "gap", "unemployed", "trend"}
        ],
    )
    http, catalog = labour_catalog(DATA, artifact_dir=str(tmp_path))
    try:
        payload = await respond(catalog.report(spec, pptx=True))
        baseline = await respond(catalog.report(spec))
    finally:
        await http.close()

    assert payload["status"] == "ok", payload.get("message")
    assert payload["report_id"] == baseline["report_id"]
    assert "report.pptx" not in {f["name"] for f in baseline["artifact"]["files"]}
    slide_bytes = read(payload, "report.pptx", tmp_path)
    stored = json.loads(read(payload, "report.json", tmp_path))
    assert stored["spec"] == json.loads(read(baseline, "report.json", tmp_path))["spec"]
    assert any(getattr(part, "name", None) == "report.pptx" for part in reply(payload).content)

    deck = pptx.Presentation(BytesIO(slide_bytes))
    assert len(deck.slides) == 7  # The fixture has an additional limitations page.
    assert abs(deck.slide_width / deck.slide_height - 16 / 9) < 0.001
    assert "İşgücü görünümü" in deck.slides[0].shapes[0].text
    grid = next(shape.table for shape in deck.slides[1].shapes if shape.has_table)
    assert grid.cell(1, 2).text == stored["sections"][1]["table"]["rows"][0]["value"]
    assert grid.cell(2, 2).text == stored["sections"][1]["table"]["rows"][1]["value"]
    assert "calc:" in deck.slides[1].notes_slide.notes_text_frame.text
    assert "https://" in deck.slides[2].notes_slide.notes_text_frame.text

    for slide_no in (2, 3):
        chart = next(shape.chart for shape in deck.slides[slide_no].shapes if shape.has_chart)
        if slide_no == 3:
            assert chart.value_axis.minimum_scale == 0
        points = stored["sections"][slide_no]["chart"]["series"][0]["points"]
        assert [category.label for category in chart.plots[0].categories] == [
            point["period"] for point in points
        ]
        assert list(chart.series[0].values) == [
            float(point["value"]) if point["value"] is not None else None for point in points
        ]
    with ZipFile(BytesIO(slide_bytes)) as archive:
        assert len([name for name in archive.namelist() if name.endswith(".xlsx")]) == 2


async def test_powerpoint_discloses_unverified_drafts_and_keeps_text_plain(tmp_path):
    pptx = pytest.importorskip("pptx")
    from test_reports import host

    spec = report_spec(
        summary(host("<b>Rate was 18.0%</b>.", ("rate", "18.0%"))),
        language="en",
        on_unverified="mark",
    )
    http, catalog = labour_catalog(DATA, artifact_dir=str(tmp_path))
    try:
        payload = await respond(catalog.report(spec, pptx=True))
    finally:
        await http.close()
    assert payload["status"] == "draft"
    deck = pptx.Presentation(BytesIO(read(payload, "report.pptx", tmp_path)))
    text = "\n".join(shape.text for shape in deck.slides[0].shapes if shape.has_text_frame)
    assert "UNVERIFIED DRAFT" in text
    assert "<b>Rate was 18.0%</b>." in text
