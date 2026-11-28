import json
from io import BytesIO
from zipfile import ZipFile

import pytest
from test_observations import labour_catalog
from test_reports import DATA, host, read, report_spec, summary

from tuik_mcp.server import reply, respond


async def test_word_preserves_editable_data_source_links_and_artifact(tmp_path):
    docx = pytest.importorskip("docx")
    pytest.importorskip("matplotlib")
    spec = report_spec(
        summary({"evidence": ["rate", "gap"]}),
        {"kind": "table", "table": {"rows": ["rate", "unemployed", "gap"]}},
        {"kind": "chart", "chart": {"series": ["trend"]}},
        title="İşgücü ve işsizlik görünümü",
    )
    http, catalog = labour_catalog(DATA, artifact_dir=str(tmp_path))
    try:
        payload = await respond(catalog.report(spec, docx=True))
        baseline = await respond(catalog.report(spec))
    finally:
        await http.close()

    assert payload["status"] == "ok", payload.get("message")
    assert payload["report_id"] == baseline["report_id"]
    assert "report.docx" not in {f["name"] for f in baseline["artifact"]["files"]}
    content = read(payload, "report.docx", tmp_path)
    report = json.loads(read(payload, "report.json", tmp_path))
    assert any(getattr(part, "name", None) == "report.docx" for part in reply(payload).content)

    document = docx.Document(BytesIO(content))
    assert document.paragraphs[0].text == spec["title"]
    assert [p.text for p in document.paragraphs if p.style.name == "Heading 1"] == [
        section["title"]
        for section in report["sections"]
        if section["kind"] != "limitations" or section["items"]
    ]
    for i, row in enumerate(report["sections"][1]["table"]["rows"], start=1):
        assert [cell.text for cell in document.tables[0].rows[i].cells] == [
            row["label"],
            row["period"],
            row["value"],
        ]
    assert len(document.inline_shapes) == 1
    expected_alt = report["sections"][2]["chart"]["alt_text"]
    assert document.inline_shapes[0]._inline.docPr.get("descr") == expected_alt
    with ZipFile(BytesIO(content)) as archive:
        assert b"w:tblHeader" in archive.read("word/document.xml")
        assert b"https://" in archive.read("word/_rels/document.xml.rels")
        assert b'w:instr="PAGE"' in archive.read("word/footer1.xml")


async def test_word_marks_unverified_plain_text(tmp_path):
    docx = pytest.importorskip("docx")
    pytest.importorskip("matplotlib")
    spec = report_spec(
        summary(host("<b>Rate was 18.0%</b>.", ("rate", "18.0%"))),
        language="en",
        on_unverified="mark",
    )
    http, catalog = labour_catalog(DATA, artifact_dir=str(tmp_path))
    try:
        payload = await respond(catalog.report(spec, docx=True))
    finally:
        await http.close()
    assert payload["status"] == "draft"
    document = docx.Document(BytesIO(read(payload, "report.docx", tmp_path)))
    text = "\n".join(p.text for p in document.paragraphs)
    assert "UNVERIFIED DRAFT" in text
    assert "<b>Rate was 18.0%</b>." in text
