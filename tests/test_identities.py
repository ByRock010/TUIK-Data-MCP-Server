import json
from pathlib import Path

import httpx
import pytest

from tuik_mcp.clients.base import HttpClient
from tuik_mcp.clients.tuik_portal import SearchItem, normalize_result
from tuik_mcp.config import PORTAL, Settings
from tuik_mcp.identities import digest, file_keys, key_parts, search_id, slug
from tuik_mcp.server import respond
from tuik_mcp.services.catalog import CatalogService
from tuik_mcp.utils.normalization import plain_text as plain
from tuik_mcp.utils.normalization import validate_id

FIXTURES = Path(__file__).parent / "fixtures"
XLS = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"test fixture"
DOWNLOAD = f"{PORTAL}/api/tr/data/downloads?t=i&p="


def service(handler):
    http = HttpClient(
        Settings(min_interval_seconds=0, download_interval_seconds=0, attempts=1),
        httpx.MockTransport(handler),
    )
    return http, lambda: CatalogService(http)


@pytest.mark.parametrize(
    "title,expected",
    [
        (
            "Tüketici fiyat endeksi (TÜFE) ve değişim oranları",
            "tüketici-fiyat-endeksi-tüfe-ve-değişim-oranları",
        ),
        ("İŞSİZLİK ORANI", "işsizlik-oranı"),
        ("Kâr payı, 2025", "kar-payı-2025"),
        ("— (*) —", "file"),
    ],
)
def test_slug_keeps_turkish_words(title, expected):
    assert slug(title) == expected


def test_slug_is_bounded_at_a_word_boundary():
    value = slug("Bir önceki yılın aynı ayına göre değişim oranı " * 5)
    assert len(value) <= 80 and not value.endswith("-")
    assert value.startswith("bir-önceki-yılın-aynı-ayına")


def test_case_variants_of_a_title_get_different_ids():
    upper = search_id(2, "Cinsiyete Göre Genç İşsizlik Oranı")
    lower = search_id(2, "Cinsiyete göre genç işsizlik oranı")
    assert upper != lower
    assert upper.split("~")[0] == lower.split("~")[0] == "table:cinsiyete-göre-genç-işsizlik-oranı"
    assert search_id(6, "Yayın").startswith("publication:") and search_id(3, "Metaveri") is None


def test_repeated_titles_get_ordinals_in_source_order():
    keys = file_keys([("Tablo", "xls"), ("Tablo", "xls"), ("Tablo", "pdf"), ("Tablo", "xls")])
    base = f"tablo~{digest('Tablo', 'xls')}"
    assert keys == [base, f"{base}.2", f"tablo~{digest('Tablo', 'pdf')}", f"{base}.3"]
    assert key_parts(keys[3]) == ("tablo", digest("Tablo", "xls"), 3)


@pytest.mark.parametrize(
    "value",
    [
        "table:işsizlik-oranı~0123456789",
        "publication:x~abcdef0123.2",
        "report:a-b-c~abcdef0123.42",
        "press:58298:file:tüfe~0123456789",
    ],
)
def test_stable_ids_validate(value):
    assert validate_id(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "table:Upper~0123456789",
        "table:x~012345678",
        "table:x~0123456789.1",
        "table:x-~0123456789",
        "table:x~0123456789.100",
        "dataset:x~0123456789",
        "press:1:file:../x~0123456789",
        "press:0:file:x~0123456789",
    ],
)
def test_malformed_stable_ids_are_rejected(value):
    with pytest.raises(ValueError):
        validate_id(value)


def test_search_results_get_stable_ids_only_for_downloads():
    def result(type_id, url, icon=None):
        return normalize_result(
            SearchItem(title="İşsizlik Oranı", url=url, type=type_id, icon=icon)
        )

    assert result(2, DOWNLOAD + "a").id == search_id(2, "İşsizlik Oranı")
    assert result(6, DOWNLOAD + "b").id.startswith("publication:")
    assert result(4, DOWNLOAD + "c").id.startswith("report:")
    assert result(1, "/tr/press/57987").id == "press:57987"
    assert result(2, "https://databrowser2.tuik.gov.tr/#/tr/tuik/TR,DF_X,1.0", "dataflow").id == (
        "flow:DF_X+V1.0"
    )
    assert result(3, "https://nip.tuik.gov.tr/").id.startswith("resource:")


