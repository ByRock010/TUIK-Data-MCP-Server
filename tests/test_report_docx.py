"""Editable Word briefs rendered from validated reports (#95)."""

import io
import sys
import time

import pytest

pytest.importorskip("docx")

from docx import Document  # noqa: E402
from docx.opc.constants import RELATIONSHIP_TYPE as RT  # noqa: E402
from docx.oxml import OxmlElement  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402
from test_observations import labour_catalog  # noqa: E402
from test_reports import (  # noqa: E402
    DATA,
    EVIDENCE,
    findings,
    host,
    observation,
    read,
    report_spec,
    summary,
)

from tuik_mcp.models.report import Report  # noqa: E402
from tuik_mcp.reports import docx as renderer  # noqa: E402
from tuik_mcp.server import respond  # noqa: E402

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def brief_spec(language="tr", **fields) -> dict:
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


async def build(spec: dict, tmp_path, *, docx=True, template=None) -> dict:
    http, catalog = labour_catalog(DATA, artifact_dir=str(tmp_path))
    try:
        return await respond(catalog.report(spec, docx=docx, docx_template=template))
    finally:
        await http.close()


def text_of(document) -> str:
    parts = [p.text for p in document.paragraphs]
    parts += [cell.text for t in document.tables for row in t.rows for cell in row.cells]
    return "\n".join(parts)


def external(document) -> list[str]:
    return [r.target_ref for r in document.part.rels.values() if r.is_external]


async def test_turkish_brief_uses_real_structure_and_agrees_with_the_report(tmp_path):
    payload = await build(brief_spec(), tmp_path)
    assert payload["status"] == "ok"
    files = {f["name"]: f for f in payload["artifact"]["files"]}
    assert files["report.docx"]["mime_type"] == DOCX_TYPE
    report = Report.model_validate_json(read(payload, "report.json", tmp_path))
    document = Document(io.BytesIO(read(payload, "report.docx", tmp_path)))

    # Real heading styles drive Word's navigation pane.
    assert document.paragraphs[0].style.name == "Title"
    assert document.paragraphs[0].text == report.title
    headings = [p.text for p in document.paragraphs if p.style.name == "Heading 1"]
    assert headings == [section.title for section in report.sections]

    # The KPI table equals the report, and its header row repeats on every page.
    table = document.tables[0]
    section = report.sections[1].table
    assert [[c.text for c in row.cells] for row in table.rows] == [
        section.columns,
        *[[r.label, r.period, r.value, r.status, r.id] for r in section.rows],
    ]
    for each in document.tables:
        assert each.rows[0]._tr.trPr.find(qn("w:tblHeader")) is not None

    # Numbered captions and page numbers are fields the renderer wrote itself.
    fields = [f.get(qn("w:instr")).strip() for f in document.element.body.iter(qn("w:fldSimple"))]
    assert fields == ["SEQ Tablo \\* ARABIC", "SEQ Şekil \\* ARABIC"]
    footer = document.sections[0].footer
    assert [f.get(qn("w:instr")).strip() for f in footer._element.iter(qn("w:fldSimple"))] == [
        "PAGE"
    ]
    assert report.report_id in footer.paragraphs[0].text

    # The chart is a picture with alt text, beside an editable table of its points.
    (picture,) = document.inline_shapes
    chart = report.sections[2].chart
    assert picture._inline.docPr.get("descr") == chart.alt_text
    data = [[c.text for c in row.cells] for row in document.tables[1].rows]
    points = {p.period: p.value or "" for p in chart.series[0].points}
    assert data[1:] == [[period, points[period]] for period in sorted(points)]

    # Values, periods, methods, limitations and citations are the report's own text.
    text = text_of(document)
    for item in report.evidence:
        if item.display:
            assert item.display in text
    for section in report.sections:
        for line in section.items:
            assert line in text.replace("\n", " ") or line in text
        for finding in section.findings:
            assert finding.text in text
    assert "İşsizlik Oranı (%)" in text and "Sınırlılıklar" in headings

    # The only external targets are links to the official source pages.
    urls = external(document)
    assert urls and set(urls) == {item.source.source_url for item in report.evidence}
    assert all(url.startswith("https://veriportali.tuik.gov.tr/") for url in urls)


async def test_english_brief_and_a_deterministic_document(tmp_path):
    payload = await build(brief_spec("en"), tmp_path)
    content = read(payload, "report.docx", tmp_path)
    document = Document(io.BytesIO(content))
    headings = [p.text for p in document.paragraphs if p.style.name == "Heading 1"]
    assert headings[-3:] == ["Methods", "Limitations", "Sources"]
    assert "Source: TurkStat (TÜİK)" in document.sections[0].footer.paragraphs[0].text
    report_json = read(payload, "report.json", tmp_path)
    first = renderer.render_docx(report_json)
    time.sleep(1.1)  # Zip entries carry fixed timestamps, not the clock.
    assert renderer.render_docx(report_json) == first == content


