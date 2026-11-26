"""Hand-calculated fixtures for auditable series calculations (#89)."""

import csv
import io
import json
from decimal import ROUND_DOWN, localcontext

import pytest
from test_observations import CPI, LABOUR, body, labour, labour_catalog

from tuik_mcp.calculations import recompute
from tuik_mcp.models.calculation import SCHEMA_VERSION, CalculationResponse
from tuik_mcp.server import respond
from tuik_mcp.utils.rendering import rendered

RESOURCE = LABOUR.resource_ids[0]
RATE = "İşsizlik Oranı (%)"
UNEMPLOYED = "İşsiz (Bin Kişi)"


def series(indicator: str, values: dict[str, str], **fixed) -> list[dict[str, str]]:
    return [
        {"İşgücü Göstergeleri": indicator, "Zaman": period, "Gözlem Değeri": value, **fixed}
        for period, value in values.items()
    ]


async def calculate(data: bytes, operation: str, tmp_path=None, **specification):
    settings = {"artifact_dir": str(tmp_path)} if tmp_path is not None else {}
    settings |= specification.pop("settings", {})
    http, catalog = labour_catalog(data, **settings)
    try:
        return await respond(catalog.calculate(RESOURCE, operation, **specification))
    finally:
        await http.close()


def results(payload, index=0):
    return [
        (r["period"], r["status"], r["value"], r["reason"])
        for r in payload["series"][index]["results"]
    ]


def ids_by_period(payload, index=0):
    return {item["period"]: item["id"] for item in payload["series"][index]["inputs"]}


# Rows are shuffled on purpose: periods are matched by calendar position.
RATES = series(
    RATE,
    {
        "2026-03": "8,0",
        "2025-01": "9,0",
        "2026-01": "8,4",
        "2025-12": "8,2",
        "2025-03": "8,6",
        "2025-02": "8,8",
        "2024-02": "9,9",
        "2023-02": "10,1",
    },
)


async def test_year_ago_and_previous_period_follow_the_calendar_not_row_order():
    data = labour(*RATES)
    year_ago = await calculate(
        data,
        "pp_difference",
        basis="same_period_previous_year",
        period_from="2026-01",
        period_to="2026-03",
    )
    assert year_ago["schema_version"] == SCHEMA_VERSION and year_ago["status"] == "ok"
    assert results(year_ago) == [
        ("2026-01", "valid", "-0.6", None),
        ("2026-02", "missing", None, "no source record for 2026-02"),
        ("2026-03", "valid", "-0.6", None),
    ]
    ids = ids_by_period(year_ago)
    assert year_ago["series"][0]["results"][0]["inputs"] == [ids["2026-01"], ids["2025-01"]]
    assert year_ago["output_unit"] == {
        "kind": "percentage_points",
        "multiplier": 0,
        "currency": None,
        "base_period": None,
    }
    assert year_ago["output_measure"] == {"type": "change", "basis": "same_period_previous_year"}
    assert "r = t − 12 (the same month of the previous year)" in year_ago["formula"]
    assert year_ago["counts"] == {
        "series": 1,
        "results": 3,
        "valid": 2,
        "missing": 1,
        "undefined": 0,
    }
    assert not year_ago["coverage"]["complete"]
    assert year_ago["coverage"]["gaps"][0]["absent"] == ["2026-02"]

    month = await calculate(
        data, "pp_difference", basis="previous_period", period_from="2026-01", period_to="2026-03"
    )
    # January reaches back across the year boundary to December.
    assert results(month) == [
        ("2026-01", "valid", "0.2", None),
        ("2026-02", "missing", None, "no source record for 2026-02"),
        ("2026-03", "missing", None, "no source record for 2026-02"),
    ]
    # February of a leap year is compared with February, one calendar year apart.
    leap = await calculate(
        data,
        "pp_difference",
        basis="same_period_previous_year",
        periods=None,
        period_from="2024-02",
        period_to="2024-02",
    )
    assert results(leap) == [("2024-02", "valid", "-0.2", None)]
    pair = await calculate(data, "pp_difference", basis="periods", periods=["2023-02", "2026-03"])
    assert results(pair) == [("2026-03", "valid", "-2.1", None)]
    assert pair["output_measure"]["basis"] == "periods"


