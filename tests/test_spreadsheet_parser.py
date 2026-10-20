import io
import json
from datetime import datetime
from pathlib import Path

import pytest
from openpyxl import Workbook

from tuik_mcp.parsers import parse_table
from tuik_mcp.parsers.errors import InvalidFiltersError, ParseError
from tuik_mcp.parsers.spreadsheet import Cell, TableLayout, load_sheets, render

FILES = Path(__file__).parent / "fixtures/files"
GOLDENS = {
    path.name.removesuffix(".golden.json"): json.loads(path.read_text())
    for path in sorted(FILES.glob("*.golden.json"))
}
SPREADSHEETS = [key for key, gold in GOLDENS.items() if gold["format"] in {"xls", "xlsx"}]
YEARS = ["Yıllar - Years [column 1]", "Yıllar - Years [column 12]", "Yıllar - Years [column 23]"]
# Goldens record raw cells; the parser also forward-fills these blank row labels.
FILLED = {"labour-basic": YEARS, "labour-adjusted": YEARS}


def parsed(key, **kwargs):
    gold = GOLDENS[key]
    body = (FILES / f"{key}.{gold['format']}").read_bytes()
    return parse_table(body, gold["format"], **kwargs)


@pytest.mark.parametrize("key", SPREADSHEETS)
def test_parser_matches_golden_observations(key):
    gold = GOLDENS[key]
    body = (FILES / f"{key}.{gold['format']}").read_bytes()
    (table,) = parse_table(body, gold["format"])
    layout = TableLayout(load_sheets(body, gold["format"])[0]).detect()
    assert layout.header_rows == gold["header_rows"]
    assert (table.layout, table.sheet, table.extraction) == ("table", gold["sheet"], gold["format"])
    assert table.title == gold["title"].strip()
    assert (table.title_en or "") == gold["title_en"]
    assert table.unit == gold["unit"]
    source_columns = len(gold["columns"])
    assert table.columns[:source_columns] == gold["columns"]
    assert all(c.startswith("Bölüm / Section") for c in table.columns[source_columns:])
    for index, golden_row in enumerate(gold["first_rows"]):
        expected = dict(golden_row)
        for column in FILLED.get(key, []):
            if index and expected[column] == "":
                expected[column] = gold["first_rows"][index - 1][column]
        assert {column: table.rows[index][column] for column in expected} == expected
    # The parser keeps every footer line; goldens list the source and footnote lines.
    assert set(gold["notes"]) <= set(table.notes)
    if gold["source_note"]:
        assert table.source_note == gold["source_note"]
    if sample := gold.get("forward_fill_sample"):
        row = table.rows[gold["first_row_indices"].index(sample["row"])]
        assert row[gold["columns"][sample["column"]]] == sample["expected"]


@pytest.mark.parametrize(
    "sample",
    [s for key in SPREADSHEETS for s in GOLDENS[key]["numeric_samples"]],
    ids=lambda s: f"{s['number_format']}={s['rendered']}",
)
def test_numbers_render_with_source_precision(sample):
    assert render(Cell(sample["value"], sample["number_format"])) == sample["rendered"]


@pytest.mark.parametrize(
    "value,number_format,expected",
    [
        (2.5, "0", "3"),
        (0.125, "0.00", "0.13"),
        (-0.04, "0.0", "0.0"),
        (1234567.8, "#,##0", "1234568"),
        (0.256, "0.0%", "25.6%"),
        (-1.25, "#,##0.0;[Red]-#,##0.0", "-1.3"),
        (1.0526361271701277, "General", "1.052636127"),
        (2013, "General", "2013"),
        (datetime(2026, 1, 31), "dd.mm.yyyy", "2026-01-31"),
        (True, "General", "TRUE"),
        ("TÜFE\nCPI", "@", "TÜFE\nCPI"),
        (None, "0.0", ""),
    ],
)
def test_render_edge_cases(value, number_format, expected):
    assert render(Cell(value, number_format)) == expected


