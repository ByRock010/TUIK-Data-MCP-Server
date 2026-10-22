import io
import json
from pathlib import Path

import httpx
import pytest

from tuik_mcp.clients.base import HttpClient
from tuik_mcp.config import PORTAL, Settings
from tuik_mcp.models.dataset import DownloadLink, TuikResource
from tuik_mcp.parsers import parse_document
from tuik_mcp.parsers.errors import ParseError, ParseLimitError
from tuik_mcp.parsers.limits import ParseLimits
from tuik_mcp.parsers.models import DocumentPage
from tuik_mcp.parsers.pdf import chunk_pages, normalize_text, parse_pdf
from tuik_mcp.server import respond
from tuik_mcp.services import files
from tuik_mcp.services.catalog import CatalogService

FILES = Path(__file__).parent / "fixtures/files"
ID = "publication:document~0123456789"
URL = f"{PORTAL}/api/tr/data/downloads?t=y&p=fixture"


def blank_pdf():
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources << >> >>",
    ]
    output = io.BytesIO(b"%PDF-1.4\n")
    output.seek(0, 2)
    positions = [0]
    for number, obj in enumerate(objects, 1):
        positions.append(output.tell())
        output.write(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = output.tell()
    output.write(f"xref\n0 {len(positions)}\n0000000000 65535 f \n".encode())
    for offset in positions[1:]:
        output.write(f"{offset:010d} 00000 n \n".encode())
    output.write(
        f"trailer\n<< /Size {len(positions)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    )
    return output.getvalue()


def service(body, **settings):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=body, headers={"content-type": "application/pdf"})

    http = HttpClient(
        Settings(min_interval_seconds=0, download_interval_seconds=0, **settings),
        httpx.MockTransport(handler),
    )
    catalog = CatalogService(http)
    catalog.cache.set(
        "metadata:" + ID,
        TuikResource(
            id=ID,
            title="TÜİK belgesi",
            source_url=URL,
            resource_type="publication",
            download_links=[DownloadLink(title="TÜİK belgesi", source_url=URL, format="pdf")],
        ),
        3600,
    )
    return http, catalog, calls


@pytest.mark.parametrize("name", ["ict-publication", "labour-methodology"])
def test_real_pdf_goldens_page_counts_text_and_glyph_deduplication(name):
    golden = json.loads((FILES / f"{name}.golden.json").read_text())
    body = (FILES / f"{name}.pdf").read_bytes()
    for sample in golden["page_samples"]:
        parsed = parse_pdf(body, page=sample["page"])
        assert parsed.page_count == golden["page_count"]
        assert len(parsed.pages) == 1 and parsed.pages[0].page == sample["page"]
        text = " ".join(parsed.pages[0].text.split())
        assert text.startswith(sample["text_prefix"])
    first = parse_document(body, "pdf")[0]
    assert first.page == 1 and "2026" in first.text
    assert "22002266" not in first.text


def test_limits_errors_and_no_text_are_distinct():
    body = (FILES / "ict-publication.pdf").read_bytes()
    with pytest.raises(ParseLimitError, match="document pages"):
        parse_pdf(body, budgets=ParseLimits(max_pdf_pages=2))
    with pytest.raises(ParseLimitError, match="pages per call"):
        parse_pdf(body, pages=6)
    with pytest.raises(ParseLimitError, match="characters"):
        parse_pdf(body, page=2, budgets=ParseLimits(max_pdf_chars_per_page=10))
    with pytest.raises(ValueError, match="out of range"):
        parse_pdf(body, page=18)
    with pytest.raises(ParseError, match="unreadable"):
        parse_pdf(b"%PDF-broken")
    assert parse_pdf(blank_pdf()).pages[0].text == ""


def test_normalization_and_chunks_preserve_turkish_and_page_locators():
    text = normalize_text("İş gü-\ncü  araştırması\n\n\nTÜİK\nı İ ö ü ş ç")
    assert text == "İş gücü araştırması\n\nTÜİK\nı İ ö ü ş ç"
    pages = [
        DocumentPage(page=3, heading="Amaç", text=text),
        DocumentPage(page=4, heading="Kapsam", text="x" * 100),
    ]
    chunks = chunk_pages(pages, max_chars=20)
    assert all(len(c.text) <= 20 for c in chunks)
    assert {c.page for c in chunks} == {3, 4}
    assert all(c.heading == ("Amaç" if c.page == 3 else "Kapsam") for c in chunks)
    assert "".join(c.text for c in chunks if c.page == 4) == "x" * 100


async def test_pagination_never_skips_the_rest_of_a_truncated_page_and_uses_cache(monkeypatch):
    http, catalog, calls = service((FILES / "labour-methodology.pdf").read_bytes())
    parses = []
    original = files.parse_document_source

    def parse(*args, **kwargs):
        parses.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(files, "parse_document_source", parse)
    try:
        full = await catalog.document(ID, page=3)
        assert full.page_count == 10 and full.page_start == full.page_end == 3
        assert full.next_page == 4 and full.next_offset == 0
        assert full.source_url == URL and full.download_links[0].verified
        pieces, offset = [], 0
        while True:
            result = await catalog.document(ID, page=3, max_chars=137, offset=offset)
            assert len(result.text) <= 137 and result.retrieved_at == full.retrieved_at
            pieces.append(result.text)
            if not result.truncated:
                assert result.next_page == 4 and result.next_offset == 0
                break
            assert result.next_page == 3 and result.next_offset == offset + len(result.text)
            offset = result.next_offset
        assert "".join(pieces) == full.text
        assert len(calls) == len(parses) == 1
        last = await catalog.document(ID, page=10)
        assert not last.has_more and last.next_page is None and last.next_offset is None
        assert len(calls) == 1 and len(parses) == 2
    finally:
        await http.close()


async def test_blank_page_is_explicit_and_corrupt_or_large_pdf_is_source_error():
    http, catalog, _ = service(blank_pdf())
    try:
        result = await catalog.document(ID)
        assert result.status == "no_text_layer" and result.text == "" and result.warnings
    finally:
        await http.close()
    for body, settings, code in [
        (b"%PDF-broken", {}, "parse_failed"),
        (
            (FILES / "ict-publication.pdf").read_bytes(),
            {"parse_max_pdf_pages": 2},
            "parse_limit_exceeded",
        ),
    ]:
        http, catalog, _ = service(body, **settings)
        try:
            error = await respond(catalog.document(ID))
            assert error["code"] == code and error["source_url"] == URL
        finally:
            await http.close()


async def test_multiple_pdf_choices_non_document_and_invalid_paging():
    http, catalog, calls = service((FILES / "ict-publication.pdf").read_bytes())
    try:
        record = await catalog.resource(ID)
        record.download_links.append(
            DownloadLink(title="İkinci", source_url=URL + "2", format="pdf")
        )
        catalog.cache.set("metadata:" + ID, record, 3600)
        assert (await catalog.document(ID)).status == "document_choices" and not calls
        record.download_links.clear()
        catalog.cache.set("metadata:" + ID, record, 3600)
        assert (await catalog.document(ID)).status == "not_document" and not calls
        for kwargs in [{"page": 0}, {"max_chars": 0}, {"max_chars": 20001}, {"offset": -1}]:
            assert (await respond(catalog.document(ID, **kwargs)))["code"] == "invalid_input"
    finally:
        await http.close()
