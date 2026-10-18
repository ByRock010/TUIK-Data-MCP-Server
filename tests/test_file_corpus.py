import hashlib
import importlib.util
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tuik_mcp.utils.normalization import official_url

FILES = Path(__file__).parent / "fixtures/files"
MANIFEST = json.loads((FILES / "manifest.json").read_text())


def test_corpus_provenance_size_and_coverage():
    sources = json.loads((FILES / "sources.json").read_text())
    assert len(MANIFEST) >= 12
    assert {row["key"] for row in MANIFEST} == {row["key"] for row in sources}
    assert sum(row["size_bytes"] for row in MANIFEST) < 3_000_000
    assert {row["format"] for row in MANIFEST} == {"xls", "xlsx", "pdf"}
    assert len({row["sha256"] for row in MANIFEST}) == len(MANIFEST)
    for row in MANIFEST:
        body = (FILES / row["file"]).read_bytes()
        assert len(body) == row["size_bytes"]
        assert hashlib.sha256(body).hexdigest() == row["sha256"]
        golden = json.loads((FILES / row["golden"]).read_text())
        assert golden["sha256"] == row["sha256"]
        assert golden["format"] == row["format"]
        assert official_url(row["source_url"], fetch=True)
        assert row["publisher"] == "Türkiye İstatistik Kurumu (TÜİK)"
        assert datetime.fromisoformat(row["retrieved_at"]).utcoffset().total_seconds() == 0
        assert row["resource_id"] or row["search_query"]


@pytest.mark.parametrize("entry", MANIFEST, ids=lambda row: row["key"])
def test_golden_source_observations(entry):
    golden = json.loads((FILES / entry["golden"]).read_text())
    path = FILES / entry["file"]
    if entry["format"] == "pdf":
        pdfplumber = pytest.importorskip("pdfplumber")
        with pdfplumber.open(path) as pdf:
            assert len(pdf.pages) == golden["page_count"]
            for sample in golden["page_samples"]:
                page = pdf.pages[sample["page"] - 1]
                clean = page.dedupe_chars()
                text = " ".join((clean.extract_text() or "").split())
                assert text.startswith(sample["text_prefix"])
                assert len(page.chars) == sample["raw_chars"]
                assert len(clean.chars) == sample["deduplicated_chars"]
        return
    if entry["format"] == "xls":
        xlrd = pytest.importorskip("xlrd")
        book = xlrd.open_workbook(path, formatting_info=True)
        sheet = book.sheet_by_name(golden["sheet"])
        assert book.sheet_names() == golden["sheets"]
        assert [sheet.nrows, sheet.ncols] == golden["source_shape"]
        assert sorted(map(list, sheet.merged_cells)) == golden["merged_ranges"]
        for sample in golden["raw_rows"]:
            assert sheet.row_values(sample["row"]) == sample["values"]
        for sample in golden["numeric_samples"]:
            cell = sheet.cell(sample["row"], sample["column"])
            assert cell.value == sample["value"]
            fmt = book.format_map[book.xf_list[cell.xf_index].format_key].format_str
            assert fmt == sample["number_format"]
    else:
        openpyxl = pytest.importorskip("openpyxl")
        book = openpyxl.load_workbook(path, data_only=True, read_only=True)
        try:
            assert book.sheetnames == golden["sheets"]
            sheet = book[golden["sheet"]]
            assert [sheet.max_row, sheet.max_column] == golden["source_shape"]
            for sample in golden["raw_rows"]:
                row = tuple(
                    sheet.iter_rows(
                        min_row=sample["row"] + 1, max_row=sample["row"] + 1, values_only=True
                    )
                )[0]
                assert list(row) == sample["values"]
        finally:
            book.close()


def test_corpus_covers_observed_layout_and_pdf_edge_cases():
    def load(key):
        return json.loads((FILES / f"{key}.golden.json").read_text())

    cpi, labour = load("cpi-index"), load("labour-basic")
    assert cpi["unit"] == "2025=100" and len(cpi["header_rows"]) == 2
    assert cpi["merged_ranges"] and cpi["section_sample"]
    assert any(note.startswith("(*)") for note in cpi["notes"])
    assert labour["source_shape"][0] - labour["last_nonempty_row"] > 400
    assert labour["forward_fill_sample"]["raw_value"] == ""
    formats = {item["number_format"] for item in labour["numeric_samples"]}
    assert {"###,###", "0.0", "General"} <= formats
    pdf = load("ict-publication")
    assert pdf["page_count"] == 17
    assert any(s["raw_chars"] > s["deduplicated_chars"] for s in pdf["page_samples"])
    assert load("labour-methodology")["page_count"] == 10


def test_fetcher_recognizes_real_xlsx_despite_upstream_xls_label():
    script = Path(__file__).parents[1] / "scripts/fetch_fixtures.py"
    spec = importlib.util.spec_from_file_location("fetch_fixtures", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    body = (FILES / "cpi-basket.xlsx").read_bytes()
    assert module.fixture_format(body, "zip") == "xlsx"
    with pytest.raises(ValueError, match="Unexpected fixture format"):
        module.fixture_format(b"arbitrary", "csv")


@pytest.mark.parametrize("change", [None, "resource_id", "query", "title", "body"])
async def test_refresh_resumes_only_matching_source_and_content(tmp_path, monkeypatch, change):
    script = Path(__file__).parents[1] / "scripts/fetch_fixtures.py"
    spec = importlib.util.spec_from_file_location("fetch_fixtures_resume", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = {"key": "test", "resource_id": "press:1", "file_title": "Same title"}
    old = b"%PDF-old fixture"
    saved = {
        "key": "test",
        "title": "Same title",
        "resource_id": "press:1",
        "search_query": None,
        "file": "test.pdf",
        "format": "pdf",
        "sha256": hashlib.sha256(old).hexdigest(),
        "size_bytes": len(old),
    }
    (tmp_path / "test.pdf").write_bytes(old)
    (tmp_path / "manifest.json").write_text(json.dumps([saved]))
    if change == "resource_id":
        source["resource_id"] = "press:2"
    elif change == "query":
        source = {"key": "test", "query": "new source", "title": "Same title"}
    elif change == "title":
        source["file_title"] = "New title"
    elif change == "body":
        (tmp_path / "test.pdf").write_bytes(b"corrupted")
    calls = []
    link = SimpleNamespace(title=source.get("file_title", "Same title"))
    resource = SimpleNamespace(
        download_links=[link], id="resource:test", source_url="official page"
    )

    class Service:
        def __init__(self, http):
            self.portal = self

        async def resource(self, resource_id):
            calls.append(resource_id)
            return resource

        async def search(self, query, limit):
            calls.append(query)
            return SimpleNamespace(
                results=[SimpleNamespace(title="Same title", id="resource:test")]
            )

        async def download(self, selected):
            calls.append("download")
            return b"%PDF-new fixture", SimpleNamespace(
                format="pdf", source_url="official download"
            )

    monkeypatch.setattr(module, "CatalogService", Service)
    result = await module.fetch([source], tmp_path, resume=True)
    if change is None:
        assert calls == []
        assert result[0]["sha256"] == saved["sha256"]
    else:
        assert calls[-1] == "download"
        assert result[0]["sha256"] != saved["sha256"]
        assert result[0]["resource_id"] == source.get("resource_id")
    assert json.loads((tmp_path / "manifest.json").read_text()) == result
