"""Editable PowerPoint briefings rendered from validated reports (#93)."""

import io
import json
import sys
import time

import pytest

pptx = pytest.importorskip("pptx")

from pptx import Presentation  # noqa: E402
from pptx.enum.chart import XL_CHART_TYPE  # noqa: E402
from test_observations import labour_catalog  # noqa: E402
from test_reports import (  # noqa: E402
    DATA,
    EVIDENCE,
    host,
    observation,
    read,
    report_spec,
    summary,
)

from tuik_mcp.models.report import Report  # noqa: E402
from tuik_mcp.reports import pptx as renderer  # noqa: E402
from tuik_mcp.server import respond  # noqa: E402

PPTX_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def briefing(language="tr", **fields) -> dict:
    """Title and key messages, KPI table, trend, comparison, methods/limitations, sources."""
    return report_spec(
        summary({"evidence": ["rate", "gap"]}),
        {"kind": "table", "table": {"rows": ["rate", "gap", "unemployed", "fewer", "peak"]}},
        {"kind": "chart", "chart": {"series": ["trend"], "type": "line"}},
        {
            "kind": "findings",
            "findings": [{"evidence": ["unemployed", "fewer"]}, {"evidence": ["peak"]}],
        },
        language=language,
        **fields,
    )


async def build(spec: dict, tmp_path, *, deck=True) -> dict:
    http, catalog = labour_catalog(DATA, artifact_dir=str(tmp_path))
    try:
        return await respond(catalog.report(spec, pptx=deck))
    finally:
        await http.close()


def texts(slide) -> list[str]:
    return [shape.text_frame.text for shape in slide.shapes if shape.has_text_frame]


def title(slide) -> str:
    return texts(slide)[0]


def runs(prs):
    for slide in prs.slides:
        for shape in slide.shapes:
            frames = [shape.text_frame] if shape.has_text_frame else []
            if shape.has_table:
                frames += [cell.text_frame for row in shape.table.rows for cell in row.cells]
            for frame in frames:
                for paragraph in frame.paragraphs:
                    yield from paragraph.runs


async def test_turkish_briefing_is_editable_and_matches_the_report(tmp_path):
    payload = await build(briefing(), tmp_path)
    assert payload["status"] == "ok"
    files = {f["name"]: f for f in payload["artifact"]["files"]}
    assert files["report.pptx"]["mime_type"] == PPTX_TYPE
    report = Report.model_validate_json(read(payload, "report.json", tmp_path))
    content = read(payload, "report.pptx", tmp_path)
    prs = Presentation(io.BytesIO(content))

    # Six 16:9 slides: title/key messages, table, trend, comparison, methods/limitations, sources.
    assert prs.slide_width * 9 == prs.slide_height * 16
    assert [title(s) for s in prs.slides] == [
        "İşgücü bülteni",
        "Tablo",
        "Grafik",
        "Bulgular",
        "Yöntem ve sınırlılıklar",
        "Kaynaklar",
    ]
    summary_text = report.sections[0].findings[0].text
    assert any(summary_text in text for text in texts(prs.slides[0]))

    # A native, editable table with exactly the report's cells.
    (table,) = [shape.table for shape in prs.slides[1].shapes if shape.has_table]
    section = report.sections[1].table
    assert [cell.text for cell in table.rows[0].cells] == section.columns
    assert [[cell.text for cell in row.cells] for row in list(table.rows)[1:]] == [
        [r.label, r.period, r.value, r.status, r.id] for r in section.rows
    ]

    # A native line chart whose embedded data are the report's points; gaps stay empty.
    (frame,) = [shape for shape in prs.slides[2].shapes if shape.has_chart]
    chart = frame.chart
    assert chart.chart_type == XL_CHART_TYPE.LINE_MARKERS
    assert chart.part.chart_workbook.xlsx_part is not None
    points = {p.period: p for p in report.sections[2].chart.series[0].points}
    periods = sorted(points)
    assert list(chart.plots[0].categories) == periods
    assert list(chart.plots[0].series[0].values) == [
        float(points[p].value) if points[p].value is not None else None for p in periods
    ]
    assert None in chart.plots[0].series[0].values
    assert frame._element.nvGraphicFramePr.cNvPr.get("descr") == report.sections[2].chart.alt_text

    # Every analytical slide has a compact source/period/unit note.
    for slide in list(prs.slides)[:4]:
        note = texts(slide)[-1]
        assert note.startswith("Kaynak: TÜİK · flow:") and "Dönem:" in note and "Birim:" in note
    # Speaker notes carry the full citations and calculation context.
    notes = prs.slides[1].notes_slide.notes_text_frame.text
    gap = next(e for e in report.evidence if e.key == "gap")
    assert gap.source.source_url in notes and gap.calculation_id in notes and gap.formula in notes
    assert report.report_id in notes

    # Readable everywhere: nothing below 12 pt; Turkish text survives exactly.
    assert all(run.font.size is None or run.font.size.pt >= 12 for run in runs(prs))
    assert "İşsizlik Oranı (%)" in [cell.text for row in table.rows for cell in row.cells]


async def test_english_labels_and_a_deterministic_deck(tmp_path):
    payload = await build(briefing("en"), tmp_path)
    content = read(payload, "report.pptx", tmp_path)
    prs = Presentation(io.BytesIO(content))
    assert [title(s) for s in prs.slides][-2:] == ["Methods and limitations", "Sources"]
    assert texts(prs.slides[1])[-1].startswith("Source: TurkStat (TÜİK)")
    # The same report.json renders the same deck, byte for byte.
    document = read(payload, "report.json", tmp_path)
    first = renderer.render_pptx(document)
    time.sleep(1.1)  # Zip entries carry fixed timestamps, not the clock.
    assert renderer.render_pptx(document) == first == content


