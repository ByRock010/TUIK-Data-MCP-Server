"""Normalized observation contract and reviewed recipes (#87)."""

import csv
import dataclasses
import io
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from tuik_mcp.clients.base import HttpClient
from tuik_mcp.config import Settings
from tuik_mcp.models.observation import SCHEMA_VERSION, ObservationResponse
from tuik_mcp.observations import BY_RESOURCE, RECIPES, SchemaChanged, normalize
from tuik_mcp.observations.geography import PROVINCES
from tuik_mcp.observations.recipes import MeasureRule
from tuik_mcp.parsers.errors import ParseError, ParseLimitError
from tuik_mcp.server import respond
from tuik_mcp.services.catalog import CatalogService

FIXTURES = Path(__file__).parent / "fixtures"
FLOW = "flow:DF_ALTINSIZ_DTH+V1.0"
ATTRIBUTES = ["Gözlem Durumu", "Ondalık Basamak Sayısı", "Zaman Formatı", "Birim Çarpanı"]
CPI = BY_RESOURCE["flow:DF_TUFE_SDMX_TT01+V1.0"]
LABOUR = BY_RESOURCE["flow:DF_ISGUCU_AYLIK_TEMEL_ISGUCU_C+V1.0"]
POPULATION = BY_RESOURCE["flow:DF_ADNKS_T24+V1.1"]
TRADE = BY_RESOURCE[FLOW]
EXPORT_PRICES = BY_RESOURCE["flow:DF_ALTINSIZ_IHRACAT_BDE+V1.0"]
ENERGY = BY_RESOURCE["flow:DF_SANAYI_ENERJI_YILLIK_NIHAI_TUKETIM_TJ+V1.0"]


def body(header: list[str], *rows: dict[str, str]) -> bytes:
    lines = [";".join(header)]
    lines += [";".join(row.get(name, "") for name in header) for row in rows]
    return ("﻿" + "\n".join(lines) + "\n").encode()


def labour(*rows):
    header = [
        "Coğrafi Kapsam",
        "Gözlem Sıklığı",
        "İstatistiksel Gösterge",
        "İşgücü Göstergeleri",
        "Temel Yaş",
        "Mevsimsel Düzeltme",
        "Cinsiyet",
        "Zaman",
        "Gözlem Değeri",
        "Ölçü Birimi",
        *ATTRIBUTES,
        "Gizlilik Durumu",
    ]
    base = {
        "Coğrafi Kapsam": "Türkiye",
        "Gözlem Sıklığı": "Aylık",
        "İstatistiksel Gösterge": "Temel İşgücü Göstergeleri",
        "Temel Yaş": "15+",
        "Mevsimsel Düzeltme": "Arındırılmamış",
        "Cinsiyet": "Toplam",
        "Zaman": "2026-09",
    }
    return body(header, *({**base, **row} for row in rows))


def population(*rows):
    header = [
        "Coğrafi Kapsam",
        "Gözlem Sıklığı",
        "İstatistiksel Gösterge",
        "İkamet edilen yer",
        "Yaş Grubu",
        "ADNKS Gösterge",
        "Zaman",
        "Gözlem",
        *ATTRIBUTES,
        "Ölçü Birimi",
    ]
    base = {
        "Coğrafi Kapsam": "Türkiye",
        "Gözlem Sıklığı": "Yıllık",
        "İstatistiksel Gösterge": "Adrese Dayalı Nüfus Kayıt Sistemi",
        "Yaş Grubu": "Uygulanabilir değil",
        "ADNKS Gösterge": "Toplam yaş bağımlılık oranı(%)",
        "Zaman": "2025",
    }
    return body(header, *({**base, **row} for row in rows))


