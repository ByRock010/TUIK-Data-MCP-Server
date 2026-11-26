"""Replay the held-out 2021 ADNKS workbook with six independent sheet samples."""

import argparse
import json
from dataclasses import replace
from pathlib import Path

from audit import align, digest, field, from_coordinate, raw_number, raw_workbook
from openpyxl.utils.cell import column_index_from_string, coordinate_from_string

from tuik_mcp.clients.base import SourceError
from tuik_mcp.parsers.limits import ParseLimits
from tuik_mcp.services.parsing import parse_source

SOURCE = "https://www.tuik.gov.tr/indir/duyuru/favori_raporlar.xlsx"
SHA256 = "3901a6d86382c61175b7e9ca06db8a2b1fb16ece662bd0fb5a947f13b503e7b4"
OFFLINE_MAX_CELLS = 700_000
# One total population count near the middle of each sheet. Choose count
# columns from the source headers before comparing any production output.
SAMPLES = {
    "İL NÜFUSU": ("C49", (6, 7), (2,), 7),
    "İLÇE NÜFUSU": ("E504", (6, 7), (3, 4), 7),
    "BÜYÜKŞEHİR B. NÜFUSU": ("C22", (6,), (2,), 6),
    "BELEDİYE NÜFUSU": ("J687", (6,), (6, 7, 8), 6),
    "MAHALLE NÜFUSU": ("K16069", (6,), (6, 7, 8, 9), 6),
    "KÖY NÜFUSU": ("H9112", (6,), (5, 6, 7), 6),
}


def compare(sheet, table, address, header_rows, label_columns, row_count_start):
    letter, row_number = coordinate_from_string(address)
    column = column_index_from_string(letter) - 1
    row_index = row_number - 1
    row = sheet.grid[row_index]
    value, number_format = row[column]
    if not raw_number(value):
        raise ValueError(f"Expected a numeric source cell: {sheet.name}!{address}")
    numeric_cells = [(c, v, f) for c, (v, f) in enumerate(row) if raw_number(v)]
    hit, matches = align(row, numeric_cells, table)
    parser_column = table.columns[column]
    parser_value = hit[1][parser_column] if hit else None
    source_rows = sum(any(field(value) for value, _ in row) for row in sheet.grid[row_count_start:])
    # Compare the explicit source markers on the sampled row independently of
    # alignment. In this workbook C is a suppression marker and - is no data.
    marker_columns = {table.columns[c]: v for c, (v, _) in enumerate(row) if v in ("-", "C")}
    return {
        "sheet": sheet.name,
        "cell": address,
        "header_context": [from_coordinate(sheet, r - 1, column) for r in header_rows],
        "row_labels": [field(row[c - 1][0]) for c in label_columns],
        "reference_period": "2021-12-31",
        "period_source": {"cell": "A1", "text": field(sheet.grid[0][0][0])},
        "unit": "persons",
        "number_format": number_format,
        "source_value": value,
        "parser_layout": table.layout,
        "source_rows": source_rows,
        "parser_rows": table.total_rows,
        "parser_row": hit[0] if hit else None,
        "parser_column": parser_column,
        "parser_value": parser_value,
        "unique_row_matches": matches,
        "merged_ranges": len(sheet.merges),
        "value_matches": parser_value == str(value),
        "source_markers": marker_columns,
        "parser_markers": {column: hit[1][column] for column in marker_columns} if hit else {},
    }


def audit_workbook(path):
    body = path.read_bytes()
    if digest(body) != SHA256:
        raise ValueError("The workbook SHA-256 differs from the reviewed snapshot")
    limits = ParseLimits()
    try:
        parse_source(body, "xlsx", SOURCE, budgets=limits)
    except SourceError as exc:
        if exc.code != "parse_limit_exceeded":
            raise
        default = {"classification": "unsupported", "code": exc.code, "message": str(exc)}
    else:
        default = {"classification": "available"}
    offline = parse_source(
        body, "xlsx", SOURCE, budgets=replace(limits, max_cells=OFFLINE_MAX_CELLS)
    )
    independent = list(raw_workbook(body, "xlsx"))
    if [sheet.name for sheet in independent] != [table.sheet for table in offline]:
        raise ValueError("Production parser did not preserve workbook sheet names and order")
    samples = []
    for sheet, table in zip(independent, offline, strict=True):
        address, headers, labels, start = SAMPLES[sheet.name]
        samples.append(compare(sheet, table, address, headers, labels, start))
        if sheet.name == "MAHALLE NÜFUSU":
            # Exercise both published count and deliberate male/female suppression.
            samples.append(compare(sheet, table, "K12040", headers, labels, start))
    reconciled = all(
        sample["value_matches"]
        and sample["source_rows"] == sample["parser_rows"]
        and sample["source_markers"] == sample["parser_markers"]
        for sample in samples
    )
    return {
        "source": {
            "url": SOURCE,
            "format": "xlsx",
            "size_bytes": len(body),
            "sha256": SHA256,
            "reference_period": "2021-12-31",
            "retrieved_date": "2026-11-25",
            "split": "supplemental held-out",
        },
        "default": default,
        "offline": {
            "classification": "inferred" if reconciled else "partial",
            "max_workbook_cells": OFFLINE_MAX_CELLS,
            "samples": samples,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit_workbook(args.workbook), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
