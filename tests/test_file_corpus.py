import hashlib
import importlib.util
import io
import json
import zipfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from tuik_mcp.clients.tuik_portal import file_format
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


def test_production_recognizes_real_xlsx_despite_upstream_xls_label():
    body = (FILES / "cpi-basket.xlsx").read_bytes()
    assert file_format(body, "application/vnd.ms-excel") == "xlsx"


async def test_data_response_verifies_mislabeled_xlsx(catalog, monkeypatch):
    service, _ = catalog

    async def fetch_file(url):
        return httpx.Response(
            200,
            content=(FILES / "cpi-basket.xlsx").read_bytes(),
            headers={"content-type": "application/vnd.ms-excel"},
        )

    monkeypatch.setattr(service.portal, "_fetch_file", fetch_file)
    data = await service.data("press:57987")
    assert data.status == "download_available"
    assert data.download_links[0].format == "xlsx" and data.download_links[0].verified


@pytest.mark.parametrize("names", [["report.txt"], ["[Content_Types].xml"], ["xl/workbook.xml"]])
def test_generic_zip_is_not_a_spreadsheet_even_with_xlsx_mime(names):
    body = io.BytesIO()
    with zipfile.ZipFile(body, "w") as archive:
        for name in names:
            archive.writestr(name, "data")
    assert (
        file_format(
            body.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        == "zip"
    )


def test_truncated_zip_does_not_raise_or_claim_xlsx():
    assert file_format(b"PK\x03\x04truncated", "application/vnd.ms-excel") == "zip"


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


async def test_interrupted_resume_keeps_unprocessed_manifest_entries(tmp_path, monkeypatch):
    script = Path(__file__).parents[1] / "scripts/fetch_fixtures.py"
    spec = importlib.util.spec_from_file_location("fetch_fixtures_interrupted", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sources, previous = [], []
    for index in range(3):
        key = f"test-{index}"
        body = f"%PDF-old {index}".encode()
        sources.append({"key": key, "resource_id": f"press:{index}", "file_title": key})
        previous.append(
            {
                "key": key,
                "title": key,
                "resource_id": f"press:{index}",
                "search_query": None,
                "file": f"{key}.pdf",
                "format": "pdf",
                "sha256": hashlib.sha256(body).hexdigest(),
                "size_bytes": len(body),
            }
        )
        (tmp_path / f"{key}.pdf").write_bytes(body)
    (tmp_path / "manifest.json").write_text(json.dumps(previous))
    # Refresh the first file, then interrupt before processing the final valid file.
    (tmp_path / "test-0.pdf").write_bytes(b"corrupted")
    (tmp_path / "test-1.pdf").write_bytes(b"corrupted")
    calls = []
    interrupt = True

    class Service:
        def __init__(self, http):
            self.portal = self

        async def resource(self, resource_id):
            if resource_id == "press:1" and interrupt:
                raise RuntimeError("refresh interrupted")
            key = "test-" + resource_id.split(":")[1]
            return SimpleNamespace(
                download_links=[SimpleNamespace(title=key)], source_url="official page"
            )

        async def download(self, link):
            calls.append(link.title)
            return b"%PDF-new fixture", SimpleNamespace(format="pdf", source_url="official file")

    monkeypatch.setattr(module, "CatalogService", Service)
    with pytest.raises(RuntimeError, match="refresh interrupted"):
        await module.fetch(sources, tmp_path, resume=True)
    partial = json.loads((tmp_path / "manifest.json").read_text())
    assert len(partial) == 3 and partial[2] == previous[2]
    assert partial[0]["sha256"] != previous[0]["sha256"]
    interrupt = False
    result = await module.fetch(sources, tmp_path, resume=True)
    assert calls == ["test-0", "test-1"]
    assert len(result) == 3 and result[2] == previous[2]