def energy(*rows):
    header = [
        "Gözlem Sıklığı",
        "Coğrafi Kapsam",
        "İstatistiksel Gösterge (E_SSNETI)",
        "Ölçü Birimi (TJ)",
        "Enerji Gösterge (SAN_ENERJI_TJ)",
        "Enerji Kaynak (T)",
        "Bölüm ve gruplar (B07)",
        "NACE Rev. 2, Kısım (B)",
        "Zaman (2024)",
        "Gözlem Değeri",
        "Gözlem Durumu",
        "Gizlilik Durumu",
        "Zaman Serisi Dönüşümü",
        "Ondalık Basamak Sayısı",
        "Zaman Formatı",
        "Birim Çarpanı",
    ]
    base = {
        "Gözlem Sıklığı": "Yıllık",
        "Coğrafi Kapsam": "Türkiye",
        "İstatistiksel Gösterge (E_SSNETI)": "Sanayi Sektörü Nihai Enerji Tüketim İstatistikleri",
        "Ölçü Birimi (TJ)": "Terajul",
        "Enerji Gösterge (SAN_ENERJI_TJ)": next(iter(ENERGY.measures)),
        "Bölüm ve gruplar (B07)": "Toplam",
        "NACE Rev. 2, Kısım (B)": "Toplam",
        "Zaman (2024)": "2024",
        "Gizlilik Durumu": "Yayıma serbest",
    }
    return body(header, *({**base, **row} for row in rows))


def run(recipe, data, **limits):
    return normalize(recipe, data, "f" * 64, **limits)


def statuses(table):
    return [(o.status, o.reason) for o in table.observations]


def test_recipes_are_versioned_reviewed_and_cover_five_initial_domains():
    assert len({recipe.id for recipe in RECIPES}) == len(RECIPES)
    assert {recipe.domain for recipe in RECIPES} == {
        "prices",
        "labour",
        "population",
        "foreign_trade",
        "industry",
    }
    for recipe in RECIPES:
        assert recipe.version >= 1 and recipe.resource_ids and recipe.measures
        assert set(recipe.example) <= set(recipe.concepts())
    assert len(PROVINCES) == 81 and len(set(PROVINCES.values())) == 81


def test_percent_persons_and_seasonal_adjustment_stay_distinct():
    table = run(
        LABOUR,
        labour(
            {"İşgücü Göstergeleri": "İşsizlik Oranı (%)", "Gözlem Değeri": "8,4"},
            {"İşgücü Göstergeleri": "İstihdam (Bin Kişi)", "Gözlem Değeri": "32199,999999999996"},
            {
                "İşgücü Göstergeleri": "İşsizlik Oranı (%)",
                "Mevsimsel Düzeltme": "Mevsim etkilerinden arındırılmış",
                "Gözlem Değeri": "8,6",
            },
        ),
    )
    rate, employed, adjusted = table.observations
    assert [o.status for o in table.observations] == ["valid"] * 3
    assert (rate.unit.kind, rate.unit.multiplier, rate.measure.type) == ("percent", 0, "rate")
    assert (employed.unit.kind, employed.unit.multiplier) == ("persons", 3)
    assert employed.value == Decimal("32199.999999999996")
    assert employed.value_text == "32199,999999999996"
    assert (rate.adjustment.seasonal, adjusted.adjustment.seasonal) == ("not_adjusted", "adjusted")
    assert rate.adjustment.source == "source" and rate.adjustment.calendar == "unknown"
    assert rate.dimensions == {}  # Constant dimensions are metadata only.
    assert table.constant["Cinsiyet"] == "Toplam"
    assert table.varying["Mevsimsel Düzeltme"] == [
        "Arındırılmamış",
        "Mevsim etkilerinden arındırılmış",
    ]
    assert [o.locator.record for o in table.observations] == [2, 3, 4]


def test_percentage_points_need_an_explicit_mapping_never_a_label_pattern():
    recipe = dataclasses.replace(
        LABOUR,
        measures={
            **LABOUR.measures,
            "İşsizlik Oranı Değişimi (yüzde puan)": MeasureRule("change", "percentage_points"),
        },
    )
    table = run(
        recipe,
        labour(
            {"İşgücü Göstergeleri": "İşsizlik Oranı (%)", "Gözlem Değeri": "8,4"},
            {
                "İşgücü Göstergeleri": "İşsizlik Oranı Değişimi (yüzde puan)",
                "Gözlem Değeri": "-0,2",
            },
        ),
    )
    assert [o.unit.kind for o in table.observations] == ["percent", "percentage_points"]
    unknown = run(
        LABOUR,
        labour({"İşgücü Göstergeleri": "Yeni Oran (%)", "Gözlem Değeri": "1"}),
    )
    assert statuses(unknown) == [("unmapped", "measure 'Yeni Oran (%)' has no reviewed mapping")]
    assert unknown.observations[0].value is None and unknown.observations[0].unit is None