async def test_quarter_and_year_boundaries_use_their_own_calendar():
    quarterly = labour(
        *series(
            RATE,
            {"2025-Q1": "9,5", "2025-Q4": "8,9", "2026-Q1": "8,6"},
            **{"Gözlem Sıklığı": "Çeyreklik"},
        )
    )
    previous = await calculate(
        quarterly, "pp_difference", basis="previous_period", period_from="2026-Q1"
    )
    assert results(previous) == [("2026-Q1", "valid", "-0.3", None)]
    year_ago = await calculate(
        quarterly, "pp_difference", basis="same_period_previous_year", period_from="2026-Q1"
    )
    assert results(year_ago) == [("2026-Q1", "valid", "-0.9", None)]
    assert "r = t − 4 (the same quarter of the previous year)" in year_ago["formula"]
    yearly = labour(*series(RATE, {"2024": "9,4", "2025": "8,8"}, **{"Gözlem Sıklığı": "Yıllık"}))
    annual = await calculate(yearly, "pp_difference", basis="same_period_previous_year")
    assert results(annual) == [
        ("2024", "missing", None, "no source record for 2023"),
        ("2025", "valid", "-0.6", None),
    ]


async def test_percent_change_and_difference_on_levels_with_exact_rounding():
    data = labour(
        *series(
            UNEMPLOYED,
            {
                "2025-03": "3200",
                "2026-03": "3000",
                "2025-04": "0",
                "2026-04": "10",
                "2025-05": "3",
                "2026-05": "4",
                "2025-06": "-5",
                "2026-06": "5",
            },
        )
    )
    change = await calculate(
        data,
        "percent_change",
        basis="same_period_previous_year",
        period_from="2026-03",
        period_to="2026-06",
        decimals=1,
    )
    rows = change["series"][0]["results"]
    # (3000 − 3200) × 100 ÷ 3200 = −6.25, shown half-to-even as −6.2.
    assert (rows[0]["value"], rows[0]["rounded"]) == ("-6.25", "-6.2")
    assert (rows[1]["status"], rows[1]["reason"]) == (
        "undefined",
        "reference value is zero; percent change is undefined",
    )
    # 100 ÷ 3 is rounded to ten places before display.
    assert (rows[2]["value"], rows[2]["rounded"]) == ("33.3333333333", "33.3")
    assert rows[3]["status"] == "undefined" and "negative" in rows[3]["reason"]
    assert change["input_unit"] == {
        "kind": "persons",
        "multiplier": 3,
        "currency": None,
        "base_period": None,
    }
    assert change["output_unit"]["kind"] == "percent"
    assert change["rounding"] == {"value_places": 10, "decimals": 1, "mode": "ROUND_HALF_EVEN"}

    difference = await calculate(
        data,
        "difference",
        basis="same_period_previous_year",
        period_from="2026-03",
        period_to="2026-03",
    )
    assert results(difference) == [("2026-03", "valid", "-200", None)]
    # The difference stays in thousands of persons; it is never rescaled.
    assert difference["output_unit"]["kind"] == "persons"
    assert difference["output_unit"]["multiplier"] == 3


async def test_moving_average_needs_every_period_in_its_window():
    data = labour(
        *series(RATE, {"2026-01": "8,0", "2026-02": "8,3", "2026-03": "8,1", "2026-05": "8,6"})
    )
    average = await calculate(
        data, "moving_average", window=3, period_from="2026-03", period_to="2026-05"
    )
    assert results(average) == [
        ("2026-03", "valid", "8.1333333333", None),
        ("2026-04", "missing", None, "window: no source record for 2026-04"),
        ("2026-05", "missing", None, "window: no source record for 2026-04"),
    ]
    assert len(average["series"][0]["results"][0]["inputs"]) == 3
    assert any("describes those periods only" in issue for issue in average["issues"])


async def test_extrema_report_every_tie_and_label_partial_coverage():
    data = labour(
        *series(
            RATE,
            {
                "2026-01": "8,4",
                "2026-02": "8,0",
                "2026-03": "8,40",
                "2026-04": "8,00",
                "2026-05": "8,2",
            },
        )
    )
    full = await calculate(data, "extrema")
    extrema = {e["kind"]: e for e in full["series"][0]["extrema"]}
    assert (extrema["max"]["value"], extrema["max"]["periods"]) == ("8.4", ["2026-01", "2026-03"])
    assert (extrema["min"]["value"], extrema["min"]["periods"]) == ("8", ["2026-02", "2026-04"])
    assert extrema["min"]["rounded"] == "8.00" and len(extrema["min"]["inputs"]) == 2
    assert full["coverage"]["complete"] and full["coverage"]["bounds"] == "series_extent"
    assert full["issues"] == []

    partial = await calculate(data, "extrema", period_from="2026-04", period_to="2026-07")
    assert {e["kind"]: e["periods"] for e in partial["series"][0]["extrema"]} == {
        "min": ["2026-04"],
        "max": ["2026-05"],
    }
    assert not partial["coverage"]["complete"]
    assert any(
        "Only 2 of 4 series periods" in issue and "not the whole range" in issue
        for issue in partial["issues"]
    )


