"""Reproduce the issue #106 audit from the official bytes listed in ADR 0007."""

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import shapely
import xlrd
from shapely.geometry import shape
from shapely.validation import explain_validity

CASE = str.maketrans({"İ": "i", "I": "ı"})
PROVINCE = re.compile(r"TR[0-9A-C][0-9]{2}")
FEATURES = ("nuts2.json", "nuts3.json")
SERIES = (
    "population.json",
    "population-l2.json",
    "housing.json",
    "unemployment-l3.json",
    "unemployment-l2.json",
)
WORKBOOKS = ("population.xls", "labour.xls")
FILES = ("sideMenu.json", "ibbs.json", *FEATURES, *SERIES, *WORKBOOKS)


def label(value):
    return unicodedata.normalize("NFC", value).strip().translate(CASE).casefold()


def index(rows, key):
    indexed = {}
    for row in rows:
        item = key(row)
        if item in indexed:
            raise ValueError(f"Duplicate source key: {item}")
        indexed[item] = row
    return indexed


def difference(expected, observed):
    return {"missing": sorted(expected.keys() - observed), "unmatched": sorted(observed - expected)}


def geography(features, code_key):
    return index(features["features"], lambda item: item["properties"][code_key])


def observations(document):
    periods = document["tarihler"]
    if len(periods) != len(set(periods)):
        raise ValueError("Duplicate periods")
    indexed = index(document["veriler"], lambda row: row["duzeyKodu"])
    return {code: dict(zip(periods, row["veri"], strict=True)) for code, row in indexed.items()}


def workbook_rows(workbook, test):
    sheet = xlrd.open_workbook(file_contents=workbook).sheet_by_index(0)
    return sheet, index(
        (row for row in sheet.get_rows() if test(row)),
        lambda row: row[0].value,
    )