def test_row_counts_follow_the_source_structure():
    # CPI: 5 sections x 2005-2026; labour: 21 years x 12 months + 8 months of 2026;
    # population: 81 provinces + total; housing: 13 annual + 13 x 12 monthly rows.
    counts = {"cpi-index": 110, "labour-basic": 260, "population": 82, "housing": 169}
    assert {key: parsed(key)[0].total_rows for key in counts} == counts


def test_section_rows_become_columns():
    (cpi,) = parsed("cpi-index")
    sections = [row["Bölüm / Section"] for row in cpi.rows]
    assert sections[0] == "Endeks - Index"
    assert len(set(sections)) == 5 and all(sections.count(s) == 22 for s in set(sections))
    (labour,) = parsed("labour-basic")
    blocks = [f"Bölüm / Section [column {n}]" for n in (1, 12, 23)]
    expected = ["Toplam - Total", "Erkek - Male", "Kadın - Female"]
    assert [labour.rows[0][c] for c in blocks] == expected
    assert labour.warnings and "(Bin kişi - Thousand person)" in labour.notes


def test_only_row_labels_are_forward_filled():
    (trade,) = parsed("foreign-trade")
    january = [row for row in trade.rows if row["Aylar / Months"] == "Ocak - January"]
    assert [row["Yıl / Year"] for row in january] == ["2025", "2026  (r)"]
    # A blank measure is not data from the row above.
    assert january[0]["Değişim / Change / (%) [column 4]"] == ""
    assert trade.rows[0]["Aylar / Months"] == ""
    (housing,) = parsed("housing")
    annual, monthly = housing.rows[:13], housing.rows[13:]
    assert all(row["Ay Month"] == "" for row in annual)
    assert all(row["Yıl Year"] for row in monthly)
    assert monthly[1] == {**monthly[1], "Yıl Year": "2013", "Ay Month": "Şubat - February"}


def test_filters_limit_and_total():
    (table,) = parsed("cpi-index", limit=3, filters={"Bölüm / Section": "Endeks - Index"})
    assert table.total_rows == 22 and len(table.rows) == 3
    assert [row["Yıl / Year"] for row in table.rows] == ["2005", "2006", "2007"]
    with pytest.raises(InvalidFiltersError):
        parsed("cpi-index", filters={"Year": "2005"})


def workbook(**sheets):
    book = Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(list(row))
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


SIMPLE = [
    ["Örnek tablo"],
    ["Sample table"],
    ["[2025=100]"],
    ["Yıl\nYear", "Değer\nValue"],
    [2024, 10.5],
    [2025, 11.25],
    [],
    ["TÜİK, Örnek"],
]


def test_every_sheet_or_a_selected_sheet():
    body = workbook(Birinci=SIMPLE, Ikinci=SIMPLE[:4] + [[2026, 12.0]])
    first, second = parse_table(body, "xlsx")
    assert (first.sheet, first.title, first.title_en, first.unit) == (
        "Birinci",
        "Örnek tablo",
        "Sample table",
        "2025=100",
    )
    assert first.columns == ["Yıl Year", "Değer Value"]
    assert first.rows == [
        {"Yıl Year": "2024", "Değer Value": "10.5"},
        {"Yıl Year": "2025", "Değer Value": "11.25"},
    ]
    assert first.source_note == "TÜİK, Örnek" and second.total_rows == 1
    (selected,) = parse_table(body, "xlsx", sheet="Ikinci")
    assert selected.sheet == "Ikinci"
    with pytest.raises(ValueError, match="Birinci"):
        parse_table(body, "xlsx", sheet="Yok")


def test_unrecognized_layout_falls_back_to_raw_grid():
    (table,) = parse_table(workbook(Liste=[["Kod", "Ad"], ["01", "Gıda"], ["02", "Giyim"]]), "xlsx")
    assert table.layout == "raw" and table.warnings
    assert table.columns == ["column_1", "column_2"]
    assert table.rows[1] == {"column_1": "01", "column_2": "Gıda"} and table.total_rows == 3


@pytest.mark.parametrize("fmt", ["xls", "xlsx"])
def test_unreadable_workbooks_are_parse_errors(fmt):
    with pytest.raises(ParseError) as error:
        parse_table(b"not a workbook", fmt)
    assert not isinstance(error.value, ValueError)
