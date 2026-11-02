"""Best-effort PDF tables (#8) on the recorded TÜİK publication and methodology PDFs."""

import re
from functools import cache
from pathlib import Path

import httpx
import pytest

from tuik_mcp.clients.base import HttpClient
from tuik_mcp.config import PORTAL, Settings
from tuik_mcp.models.dataset import DownloadLink, TuikResource
from tuik_mcp.parsers import pdf_tables
from tuik_mcp.parsers.pdf import parse_pdf
from tuik_mcp.parsers.pdf_tables import HEURISTIC, NO_HEADER, cell_text
from tuik_mcp.server import respond
from tuik_mcp.services.catalog import CatalogService

FILES = Path(__file__).parent / "fixtures/files"
ID = "publication:hanehalki~0123456789"
URL = f"{PORTAL}/api/tr/data/downloads?t=y&p=fixture"
IDENTIFIER = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")


@cache
def body(name):
    return (FILES / f"{name}.pdf").read_bytes()


def tables(name, page):
    return parse_pdf(body(name), page=page, tables=True)


def test_cell_text_joins_wrapped_codes_and_words():
    assert cell_text(None) == ""
    assert cell_text("INT_FAAL_SAGLIK_TIBKONS_REC\nETE") == "INT_FAAL_SAGLIK_TIBKONS_RECETE"
    assert cell_text("YAPAYZEKA_KULLANMAMA_HBR\n_YOK") == "YAPAYZEKA_KULLANMAMA_HBR_YOK"
    assert cell_text("Hanede bulunan\nbilişim  ekipmanları") == "Hanede bulunan bilişim ekipmanları"
    assert cell_text("HANE\nBİLGİLERİ") == "HANE BİLGİLERİ"
    assert cell_text("istatis-\ntikler") == "istatistikler"


def test_titled_header_table_from_a_ruled_publication_page():
    (table,) = tables("ict-publication", 5).tables
    assert table.page == 5 and table.extraction == "pdf" and table.layout == "table"
    assert table.title == (
        "HANEHALKI BİLİŞİM TEKNOLOJİLERİ KULLANIM ARAŞTIRMASI MİKRO VERİ SETİ KILAVUZU, 2026"
    )
    assert table.columns[:2] == ["Değişken Adı", "Değişken Açıklama"]
    assert table.columns[3] == "Seçenekler" and len(table.columns) == 4
    assert table.total_rows == len(table.rows) == 41
    assert table.rows[0]["Değişken Adı"] == "HANE BİLGİLERİ"  # Section rows are kept.
    year = next(row for row in table.rows if row["Değişken Adı"] == "REFERANS_YIL")
    assert year["Değişken Açıklama"] == "Anketin uygulandığı yıl"
    assert year[table.columns[2]] == "4"
    assert table.warnings == [HEURISTIC]


def test_continuation_pages_are_raw_and_wrapped_codes_are_joined():
    (table,) = tables("ict-publication", 8).tables
    assert table.layout == "raw" and table.title is None
    assert table.columns == ["column_1", "column_2", "column_3", "column_4"]
    assert table.warnings == [HEURISTIC, NO_HEADER]
    assert table.rows[0]["column_1"] == "INT_FAAL_SAGLIK_TIBKONS_RECETE"


@pytest.mark.parametrize("page", range(5, 13))
def test_every_code_on_a_data_dictionary_page_is_in_its_table(page):
    parsed = tables("ict-publication", page)
    (table,) = parsed.tables
    cells = {value for row in table.rows for value in row.values()}
    codes = set(IDENTIFIER.findall(" ".join(cells)))
    # Page text splits wrapped codes into line fragments; the table rejoins them.
    for code in IDENTIFIER.findall(parsed.pages[0].text):
        assert any(code in found for found in codes), code


@pytest.mark.parametrize(
    ("name", "page"),
    [("ict-publication", 1), ("ict-publication", 3), ("labour-methodology", 2)],
)
def test_decorative_boxes_and_prose_pages_have_no_tables(name, page):
    parsed = tables(name, page)
    assert parsed.tables == [] and parsed.warnings == []


def test_flowchart_is_not_reported_as_a_table():
    parsed = tables("labour-methodology", 10)
    assert parsed.pages[0].text.startswith("Ek 2:") and parsed.tables == []