async def test_bulletin_files_resolve_in_a_fresh_session():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/tr/press/57987":
            return httpx.Response(200, json=json.loads((FIXTURES / "press.json").read_text()))
        if request.url.path == "/api/tr/dataflows":
            return httpx.Response(200, json=json.loads((FIXTURES / "flows.json").read_text()))
        if request.url.path == "/api/tr/data/downloads":
            return httpx.Response(
                200, content=XLS, headers={"content-type": "application/vnd.ms-excel"}
            )
        raise AssertionError(request.url)

    http, fresh = service(handler)
    try:
        bulletin = await fresh().resource("press:57987")
        ids = [link.resource_id for link in bulletin.download_links]
        assert ids and all(ids) and len(set(ids)) == len(ids)
        link = bulletin.download_links[0]
        resolved = await fresh().resource(link.resource_id)
        assert (resolved.title, resolved.download_links[0].source_url) == (
            link.title,
            link.source_url,
        )
        assert resolved.source_url.endswith("/tr/press/57987")
        data = await fresh().data(link.resource_id)
        assert data.status == "download_available" and data.download_links[0].verified
        missing = link.resource_id.split("~")[0] + "~0000000000"
        error = await respond(fresh().resource(missing))
        assert error["status"] == "error" and error["code"] == "not_found"
    finally:
        await http.close()


async def test_same_title_in_bulletin_tables_and_statistical_tables_is_two_files():
    payload = json.loads((FIXTURES / "press.json").read_text())
    table = next(e for e in payload["data"]["tables"] if e["type"] == "xls")
    statistical = {**table, "url": table["url"].replace("t=t&", "t=i&").replace("p=", "p=x")}
    payload["data"]["statisticalTables"].append(statistical)
    payload["data"]["statisticalTables"].append(dict(statistical, url=statistical["url"] + "y"))

    def handler(request):
        if request.url.path == "/api/tr/press/57987":
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json=json.loads((FIXTURES / "flows.json").read_text()))

    http, fresh = service(handler)
    try:
        links = (await fresh().resource("press:57987")).download_links
        same = [link for link in links if link.title == plain(table["title"])]
        ids = [link.resource_id for link in same]
        # A bulletin table and a statistical table differ; a repeated listing gets ".2".
        assert len(set(ids)) == 3 and ids[2] == ids[1] + ".2" and "." not in ids[1]
        assert (await fresh().resource(ids[2])).download_links[0].source_url.endswith("y")
    finally:
        await http.close()


def search_handler(items, requests):
    def handler(request):
        if request.url.path == "/api/tr/data/downloads":
            return httpx.Response(
                200, content=XLS, headers={"content-type": "application/vnd.ms-excel"}
            )
        body = json.loads(request.content)
        requests.append(body)
        page = [item for item in items if item["type"] in body["typeIds"]]
        start = (body["page"] - 1) * 10
        payload = {"data": page[start : start + 10], "total": len(page)}
        return httpx.Response(200, json={"isError": False, "data": payload})

    return handler


async def test_search_kinds_resolve_in_a_fresh_session():
    items = [
        {"title": "Cinsiyete Göre Genç İşsizlik Oranı", "url": DOWNLOAD + "upper", "type": 2},
        {"title": "Cinsiyete göre genç işsizlik oranı", "url": DOWNLOAD + "lower", "type": 2},
        {"title": "Cinsiyete göre genç işsizlik oranı", "url": DOWNLOAD + "again", "type": 2},
        {"title": "Cinsiyete göre genç işsizlik oranı", "url": DOWNLOAD + "pub", "type": 6},
    ]
    requests = []
    http, fresh = service(search_handler(items, requests))
    try:
        upper = search_id(2, items[0]["title"])
        lower = search_id(2, items[1]["title"])
        assert (await fresh().resource(upper)).source_url.endswith("upper")
        assert (await fresh().resource(lower)).source_url.endswith("lower")
        assert (await fresh().resource(lower + ".2")).source_url.endswith("again")
        assert requests[0]["typeIds"] == [2]
        assert requests[0]["text"] == "cinsiyete göre genç işsizlik oranı"
        data = await fresh().data(lower)
        assert data.status == "download_available" and data.resource_id == lower
        error = await respond(fresh().resource(lower + ".3"))
        assert error["code"] == "not_found" and "/tr/search?" in error["source_url"]
    finally:
        await http.close()


async def test_resolution_is_bounded_to_five_search_pages():
    items = [{"title": f"Başka tablo {n}", "url": DOWNLOAD + str(n), "type": 2} for n in range(80)]
    requests = []
    http, fresh = service(search_handler(items, requests))
    try:
        error = await respond(fresh().resource(search_id(2, "Aranan tablo")))
        assert error["code"] == "not_found" and len(requests) == 5
    finally:
        await http.close()