async def test_drafts_are_marked_on_every_slide_and_long_content_continues(tmp_path):
    extra = [
        observation(f"o{i}", p) for i, p in enumerate(["2025-01", "2025-02", "2025-12", "2026-01"])
    ]
    narrative = "Oran Mart 2026'da %8,5 oldu."
    spec = report_spec(
        summary(
            {"evidence": ["rate", "gap"]},
            host(narrative, ("rate", "%8,5")),
            *[{"evidence": ["unemployed", "fewer"]} for _ in range(6)],
        ),
        {
            "kind": "table",
            "table": {
                "rows": ["rate", "gap", "unemployed", "fewer", "peak", "o0", "o1", "o2", "o3"]
            },
        },
        {"kind": "chart", "chart": {"series": ["trend"], "type": "bar"}},
        evidence=[*EVIDENCE, *extra],
        on_unverified="mark",
    )
    payload = await build(spec, tmp_path)
    assert payload["status"] == "draft"
    report = Report.model_validate_json(read(payload, "report.json", tmp_path))
    prs = Presentation(io.BytesIO(read(payload, "report.pptx", tmp_path)))
    for slide in prs.slides:
        assert "DOĞRULANMAMIŞ TASLAK" in texts(slide)
    # The unverified sentence keeps its marker; no message is dropped across continuations.
    shown = "\n".join(text for slide in prs.slides for text in texts(slide))
    assert f"DOĞRULANMAMIŞ TASLAK: {narrative}" in shown
    assert any(title(s) == "Temel mesajlar (devam)" for s in prs.slides)
    # Nine rows continue on a second table slide; every row appears once, in order.
    rows = [
        [cell.text for cell in row.cells][0:3]
        for slide in prs.slides
        for shape in slide.shapes
        if shape.has_table
        for row in list(shape.table.rows)[1:]
    ]
    assert rows == [[r.label, r.period, r.value] for r in report.sections[1].table.rows]
    assert [title(s) for s in prs.slides].count("Tablo (devam)") == 1
    (chart,) = [s.chart for slide in prs.slides for s in slide.shapes if s.has_chart]
    assert chart.chart_type == XL_CHART_TYPE.COLUMN_CLUSTERED


async def test_rejected_reports_and_plain_reports_have_no_deck(tmp_path):
    rejected = report_spec(summary(host("Oran %8,5 oldu.", ("rate", "%8,5"))))
    payload = await build(rejected, tmp_path)
    assert payload["status"] == "rejected" and payload["artifact"] is None
    assert not list(tmp_path.rglob("report.pptx"))
    plain = await build(briefing(), tmp_path / "plain", deck=False)
    assert "report.pptx" not in [f["name"] for f in plain["artifact"]["files"]]
    with pytest.raises(renderer.RenderError, match="rejected"):
        document = json.loads(read(plain, "report.json", tmp_path / "plain"))
        renderer.render_pptx(json.dumps({**document, "status": "rejected"}).encode())


async def test_missing_extra_fails_only_the_deck_request(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "tuik_mcp.reports.pptx", None)
    payload = await build(briefing(), tmp_path)
    assert payload["status"] == "error" and payload["code"] == "pptx_unavailable"
    assert "[pptx-out]" in payload["message"]
    assert (await build(briefing(), tmp_path, deck=False))["status"] == "ok"


async def test_read_back_rejects_a_changed_cell_or_point_and_external_targets(tmp_path):
    payload = await build(briefing(), tmp_path)
    report = Report.model_validate_json(read(payload, "report.json", tmp_path))
    deck = renderer.Deck(report)
    content = deck.build()
    renderer.verify(content, deck.expected_tables, deck.expected_charts)

    def changed(edit) -> bytes:
        prs = Presentation(io.BytesIO(content))
        edit(prs)
        output = io.BytesIO()
        prs.save(output)
        return output.getvalue()

    def cell(prs):
        table = next(s.table for slide in prs.slides for s in slide.shapes if s.has_table)
        table.cell(1, 2).text_frame.paragraphs[0].runs[0].text = "%8,50"

    def point(prs):
        chart = next(s.chart for slide in prs.slides for s in slide.shapes if s.has_chart)
        data = renderer.CategoryChartData()
        data.categories = list(chart.plots[0].categories)
        values = list(chart.plots[0].series[0].values)
        values[0] = 99.0
        data.add_series(chart.plots[0].series[0].name, values)
        chart.replace_data(data)

    def link(prs):
        run = next(renderer_runs(prs))
        run.hyperlink.address = "https://example.com/"

    def renderer_runs(prs):
        return runs(prs)

    for edit, message in ((cell, "table cell"), (point, "Chart values"), (link, "external")):
        with pytest.raises(renderer.RenderError, match=message):
            renderer.verify(changed(edit), deck.expected_tables, deck.expected_charts)


def test_text_is_plain_and_pages_never_split_a_bullet():
    assert renderer.plain("a‮b\x07c\n  d") == "a b c d"
    pages = renderer.chunks(["x" * 300, "y" * 50, "z" * 400], 100, 4)
    assert pages == [["x" * 300], ["y" * 50], ["z" * 400]]
    assert renderer.chunks([], 100, 5) == [[]]