async def test_rebase_uses_one_declared_period_and_refuses_a_missing_or_zero_base():
    yearly = {"Gözlem Sıklığı": "Yıllık"}
    women = series(UNEMPLOYED, {"2025": "2000", "2026": "2100"}, Cinsiyet="Kadın", **yearly)
    men = series(UNEMPLOYED, {"2025": "4000", "2026": "3900"}, Cinsiyet="Erkek", **yearly)
    data = labour(*women, *men)
    rebased = await calculate(
        data, "rebase", filters={"İşgücü Göstergeleri": UNEMPLOYED}, base_period="2025"
    )
    by_sex = {item["dimensions"]["Cinsiyet"]: item for item in rebased["series"]}
    assert [r["value"] for r in by_sex["Kadın"]["results"]] == ["100", "105"]
    assert [r["value"] for r in by_sex["Erkek"]["results"]] == ["100", "97.5"]
    assert rebased["output_unit"]["kind"] == "index"
    assert rebased["output_unit"]["base_period"] == "2025"
    # The original official values stay beside the derived index.
    assert [i["value"] for i in by_sex["Erkek"]["inputs"]] == ["4000", "3900"]

    absent = await calculate(
        data, "rebase", filters={"İşgücü Göstergeleri": UNEMPLOYED}, base_period="2024"
    )
    assert absent["code"] == "invalid_input" and "no source record for 2024" in absent["message"]
    zero = labour(*series(UNEMPLOYED, {"2025-01": "0", "2026-01": "5"}))
    refused = await calculate(zero, "rebase", base_period="2025-01")
    assert refused["code"] == "invalid_input" and "2025-01 is zero" in refused["message"]


async def test_incompatible_measures_series_and_adjustments_are_refused():
    rates = labour(*RATES)
    levels = labour(*series(UNEMPLOYED, {"2025-01": "3000", "2026-01": "3100"}))
    cases = [
        (rates, "difference", {"basis": "previous_period"}, "use pp_difference"),
        (rates, "percent_change", {"basis": "previous_period"}, "use pp_difference"),
        (levels, "pp_difference", {"basis": "previous_period"}, "needs percent values"),
    ]
    for data, operation, extra, message in cases:
        refused = await calculate(data, operation, **extra)
        assert refused["code"] == "invalid_input" and message in refused["message"], operation

    both = labour(
        *series(RATE, {"2026-01": "8,4"}, Cinsiyet="Kadın"),
        *series(RATE, {"2026-01": "8,0"}, Cinsiyet="Erkek"),
    )
    several = await calculate(both, "extrema")
    assert several["code"] == "invalid_input" and "2 series" in several["message"]
    assert "'Cinsiyet': ['Erkek', 'Kadın']" in several["message"]

    adjusted = labour(
        *series(UNEMPLOYED, {"2025-01": "3000"}),
        *series(
            UNEMPLOYED,
            {"2025-01": "3050"},
            **{"Mevsimsel Düzeltme": "Mevsim etkilerinden arındırılmış"},
        ),
    )
    mixed = await calculate(adjusted, "rebase", base_period="2025-01")
    assert mixed["code"] == "invalid_input" and "mixes adjustment" in mixed["message"]

    frequencies = labour(
        *series(RATE, {"2026-01": "8,4"}),
        *series(RATE, {"2026-Q1": "8,2"}, **{"Gözlem Sıklığı": "Çeyreklik"}),
    )
    resampled = await calculate(frequencies, "extrema")
    assert resampled["code"] == "invalid_input" and "never resampled" in resampled["message"]


async def test_duplicates_and_unusable_inputs_never_enter_a_formula():
    data = labour(
        *series(RATE, {"2025-01": "9,0", "2026-01": "8,4", "2026-02": "-", "2025-02": "8,9"}),
        *series(RATE, {"2025-01": "9,1"}),
    )
    result = await calculate(
        data, "pp_difference", basis="same_period_previous_year", period_from="2026-01"
    )
    assert results(result) == [
        ("2026-01", "missing", None, "2025-01 has duplicate source records"),
        ("2026-02", "missing", None, "2026-02 is missing"),
    ]
    duplicates = [i for i in result["series"][0]["inputs"] if i["period"] == "2025-01"]
    assert [i["status"] for i in duplicates] == ["duplicate", "duplicate"]
    assert all(i["value"] is None for i in duplicates)