def test_month_quarter_and_year_periods_must_match_the_published_frequency():
    table = run(
        LABOUR,
        labour(
            {"İşgücü Göstergeleri": "İşsizlik Oranı (%)", "Gözlem Değeri": "8", "Zaman": "2026"},
            {
                "İşgücü Göstergeleri": "İstihdam (Bin Kişi)",
                "Gözlem Değeri": "8",
                "Gözlem Sıklığı": "Yıllık",
                "Zaman": "2026-03",
            },
            {
                "İşgücü Göstergeleri": "İşgücü (Bin Kişi)",
                "Gözlem Değeri": "8",
                "Gözlem Sıklığı": "Çeyreklik",
                "Zaman": "2025-Q4",
            },
            {
                "İşgücü Göstergeleri": "İşsiz (Bin Kişi)",
                "Gözlem Değeri": "8",
                "Gözlem Sıklığı": "Haftalık",
            },
        ),
    )
    assert statuses(table) == [
        ("invalid", "period '2026' is not a month period"),
        ("invalid", "period '2026-03' is not a year period"),
        ("valid", None),
        ("unmapped", "unknown frequency 'Haftalık'"),
    ]
    assert table.observations[2].period.frequency == "quarter"
    assert table.observations[0].value is None


def test_national_and_province_geography_come_from_reviewed_labels():
    table = run(
        POPULATION,
        population(
            {"İkamet edilen yer": "Toplam", "Gözlem": "45,97"},
            {"İkamet edilen yer": "Ankara", "Gözlem": "40,46"},
            {"İkamet edilen yer": "Ankara Merkez", "Gözlem": "39"},
        ),
    )
    national, province, unknown = table.observations
    assert (national.geography.code, national.geography.level) == ("TR", "national")
    assert (province.geography.code, province.geography.level) == ("06", "province")
    assert province.geography.source == "recipe"
    assert unknown.status == "unmapped" and unknown.geography is None
    assert (province.unit.kind, province.measure.type) == ("percent", "ratio")
    assert province.locator.column == "Gözlem"
    assert table.constant["Coğrafi Kapsam"] == "Türkiye"


def test_markers_confidential_cells_and_bad_numbers_never_become_values():
    table = run(
        ENERGY,
        energy(
            {"Enerji Kaynak (T)": "A", "Gözlem Değeri": "", "Gizlilik Durumu": "Gizli veri"},
            {"Enerji Kaynak (T)": "B", "Gözlem Değeri": ""},
            {"Enerji Kaynak (T)": "C", "Gözlem Değeri": "-"},
            {"Enerji Kaynak (T)": "D", "Gözlem Değeri": "1.234,5"},
            {"Enerji Kaynak (T)": "E", "Gözlem Değeri": "0"},
            {"Enerji Kaynak (T)": "F", "Gözlem Değeri": "5", "Gizlilik Durumu": "Gizli veri"},
            {"Enerji Kaynak (T)": "G", "Gözlem Değeri": "5", "Gözlem Durumu": "Geçici"},
            {"Enerji Kaynak (T)": "H", "Gözlem Değeri": "5", "Birim Çarpanı": "3"},
        ),
    )
    assert statuses(table) == [
        ("confidential", "published as confidential"),
        ("missing", "empty in the source"),
        ("missing", "source marker '-'"),
        ("invalid", "'1.234,5' is not a number in the recipe's notation"),
        ("valid", None),
        ("invalid", "value published with a confidentiality flag"),
        ("unmapped", "observation status 'Geçici' is not reviewed"),
        ("unmapped", "Birim Çarpanı is '3'; the recipe expects none"),
    ]
    values = [o.value for o in table.observations]
    assert values == [None, None, None, None, Decimal("0"), None, None, None]
    assert table.observations[0].source_status == {"Gizlilik Durumu": "Gizli veri"}
    assert table.observations[4].unit.kind == "terajoule"
    assert table.columns["NACE Rev. 2, Kısım"] == "NACE Rev. 2, Kısım (B)"


