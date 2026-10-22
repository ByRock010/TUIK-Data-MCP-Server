import json

import pytest
from pydantic import ValidationError

from tuik_mcp.clients.base import SourceError
from tuik_mcp.models.common import SearchInput
from tuik_mcp.server import respond
from tuik_mcp.utils.normalization import official_url, plain_text, turkish_casefold, validate_id


def test_turkish_normalization():
    assert turkish_casefold("İŞSİZLİK") == "işsizlik"
    assert turkish_casefold("IĞDIR") == "ığdır"
    assert SearchInput(query=" nu\u0308fus ").query == "nüfus"
    assert (
        plain_text("<script>bad()</script>Genç <span>işsizlik</span> &amp; ücret")
        == "Genç işsizlik & ücret"
    )


@pytest.mark.parametrize("query", ["", "   ", "x" * 201, "a\x00b", "a\nb"])
def test_invalid_query(query):
    with pytest.raises(ValidationError):
        SearchInput(query=query)


@pytest.mark.parametrize(
    "value",
    [
        "https://example.org",
        "press:../1",
        "press:1?x=y",
        "press:0",
        "flow:X/../secret",
        "resource:zz",
        "classification:-1",
    ],
)
def test_invalid_id(value):
    with pytest.raises(ValueError):
        validate_id(value)


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.test",
        "https://www.tuik.gov.tr.evil.test",
        "http://127.0.0.1",
        "file:///etc/passwd",
        "https://user@www.tuik.gov.tr",
        "https://www.tuik.gov.tr:444",
        "//evil.test/a",
        "https://www.tuik.gov.tr\\@evil.test/",
        "https://www.tuik.gov.tr\n/",
    ],
)
def test_allowed_domains(url):
    with pytest.raises(ValueError):
        official_url(url, fetch=True)


async def test_successful_search_and_metadata(catalog):
    service, calls = catalog
    result = await service.search("işsizlik")
    assert len(result.results) == 10
    assert result.results[0].title == "İşsizlik Oranı"
    assert all(
        r.source_url.startswith("https://") and r.publisher and r.retrieved_at
        for r in result.results
    )
    assert json.loads(calls[0].content)["text"] == "işsizlik"
    assert calls[0].headers["user-agent"].startswith("Mozilla/5.0 (compatible; tuik-mcp/")
    metadata = await service.resource("press:57987")
    assert metadata.title == "İşgücü İstatistikleri, Ağustos 2026"
    assert metadata.download_links and metadata.metadata
    assert metadata.updated_at is None
    assert "<" not in metadata.description
    assert metadata.source_url.endswith("/tr/press/57987")


async def test_empty_search(catalog):
    service, _ = catalog
    result = await service.search("no matches")
    assert result.results == [] and result.total == 0 and result.source_url


async def test_cache_and_data_download(catalog):
    service, calls = catalog
    result = await service.search("işsizlik")
    await service.search("İŞSİZLİK")
    assert len(calls) == 1
    data = await service.data(result.results[0].id)
    assert data.status == "ok"
    assert data.download_links[0].verified
    assert data.download_links[0].format == "xls"
    assert data.source_url and data.rows
    before = len(calls)
    await service.data(result.results[0].id)
    assert len(calls) == before


async def test_csv_data_filtering_and_limits(catalog):
    service, _ = catalog
    result = await service.data("flow:DF_ALTINSIZ_DTH+V1.0", limit=1)
    assert result.status == "ok" and len(result.rows) == 1
    assert result.total_rows == 3 and result.truncated
    assert result.source_url.endswith("/file/csv")
    column = next(c for c in result.columns if c.startswith("Zaman ("))
    value = result.rows[0][column]
    result = await service.data("flow:DF_ALTINSIZ_DTH+V1.0", {column: value})
    assert all(row[column] == value for row in result.rows)
    empty = await service.data("flow:DF_ALTINSIZ_DTH+V1.0", {column: "not present"})
    assert empty.rows == [] and empty.total_rows == 0
    with pytest.raises(SourceError, match="exact column"):
        await service.data("flow:DF_ALTINSIZ_DTH+V1.0", {"made-up": "1"})


async def test_unsupported_filters_and_unstructured(catalog):
    service, _ = catalog
    records = await service.search_classifications("NACE", 2)
    record = records.results[0]
    assert record.title == "NACE Rev.1" and record.source_url
    result = await service.data(record.id)
    assert result.status == "not_structured" and result.source_url
    result = await service.data(record.id, {"year": "2025"})
    assert result.status == "unsupported_filters"
    themes = await service.themes()
    assert any(t.name == "Nüfus ve Demografi" for t in themes.themes)
    assert all(t.source_url for t in themes.themes)


async def test_invalid_input_and_expired_handle(catalog):
    service, calls = catalog
    result = await respond(service.resource("https://example.com"))
    assert result["status"] == "error" and result["code"] == "invalid_input"
    assert result["source_url"] and not calls
    with pytest.raises(SourceError, match="Search again"):
        await service.resource("resource:" + "0" * 64)
    for limit in [0, 1001, True]:
        with pytest.raises(ValueError):
            await service.data("press:57987", limit=limit)