async def test_template_placeholders_are_fixed_and_text_stays_plain(tmp_path):
    hostile = "Not <w:t>bold</w:t> {title} [link](https://evil.example) HYPERLINK x PAGE"
    spec = brief_spec(subtitle="{report_id} <script>")
    spec["sections"][0]["findings"].append(host(hostile, evidence=["rate"]))
    template = {
        "organization": "Ekonomi Masası",
        "header": "{organization} · {title} · {status}",
        "footer": "{report_id} · kesim {cutoff}",
    }
    payload = await build(spec, tmp_path, template=template)
    assert payload["status"] == "ok", findings(payload)
    document = Document(io.BytesIO(read(payload, "report.docx", tmp_path)))
    header = document.sections[0].header.paragraphs[0].text
    assert header == "Ekonomi Masası · İşgücü bülteni · doğrulandı"
    assert (
        document.sections[0]
        .footer.paragraphs[0]
        .text.startswith(f"{payload['report_id']} · kesim yok · Sayfa ")
    )
    # Source and host text are literal runs: no new field, link or markup.
    text = text_of(document)
    assert hostile in text and "{report_id} <script>" in text
    assert not any("evil" in url for url in external(document))
    assert len(list(document.element.body.iter(qn("w:fldSimple")))) == 2

    for bad, message in (
        ({"header": "{date}"}, "Unsupported template placeholder {date}"),
        ({"footer": "{title"}, "unmatched brace"),
        ({"organization": "{title}"}, "plain text without placeholders"),
        ({"header": "a\x07b"}, "control or format characters"),
        ({"macro": "x"}, "Extra inputs are not permitted"),
    ):
        refused = await build(brief_spec(), tmp_path / "bad", template=bad)
        assert refused["status"] == "error" and message in refused["message"], refused
    alone = await build(brief_spec(), tmp_path / "alone", docx=False, template={"header": "x"})
    assert alone["status"] == "error" and "only with docx=true" in alone["message"]


async def test_drafts_long_text_and_multipage_tables(tmp_path):
    extra = [observation(f"o{i}", p) for i, p in enumerate(["2025-01", "2025-02", "2025-12"])]
    narrative = "Oran Mart 2026'da %8,5 oldu."
    spec = report_spec(
        summary(
            host(narrative, ("rate", "%8,5")),
            *[{"evidence": ["unemployed", "fewer"]} for _ in range(7)],
        ),
        {
            "kind": "table",
            "table": {"rows": ["rate", "gap", "unemployed", "fewer", "peak", "o0", "o1", "o2"]},
        },
        {"kind": "chart", "chart": {"series": ["trend"], "type": "bar"}},
        evidence=[*EVIDENCE, *extra],
        on_unverified="mark",
    )
    payload = await build(spec, tmp_path)
    assert payload["status"] == "draft", payload.get("message")
    document = Document(io.BytesIO(read(payload, "report.docx", tmp_path)))
    assert document.sections[0].header.paragraphs[0].text.startswith("DOĞRULANMAMIŞ TASLAK")
    assert any(
        p.text == f"DOĞRULANMAMIŞ TASLAK: {narrative}" and p.style.name == "List Bullet"
        for p in document.paragraphs
    )
    # Rows never split across pages; a page break starts the generated sections.
    for table in document.tables:
        for row in list(table.rows)[1:]:
            assert row._tr.trPr.find(qn("w:cantSplit")) is not None
    breaks = document.element.body.findall(".//" + qn("w:br"))
    assert [b.get(qn("w:type")) for b in breaks] == ["page"]


async def test_rejected_reports_have_no_brief_and_the_extra_is_optional(tmp_path, monkeypatch):
    rejected = await build(
        report_spec(summary(host("Oran %8,5 oldu.", ("rate", "%8,5")))), tmp_path
    )
    assert rejected["status"] == "rejected" and rejected["artifact"] is None
    assert not list(tmp_path.rglob("report.docx"))
    monkeypatch.setitem(sys.modules, "tuik_mcp.reports.docx", None)
    missing = await build(brief_spec(), tmp_path / "missing")
    assert missing["status"] == "error" and missing["code"] == "docx_unavailable"
    plain = await build(brief_spec(), tmp_path / "plain", docx=False)
    assert plain["status"] == "ok"
    assert "report.docx" not in [f["name"] for f in plain["artifact"]["files"]]


async def test_read_back_rejects_changed_cells_foreign_links_and_fields(tmp_path):
    payload = await build(brief_spec(), tmp_path)
    report = Report.model_validate_json(read(payload, "report.json", tmp_path))
    brief = renderer.Brief(report, renderer.DocxTemplate())
    content = brief.build()
    renderer.verify(content, brief.tables_expected)

    def changed(edit) -> bytes:
        document = Document(io.BytesIO(content))
        edit(document)
        output = io.BytesIO()
        document.save(output)
        return output.getvalue()

    def cell(document):
        document.tables[0].cell(1, 2).paragraphs[0].runs[0].text = "%8,50"

    def link(document):
        document.part.relate_to("https://evil.example/", RT.HYPERLINK, is_external=True)

    def field(document):
        run = document.paragraphs[0].add_run()._r
        code = OxmlElement("w:instrText")
        code.text = ' HYPERLINK "https://evil.example" '
        run.append(code)

    for edit, message in ((cell, "table cell"), (link, "external target"), (field, "field")):
        with pytest.raises(renderer.RenderError, match=message):
            renderer.verify(changed(edit), brief.tables_expected)