@pytest.mark.parametrize(
    ("operation", "arguments", "message"),
    [
        ("moving_average", {}, "needs window"),
        ("extrema", {"basis": "previous_period"}, "does not take basis"),
        ("difference", {"basis": "periods"}, "periods=[reference, target]"),
        (
            "pp_difference",
            {"basis": "periods", "periods": ["2026-03", "2026-01"]},
            "must come before",
        ),
        (
            "pp_difference",
            {"basis": "periods", "periods": ["2025-01", "2026-01"], "period_from": "2026-01"},
            "omit period_from",
        ),
        ("rebase", {"base_period": "2025"}, "not a month period"),
        ("extrema", {"period_from": "2026-03", "period_to": "2026-01"}, "after"),
        ("extrema", {"decimals": 11}, "decimals"),
    ],
)
async def test_invalid_specifications_are_input_errors(operation, arguments, message):
    refused = await calculate(labour(*RATES), operation, **arguments)
    assert refused["code"] == "invalid_input" and message in refused["message"]


async def test_held_and_unreviewed_resources_are_refused_before_download():
    http, catalog = labour_catalog(labour(*RATES))
    try:
        held = await respond(catalog.calculate("flow:DF_YABANCILARA_SATIS_V3+V1.0", "extrema"))
        assert held["code"] == "unavailable"
        other = await respond(catalog.calculate("flow:DF_TUFE_SDMX_TT05+V1.0", "extrema"))
        assert other["code"] == "unsupported"
    finally:
        await http.close()


async def test_saved_bundle_recomputes_to_the_same_results(tmp_path):
    response = await calculate(
        labour(*RATES),
        "pp_difference",
        tmp_path,
        basis="same_period_previous_year",
        period_from="2025-01",
        save=True,
    )
    CalculationResponse.model_validate(response)
    artifact = response["artifact"]
    names = {f["name"] for f in artifact["files"]}
    assert {"calculation.json", "results.csv", "inputs.csv", "README.txt", "source.csv"} <= names
    http, catalog = labour_catalog(b"", artifact_dir=str(tmp_path))
    try:
        read = catalog.artifacts.read
        bundle = json.loads(read(artifact["artifact_id"], "calculation.json")[0])
        inputs = list(
            csv.DictReader(
                io.StringIO(read(artifact["artifact_id"], "inputs.csv")[0].decode("utf-8-sig"))
            )
        )
        readme = read(artifact["artifact_id"], "README.txt")[0].decode()
    finally:
        await http.close()
    assert recompute(bundle) == bundle["series"] == response["series"]
    assert bundle["spec"]["basis"] == "same_period_previous_year"
    assert {row["value_text"] for row in inputs} >= {"8,4", "9,0"}
    assert all(row["locator"].startswith("record ") for row in inputs)
    assert "not publish" in readme and "recompute" in readme
    # Changing one input value changes the recomputed result: nothing is cached.
    bundle["series"][0]["inputs"][0]["value"] = "1"
    assert recompute(bundle) != response["series"]


async def test_output_budget_needs_a_narrower_range_or_an_artifact(tmp_path):
    months = {f"{year}-{month:02}": "8,4" for year in range(2005, 2027) for month in range(1, 13)}
    data = labour(*series(RATE, months))
    small = {"query_max_output_bytes": 16 * 1024}
    refused = await calculate(data, "pp_difference", basis="previous_period", settings=small)
    assert refused["code"] == "query_limit_exceeded" and "save=true" in refused["message"]
    saved = await calculate(
        data, "pp_difference", tmp_path, basis="previous_period", save=True, settings=small
    )
    assert saved["status"] == "ok" and not saved["inline_complete"]
    assert saved["series"][0]["results"] == [] and saved["counts"]["results"] == len(months)
    assert saved["artifact"]["artifact_id"]


