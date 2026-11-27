"""Searchable PDF reports from the same canonical evidence as the other outputs."""

import base64
import io
import json
import sys
from pathlib import Path

import pdfplumber
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_observations import labour_catalog
from test_reports import DATA, EVIDENCE, build, findings, host, read, report_spec, summary

from tuik_mcp.models.report import Report
from tuik_mcp.server import respond

pytest.importorskip("reportlab")


def brief(**fields):
    return report_spec(
        summary({"evidence": ["rate", "gap"]}, {"evidence": ["peak"]}),
        {"kind": "table", "table": {"rows": ["rate", "gap", "peak", "unemployed", "fewer"]}},
        {"kind": "chart", "chart": {"series": ["trend"]}},
        **fields,
    )


async def save_pdf(spec, root, data=DATA):
    http, catalog = labour_catalog(data, artifact_dir=str(root))
    try:
        payload = await respond(catalog.report(spec, pdf=True))
        assert payload["status"] in ("ok", "draft"), payload
        return payload, read(payload, "report.pdf", root)
    finally:
        await http.close()


async def test_pdf_bundle_keeps_values_sources_and_vector_chart(tmp_path):
    spec = brief(source_cutoff="2026-03-31")
    payload, content = await save_pdf(spec, tmp_path)
    canonical = Report.model_validate_json(read(payload, "report.json", tmp_path))
    assert canonical.status == "ok"
    assert "report.md" in {f["name"] for f in payload["artifact"]["files"]}
    with pdfplumber.open(io.BytesIO(content)) as doc:
        assert 3 <= len(doc.pages) <= 5
        text = " ".join(page.extract_text() for page in doc.pages)
        compact = " ".join(text.split())
        for sentence in findings(payload):
            assert sentence["text"] in compact
        assert "Veri kesim tarihi · 2026-03-31" in compact
        assert "Hesaplanan" in compact and "Yayımlanan" in compact
        assert any(page.lines for page in doc.pages) and not any(page.images for page in doc.pages)
        links = {item["uri"] for page in doc.pages for item in page.hyperlinks}
        assert links == {item.source.source_url for item in canonical.evidence}
        for evidence in canonical.evidence:
            if evidence.display is not None:
                assert evidence.display in compact
        for index, page in enumerate(doc.pages, 1):
            assert page.extract_text().splitlines()[-1].endswith(str(index))


async def test_long_tables_repeat_headers_with_turkish_labels(tmp_path):
    evidence = [
        EVIDENCE[0]
        | {"key": f"rate_{index}", "label": "Şehirlerde ölçüm ve değişim " * 4 + "gösterge"}
        for index in range(20)
    ]
    spec = report_spec(
        summary({"evidence": ["rate_0"]}),
        {"kind": "table", "table": {"rows": [item["key"] for item in evidence]}},
        evidence=evidence,
        title="Türkiye işgücü göstergeleri üzerine ayrıntılı değerlendirme ve ölçüm sonuçları "
        "ile yayımlanan değerlerin kaynak kapsamına göre açıklanması",
    )
    _, content = await save_pdf(spec, tmp_path)
    with pdfplumber.open(io.BytesIO(content)) as doc:
        pages = [page.extract_text() for page in doc.pages]
        assert sum("Gösterge" in page and "Kanıt" in page for page in pages) >= 2
        assert sum(page.count("Şehirlerde") for page in pages) >= 20


async def test_draft_escapes_markup_and_carries_status_on_every_page(tmp_path):
    spec = report_spec(
        summary(
            host("Oran %8,8 oldu. <img src='https://example.invalid/asset'/>", ("rate", "%8,8"))
        ),
        {"kind": "table", "table": {"rows": ["rate"]}},
        on_unverified="mark",
    )
    payload, content = await save_pdf(spec, tmp_path)
    assert payload["status"] == "draft"
    with pdfplumber.open(io.BytesIO(content)) as doc:
        assert all("DOĞRULANMAMIŞ TASLAK" in page.extract_text() for page in doc.pages)
        assert "<img" in doc.pages[0].extract_text()
        assert not any(
            "example.invalid" in link["uri"] for page in doc.pages for link in page.hyperlinks
        )


@pytest.mark.parametrize("budget, limit", [("MAX_PAGES", 1), ("PDF_TIME_BUDGET", 0)])
async def test_pdf_disabled_on_legacy_call_and_refuses_over_budget(
    tmp_path, monkeypatch, budget, limit
):
    from tuik_mcp.reports import pdf as pdf_output

    legacy = await build(brief(), tmp_path / "legacy")
    assert not any(f["name"] == "report.pdf" for f in legacy["artifact"]["files"])
    monkeypatch.setattr(pdf_output, budget, limit)
    root = tmp_path / "limited"
    http, catalog = labour_catalog(DATA, artifact_dir=str(root))
    try:
        refused = await respond(catalog.report(brief(), pdf=True))
        assert refused["code"] == "pdf_render_limit"
        assert not list(root.glob("art_*"))
    finally:
        await http.close()


async def test_pdf_extra_unavailable_is_actionable(tmp_path, monkeypatch):
    import builtins

    original = builtins.__import__

    def fail_optional(name, *args, **kwargs):
        if name == "tuik_mcp.reports.pdf":
            raise ModuleNotFoundError("reportlab")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_optional)
    http, catalog = labour_catalog(DATA, artifact_dir=str(tmp_path))
    try:
        payload = await respond(catalog.report(brief(), pdf=True))
        assert payload["code"] == "pdf_unavailable" and "pdf-out" in payload["message"]
        assert not list(tmp_path.iterdir())
    finally:
        await http.close()


async def test_stdio_reads_the_pdf_resource(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).with_name("search_stdio_server.py")), "portal"],
        env={"TUIK_ARTIFACT_DIR": str(tmp_path)},
    )
    spec = {
        "title": "Dış ticaret karşılaştırması",
        "language": "en",
        "evidence": [
            {
                "kind": "series",
                "key": "trade",
                "resource_id": "flow:DF_ALTINSIZ_DTH+V1.0",
                "period_from": "2015-01",
                "period_to": "2015-03",
            }
        ],
        "sections": [{"kind": "chart", "chart": {"series": ["trade"], "type": "bar"}}],
    }
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as client:
        await client.initialize()
        result = await client.call_tool("build_tuik_report", {"spec": spec, "pdf": True})
        assert result.structuredContent["status"] == "ok", json.dumps(result.structuredContent)
        link = next(item for item in result.content if getattr(item, "name", None) == "report.pdf")
        (asset,) = (await client.read_resource(link.uri)).contents
        assert asset.mimeType == "application/pdf"
        assert base64.b64decode(asset.blob).startswith(b"%PDF-")