def test_zebra_striped_fragments_merge_and_unruled_rows_are_rebuilt():
    (table,) = tables("labour-methodology", 9).tables
    assert table.title == "Ek 1: 2026 Yılı Anket Referans Ay ve Haftaları"
    assert table.columns == ["Referans Ay", "Referans Hafta", "Referans Gün"]
    months = [row["Referans Ay"] for row in table.rows]
    assert months == [
        "Ocak",
        "Şubat",
        "Mart",
        "Nisan",
        "Mayıs",
        "Haziran",
        "Temmuz",
        "Ağustos",
        "Eylül",
        "Ekim",
        "Kasım",
        "Aralık",
    ]
    assert table.rows[1] == {
        "Referans Ay": "Şubat",
        "Referans Hafta": "6-9",
        "Referans Gün": "2 Şubat – 1 Mart",
    }
    assert table.rows[-1]["Referans Gün"] == "30 Kasım – 3 Ocak 2027"
    assert "6 row(s) without ruling lines were rebuilt" in table.warnings[1]


def test_tables_are_opt_in(monkeypatch):
    def fail(*args):
        raise AssertionError("table detection must not run")

    monkeypatch.setattr(pdf_tables, "page_tables", fail)
    parsed = parse_pdf(body("ict-publication"), page=5)
    assert parsed.tables == [] and parsed.pages[0].text


def test_table_detection_failure_keeps_the_page_text(monkeypatch):
    def broken(*args):
        raise RuntimeError("unexpected layout")

    monkeypatch.setattr(pdf_tables, "page_tables", broken)
    parsed = tables("ict-publication", 5)
    assert parsed.pages[0].text and parsed.tables == []
    assert parsed.warnings == ["Table detection failed on page 5; its text is unaffected."]


def test_chart_pages_and_cell_budget_are_bounded(monkeypatch):
    monkeypatch.setattr(pdf_tables, "MAX_TABLE_EDGES", 10)
    skipped = tables("ict-publication", 5)
    assert skipped.tables == [] and "Table detection skipped on page 5" in skipped.warnings[0]
    monkeypatch.setattr(pdf_tables, "MAX_TABLE_EDGES", 20_000)
    monkeypatch.setattr(pdf_tables, "MAX_TABLE_CELLS_PER_PAGE", 40)
    (table,) = tables("ict-publication", 5).tables
    assert len(table.rows) == 10 and table.total_rows == 41
    assert "truncated to the per-page budget" in table.warnings[-1]


def document_service(name):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=body(name), headers={"content-type": "application/pdf"})

    http = HttpClient(
        Settings(min_interval_seconds=0, download_interval_seconds=0),
        httpx.MockTransport(handler),
    )
    catalog = CatalogService(http)
    catalog.cache.set(
        "metadata:" + ID,
        TuikResource(
            id=ID,
            title="Hanehalkı Bilişim Teknolojileri",
            source_url=URL,
            resource_type="publication",
            download_links=[DownloadLink(title="Kılavuz", source_url=URL, format="pdf")],
        ),
        3600,
    )
    return http, catalog, calls


async def test_read_document_returns_page_tables_only_when_requested():
    http, catalog, calls = document_service("ict-publication")
    try:
        plain = await catalog.document(ID, page=5)
        assert plain.status == "ok" and plain.tables is None
        result = await respond(catalog.document(ID, page=5, max_chars=100, tables=True))
        (table,) = result["tables"]
        assert table["page"] == 5 and table["layout"] == "table"
        assert table["extraction"] == "pdf" and table["total_rows"] == 41
        # Tables do not depend on the text window and come from the same snapshot.
        assert result["truncated"] and result["next_offset"] == 100 and len(calls) == 1
        again = await catalog.document(ID, page=5, offset=100, tables=True)
        assert [t.model_dump(mode="json") for t in again.tables] == [table]
        assert len(calls) == 1
        with pytest.raises(ValueError, match="tables must be a boolean"):
            await catalog.document(ID, page=5, tables="yes")
    finally:
        await http.close()


async def test_page_without_tables_reports_an_empty_list():
    http, catalog, _ = document_service("labour-methodology")
    try:
        result = await catalog.document(ID, page=10, tables=True)
        assert result.status == "ok" and result.tables == []
    finally:
        await http.close()