def audit(root):
    bodies = {name: (root / name).read_bytes() for name in FILES}
    docs = {name: json.loads(body) for name, body in bodies.items() if name.endswith(".json")}
    classes = docs["ibbs.json"]
    level2 = index((row for row in classes if row["duzey"] == 3), lambda row: row["kod"])
    level3 = index((row for row in classes if row["duzey"] == 4), lambda row: row["kod"])
    assert all(PROVINCE.fullmatch(code) for code in level3)
    feature2 = geography(docs["nuts2.json"], "duzeyKodu")
    feature3 = geography(docs["nuts3.json"], "nutsKodu")
    cip_codes = index(feature3.values(), lambda item: item["properties"]["duzeyKodu"])
    official_names = index(level3.values(), lambda row: label(row["tanim"]))
    values = {name: observations(docs[name]) for name in SERIES}
    for name, table in values.items():
        expected = level2 if name.endswith("-l2.json") else cip_codes
        if table.keys() != expected.keys():
            raise ValueError(f"{name}: {difference(expected, table.keys())}")
    population = values["population.json"]
    housing = values["housing.json"]
    unemployment = values["unemployment-l3.json"]
    pop_sheet, pop_rows = workbook_rows(
        bodies["population.xls"],
        lambda row: isinstance(row[1].value, float) and row[0].value != "Toplam-Total",
    )
    pop_by_name = index(pop_rows.values(), lambda row: label(row[0].value))
    labour_sheet, labour_rows = workbook_rows(
        bodies["labour.xls"], lambda row: PROVINCE.fullmatch(str(row[0].value))
    )
    assert [int(pop_sheet.cell_value(3, col)) for col in (1, 2)] == [2024, 2025]
    assert "%" in labour_sheet.cell_value(2, 20)
    assert [int(labour_sheet.cell_value(3, col)) for col in (2, 7, 12, 17)] == [
        2022,
        2023,
        2024,
        2025,
    ]

    crosswalk = []
    differences = {
        "population": [],
        "population_regional_totals": [],
        "housing_totals": [],
        "unemployment_rounded": [],
    }
    regions = {}
    invalid = {}
    caution = Counter()
    for code in sorted(level3.keys() & feature3.keys()):
        official = level3[code]
        feature = feature3[code]
        props = feature["properties"]
        cip = props["duzeyKodu"]
        expected = label(official["tanim"])
        actual = label(props["ad"])
        labour_row = labour_rows.get(code)
        crosswalk.append(
            {
                "ibbs3": code,
                "ibbs2": official["ust_kod"],
                "cip_province": cip,
                "official_label": official["tanim"],
                "cip_label": props["ad"],
                "labour_label": labour_row[1].value if labour_row else None,
            }
        )
        assert expected and actual and props["name"] == props["ad"]
        assert props["bolgeKodu"] == official["ust_kod"]
        regions.setdefault(official["ust_kod"], []).append(cip)
        geometry = shape(feature["geometry"])
        if not geometry.is_valid or geometry.is_empty or geometry.geom_type != "MultiPolygon":
            invalid[code] = explain_validity(geometry).partition("[")[0]
        if (pop_row := pop_by_name.get(expected)) is not None:
            for year, column in (("2024", 1), ("2025", 2)):
                observed = int(population[cip][year])
                if observed != pop_row[column].value:
                    differences["population"].append([code, year, observed, pop_row[column].value])
        if labour_row is not None:
            for year, column in (("2022", 2), ("2023", 7), ("2024", 12), ("2025", 17)):
                rate = labour_row[column].value
                assert (
                    0 <= labour_row[column + 2].value <= rate <= labour_row[column + 3].value <= 100
                )
                caution[year] += labour_row[column + 1].value == "(1)"
                rounded = Decimal(str(rate)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
                observed = unemployment[cip][year]
                if Decimal(observed.replace(",", ".")) != rounded:
                    differences["unemployment_rounded"].append([code, year, observed, str(rounded)])

    for code, periods in values["population-l2.json"].items():
        for year, region_value in periods.items():
            provincial_sum = sum(int(population[province][year]) for province in regions[code])
            if provincial_sum != int(region_value):
                differences["population_regional_totals"].append(
                    [code, year, provincial_sum, int(region_value)]
                )

    totals = {
        name: {
            year: sum(int(row[year]) for row in table.values())
            for year in next(iter(table.values()))
        }
        for name, table in (("population", population), ("housing", housing))
    }
    # TÜİK revision report, Table 2, post-revision total, checked on 2026-11-23.
    revised = {"2021": 1609561, "2022": 1625774, "2023": 1330135, "2024": 1549760, "2025": 1760292}
    for year, expected in revised.items():
        if totals["housing"][year] != expected:
            differences["housing_totals"].append([year, totals["housing"][year], expected])

    return {
        "inputs": {
            name: {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
            for name, body in bodies.items()
        },
        "classification_level_counts": Counter(row["duzey"] for row in classes),
        "ibbs2_geometry_join": difference(level2, feature2.keys()),
        "ibbs3_geometry_join": difference(level3, feature3.keys()),
        "adnks_workbook_join": difference(official_names, pop_by_name.keys()),
        "labour_workbook_join": difference(level3, labour_rows.keys()),
        "label_exceptions": {
            "cip": [
                row["ibbs3"]
                for row in crosswalk
                if label(row["official_label"]) != label(row["cip_label"])
            ],
            "labour": [
                row["ibbs3"]
                for row in crosswalk
                if row["labour_label"]
                and label(row["official_label"]) != label(row["labour_label"])
            ],
        },
        "coverage": {
            name: {"regions": len(table), "periods": list(next(iter(table.values())))}
            for name, table in values.items()
        },
        "totals": totals,
        "labour_caution_counts": caution,
        "value_differences": differences,
        "geometry": {
            "crs": docs["nuts3.json"]["crs"]["properties"]["name"],
            "geos": shapely.geos_version_string,
            "invalid": invalid,
            "positions": sum(
                len(ring)
                for item in feature3.values()
                for polygon in item["geometry"]["coordinates"]
                for ring in polygon
            ),
        },
        "crosswalk": crosswalk,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_directory", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.input_directory), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