def test_duplicate_periods_are_refused_not_chosen():
    table = run(
        LABOUR,
        labour(
            {"İşgücü Göstergeleri": "İşsizlik Oranı (%)", "Gözlem Değeri": "8,4"},
            {"İşgücü Göstergeleri": "İşsizlik Oranı (%)", "Gözlem Değeri": "8,5"},
            {
                "İşgücü Göstergeleri": "İşsizlik Oranı (%)",
                "Gözlem Değeri": "8,6",
                "Zaman": "2026-08",
            },
        ),
    )
    assert [o.status for o in table.observations] == ["duplicate", "duplicate", "valid"]
    assert table.observations[0].reason == "same period and dimensions in records 2, 3"
    assert table.observations[0].value is None
    # Yaş Grubu changes label between years but does not separate observations.
    ratios = run(
        POPULATION,
        population(
            {"İkamet edilen yer": "Ankara", "Gözlem": "40", "Yaş Grubu": "Toplam"},
            {"İkamet edilen yer": "Ankara", "Gözlem": "41"},
            {"İkamet edilen yer": "Ankara", "Gözlem": "39", "Zaman": "2024"},
        ),
    )
    assert [o.status for o in ratios.observations] == ["duplicate", "duplicate", "valid"]


def test_cpi_change_rates_carry_their_basis_and_index_its_base():
    header = [
        "Coğrafi Kapsam (TR)",
        "Gözlem Sıklığı (M)",
        "Sınıflama Düzeyi (TUFE)",
        "Endeks-Değişim (1)",
        "Temel Dönem/Yıl (2025)",
        "COICOP 2018 (Amaca Göre Bireysel Tüketim Sınıflaması, 2018) (0)",
        "Zaman (2005-01)",
        "Gözlem Değeri",
        "Ölçü Birimi",
        *ATTRIBUTES,
        "Gizlilik Durumu",
    ]
    base = {
        "Coğrafi Kapsam (TR)": "Türkiye",
        "Gözlem Sıklığı (M)": "Aylık",
        "Sınıflama Düzeyi (TUFE)": "TÜFE",
        "Temel Dönem/Yıl (2025)": "2025",
        "COICOP 2018 (Amaca Göre Bireysel Tüketim Sınıflaması, 2018) (0)": "TÜFE",
        "Zaman (2005-01)": "2026-09",
        "Gözlem Değeri": "1",
    }
    labels = ["Endeks", *list(CPI.measures)[1:]]
    data = body(header, *({**base, "Endeks-Değişim (1)": label} for label in labels))
    table = run(CPI, data)
    index, *changes = table.observations
    assert (index.measure.type, index.unit.kind, index.unit.base_period) == (
        "index",
        "index",
        "2025",
    )
    assert [o.measure.basis for o in changes] == [
        "previous_period",
        "previous_december",
        "same_period_previous_year",
        "twelve_month_average",
    ]
    assert {(o.unit.kind, o.unit.base_period) for o in changes} == {("percent", None)}
    assert index.adjustment.seasonal == "not_adjusted" and index.adjustment.source == "recipe"