async def test_index_series_with_different_bases_cannot_share_a_percent_change():
    header = [
        "Coğrafi Kapsam",
        "Gözlem Sıklığı",
        "Sınıflama Düzeyi",
        "Endeks-Değişim",
        "Temel Dönem/Yıl",
        "COICOP 2018 (Amaca Göre Bireysel Tüketim Sınıflaması, 2018)",
        "Zaman",
        "Gözlem Değeri",
        "Ölçü Birimi",
        "Gözlem Durumu",
        "Ondalık Basamak Sayısı",
        "Zaman Formatı",
        "Birim Çarpanı",
        "Gizlilik Durumu",
    ]
    base = {
        "Coğrafi Kapsam": "Türkiye",
        "Gözlem Sıklığı": "Aylık",
        "Sınıflama Düzeyi": "TÜFE",
        "Endeks-Değişim": "Endeks",
        "COICOP 2018 (Amaca Göre Bireysel Tüketim Sınıflaması, 2018)": "TÜFE",
    }
    data = body(
        header,
        {**base, "Temel Dönem/Yıl": "2003", "Zaman": "2025-12", "Gözlem Değeri": "3500"},
        {**base, "Temel Dönem/Yıl": "2025", "Zaman": "2026-01", "Gözlem Değeri": "104,5"},
    )
    http, catalog = labour_catalog(data)
    try:
        # A base-year change is a separate series, never merged into one index.
        cpi = CPI.resource_ids[0]
        refused = await respond(catalog.calculate(cpi, "percent_change", basis="previous_period"))
        assert refused["code"] == "invalid_input" and "Temel Dönem/Yıl" in refused["message"]
        one = await respond(
            catalog.calculate(
                cpi,
                "percent_change",
                basis="previous_period",
                filters={"Temel Dönem/Yıl": "2025"},
            )
        )
        assert results(one) == [("2026-01", "missing", None, "no source record for 2025-12")]
        assert one["input_unit"]["base_period"] == "2025"
    finally:
        await http.close()


@pytest.mark.parametrize(
    ("bound", "period"),
    [({"period_from": "2026-04"}, "2026-04"), ({"period_to": "2025-12"}, "2025-12")],
)
async def test_a_one_sided_bound_beyond_the_series_is_missing_not_complete(bound, period):
    data = labour(*series(RATE, {"2026-01": "8,4", "2026-03": "8,0"}))
    extrema = await calculate(data, "extrema", **bound)
    assert extrema["status"] == "ok"
    assert (extrema["period_from"], extrema["period_to"]) == (period, period)
    coverage = extrema["coverage"]
    assert (coverage["expected"], coverage["valid"], coverage["complete"]) == (1, 0, False)
    assert coverage["gaps"][0]["absent"] == [period]
    assert extrema["series"][0]["extrema"] == []
    assert any("Only 0 of 1 series periods" in issue for issue in extrema["issues"])
    change = await calculate(data, "pp_difference", basis="previous_period", **bound)
    assert [(p, s) for p, s, _, _ in results(change)] == [(period, "missing")]
    assert not change["coverage"]["complete"]


async def test_differences_are_exact_whatever_the_callers_decimal_context(tmp_path):
    tiny = "0," + "0" * 26 + "1"
    data = labour(
        *series(UNEMPLOYED, {"2025": "100", "2026": tiny}, **{"Gözlem Sıklığı": "Yıllık"})
    )
    response = await calculate(
        data, "difference", tmp_path, basis="previous_period", period_from="2026", save=True
    )
    (row,) = response["series"][0]["results"]
    assert row["value"] == "-99.999999999999999999999999999"
    assert row["rounded"] == "-100.00"
    http, catalog = labour_catalog(b"", artifact_dir=str(tmp_path))
    try:
        artifact_id = response["artifact"]["artifact_id"]
        bundle = json.loads(catalog.artifacts.read(artifact_id, "calculation.json")[0])
    finally:
        await http.close()
    with localcontext() as context:
        context.prec = 5
        context.rounding = ROUND_DOWN
        assert recompute(bundle) == bundle["series"] == response["series"]


async def test_saved_extrema_with_many_ties_fit_the_budget_and_keep_every_tie(tmp_path):
    months = {f"{year}-{month:02}": "8,4" for year in range(1935, 2027) for month in range(1, 13)}
    assert len(months) == 1104
    data = labour(*series(RATE, months))
    refused = await calculate(data, "extrema")
    assert refused["code"] == "query_limit_exceeded"
    saved = await calculate(data, "extrema", tmp_path, save=True)
    assert saved["status"] == "ok" and not saved["inline_complete"]
    assert len(rendered(saved)) <= 64 * 1024
    for extremum in saved["series"][0]["extrema"]:
        assert extremum["tie_count"] == 1104 and len(extremum["periods"]) <= 10
        assert len(extremum["inputs"]) == len(extremum["periods"])
    http, catalog = labour_catalog(b"", artifact_dir=str(tmp_path))
    try:
        artifact_id = saved["artifact"]["artifact_id"]
        bundle = json.loads(catalog.artifacts.read(artifact_id, "calculation.json")[0])
    finally:
        await http.close()
    for extremum in bundle["series"][0]["extrema"]:
        assert len(extremum["periods"]) == len(extremum["inputs"]) == 1104
    assert recompute(bundle) == bundle["series"]