def test_export_unit_values_are_an_index_with_a_currency_basis():
    header = [
        "Gözlem Sıklığı",
        "Ölçü Birimi",
        "Dış Ticaret Göstergeleri",
        "Dış Ticaret Türü",
        "İstatistiksel Gösterge",
        "Endeks-Değişim",
        "İktisadi Faaliyet",
        "Dış Ticaret Endeks Sektör Kodları",
        "Temel Dönem/Yıl",
        "Coğrafi Kapsam",
        "Zaman",
        "Gözlem Değeri",
        *ATTRIBUTES,
        "Gizlilik Durumu",
    ]
    row = {
        "Gözlem Sıklığı": "Yıllık",
        "Ölçü Birimi": "ABD doları",
        "Endeks-Değişim": "Endeks",
        "Temel Dönem/Yıl": "2015",
        "Coğrafi Kapsam": "Türkiye",
        "Zaman": "2025",
        "Gözlem Değeri": "104,96",
    }
    (observation,) = run(EXPORT_PRICES, body(header, row)).observations
    assert observation.status == "valid"
    assert (observation.unit.kind, observation.unit.currency) == ("index", "USD")
    assert observation.unit.label == "ABD doları" and observation.unit.base_period == "2015"
    missing_base = run(EXPORT_PRICES, body(header, {**row, "Temel Dönem/Yıl": ""}))
    assert statuses(missing_base) == [("unmapped", "index without a published base period")]


def edited(transform) -> bytes:
    """The real DTH fixture with each record (header included) passed through transform."""
    text = (FIXTURES / "data.csv").read_bytes().decode("utf-8-sig")
    records = list(csv.reader(io.StringIO(text), delimiter=";"))
    return ("\n".join(";".join(transform(record)) for record in records) + "\n").encode()


def test_changed_column_sets_are_refused():
    with pytest.raises(SchemaChanged) as extra:
        run(TRADE, edited(lambda r: [*r, "Yeni Boyut" if r[0].startswith("Gözlem") else "x"]))
    assert extra.value.issues == ["unexpected column 'Yeni Boyut'"]
    with pytest.raises(SchemaChanged) as missing:
        run(TRADE, edited(lambda r: r[:8] + r[9:]))
    assert missing.value.issues == ["missing column 'Coğrafi Kapsam'"]
    with pytest.raises(SchemaChanged) as twice:
        run(TRADE, edited(lambda r: [*r, "Zaman Formatı (X)" if r[0].startswith("Gözlem") else ""]))
    assert "both 'Zaman Formatı'" in twice.value.issues[0]


def test_incomplete_or_malformed_files_return_no_partial_observations():
    data = labour(
        *(
            {
                "İşgücü Göstergeleri": "İşsizlik Oranı (%)",
                "Zaman": f"2026-0{m}",
                "Gözlem Değeri": "1",
            }
            for m in range(1, 4)
        )
    )
    assert len(run(LABOUR, data, max_rows=3).observations) == 3
    with pytest.raises(ParseLimitError):
        run(LABOUR, data, max_rows=2)
    first, rest = data.split(b"\n", 1)
    with pytest.raises(ParseError, match="record 2 has 0 fields"):
        run(LABOUR, first + b"\n\n" + rest)  # A blank record would shift lineage.
    with pytest.raises(ParseError, match="record 5 has 2 fields"):
        run(LABOUR, data + b"x;y\n")


def http_catalog(data: bytes, **settings):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/tr/dataflows":
            return httpx.Response(200, content=(FIXTURES / "flows.json").read_bytes())
        return httpx.Response(200, content=data, headers={"content-type": "text/csv"})

    http = HttpClient(
        Settings(min_interval_seconds=0, download_interval_seconds=0, attempts=1, **settings),
        httpx.MockTransport(handler),
    )
    return http, CatalogService(http), calls


async def test_service_reads_the_complete_snapshot_with_lineage_and_paging():
    http, catalog, _ = http_catalog((FIXTURES / "data.csv").read_bytes())
    try:
        first = await catalog.observations(FLOW, limit=2)
        assert isinstance(first, ObservationResponse)
        assert first.status == "ok" and first.complete and first.schema_version == SCHEMA_VERSION
        assert first.recipe.id == "trade-terms-gold-excluded" and first.recipe.version == 1
        assert first.counts.source_records == first.counts.selected == first.counts.valid == 3
        assert [o.locator.record for o in first.observations] == [2, 3]
        observation = first.observations[0]
        assert observation.value_text == "98,4622068329301"
        assert observation.value == Decimal("98.4622068329301")
        assert observation.id == "obs:" + first.snapshot_id[7:23] + ":2"
        assert (observation.unit.kind, observation.unit.label) == ("index", "Oran")
        assert observation.unit.base_period == "2015"
        assert observation.period.value == "2015-01" and observation.period.source == "source"
        assert first.constant_dimensions["Ölçü Birimi"] == "Oran"
        assert first.has_more and first.next_offset == 2
        second = await catalog.observations(FLOW, offset=2, limit=2, snapshot_id=first.snapshot_id)
        assert [o.locator.record for o in second.observations] == [4]
        assert not second.has_more and second.next_offset is None
        ranged = await catalog.observations(FLOW, period_from="2015-02", period_to="2015-03")
        assert [o.period.value for o in ranged.observations] == ["2015-02", "2015-03"]
        assert ranged.selection == {"period_from": "2015-02", "period_to": "2015-03"}
        assert ranged.counts.selected == 2 and ranged.counts.source_records == 3
        changed = await respond(catalog.observations(FLOW, snapshot_id="sha256:" + "0" * 64))
        assert changed["code"] == "snapshot_changed"
        unknown = await respond(catalog.observations(FLOW, filters={"Zaman": "2015-01"}))
        assert unknown["code"] == "invalid_input" and "Gözlem Sıklığı" in unknown["message"]
        absent = await respond(catalog.observations(FLOW, filters={"Gözlem Sıklığı": "Yıllık"}))
        assert absent["code"] == "invalid_input" and "'Aylık'" in absent["message"]
        mixed = await respond(catalog.observations(FLOW, period_from="2015", period_to="2015-03"))
        assert mixed["code"] == "invalid_input"
        wrong = await respond(catalog.observations(FLOW, period_from="2015"))
        assert wrong["code"] == "invalid_input" and "['month']" in wrong["message"]
    finally:
        await http.close()


async def test_output_budget_shortens_pages_and_keeps_exact_counts():
    rows = [
        {"İşgücü Göstergeleri": "İşsizlik Oranı (%)", "Zaman": f"20{y:02}-01", "Gözlem Değeri": "1"}
        for y in range(10, 40)
    ]
    http, catalog, _ = http_catalog(labour(*rows), query_max_output_bytes=8 * 1024)
    # The fixture catalog lists only the trade flow; serve its record for labour.
    original = catalog.resource

    async def resource(resource_id):
        return (await original(FLOW)).model_copy(update={"id": resource_id})

    catalog.resource = resource
    try:
        result = await catalog.observations(LABOUR.resource_ids[0], limit=200)
        assert 0 < len(result.observations) < 30 and result.has_more
        assert result.counts.selected == result.counts.valid == 30
    finally:
        await http.close()


async def test_schema_changes_unsupported_and_held_resources_return_no_values():
    changed = edited(lambda r: [*r, "Yeni" if r[0].startswith("Gözlem") else "x"])
    http, catalog, calls = http_catalog(changed)
    try:
        result = await respond(catalog.observations(FLOW))
        assert result["status"] == "schema_changed" and result["observations"] == []
        assert result["issues"] == ["unexpected column 'Yeni'"]
        calls.clear()
        other = await respond(catalog.observations("flow:DF_TUFE_SDMX_TT05+V1.0"))
        assert other["status"] == "unsupported"
        version = await respond(catalog.observations("flow:DF_ADNKS_T24+V1.2"))
        assert "flow:DF_ADNKS_T24+V1.1" in version["message"]
        held = await respond(catalog.observations("flow:DF_YABANCILARA_SATIS_V3+V1.0"))
        assert held["status"] == "unavailable" and "housing_sales" in held["message"]
        assert calls == []  # Unreviewed families are refused before any download.
        listing = await respond(catalog.observations())
        assert listing["status"] == "catalog" and len(listing["recipes"]) == len(RECIPES)
        assert listing["unavailable"][0]["domain"] == "housing_sales"
        orphan = await respond(catalog.observations(None, offset=3))
        assert orphan["code"] == "invalid_input"
    finally:
        await http.close()


def test_a_comma_separated_export_fails_the_column_check():
    data = (FIXTURES / "data.csv").read_bytes().replace(b";", b",")
    with pytest.raises(SchemaChanged) as changed:
        run(TRADE, data)
    assert changed.value.issues[0].startswith("unexpected column")
