"""Compare production extraction with independent source record and cell reads."""

import argparse
import csv
import hashlib
import io
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import openpyxl
import xlrd
from openpyxl.utils import get_column_letter

from tuik_mcp.clients.base import SourceError
from tuik_mcp.config import Settings
from tuik_mcp.parsers.limits import ParseLimits
from tuik_mcp.services.parsing import parse_source

MARKERS = {"-", "–", "—", "…", "...", ".", "..", ":", "n/a"}
PERIOD = re.compile(
    r"(?<!\d)(?:19|20)\d{2}(?:[-/](?:0?[1-9]|1[0-2]|Q[1-4]))?(?!\d)|"
    r"Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık",
    re.IGNORECASE,
)
UNIT = re.compile(
    r"%|‰|\b(?:\d+\s*=\s*100|bin kişi|kişi|adet|USD|dolar|TL|"
    r"endeks|oran|miktar|terajul|saat|thousand|persons|number|percent)\b",
    re.IGNORECASE,
)


def digest(body):
    return hashlib.sha256(body).hexdigest()


def field(value):
    return " ".join(str(value).split()) if value is not None else ""


def number(value):
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    text = str(value).strip().replace("−", "-").removesuffix("%").replace(",", ".")
    try:
        result = Decimal(text)
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def evenly(items):
    if len(items) <= 5:
        return items
    return [items[i * (len(items) - 1) // 4] for i in range(5)]


def csv_audit(body, table):
    reader = csv.reader(io.StringIO(body.decode("utf-8-sig")), delimiter=";", strict=True)
    columns = next(reader)
    raw_rows = []
    scan_error = None
    try:
        raw_rows.extend(reader)
    except csv.Error as exc:
        scan_error = str(exc)
    parsed = table.rows if table else []
    period = next((i for i, c in enumerate(columns) if c.startswith("Zaman")), None)
    measure = next(
        (
            i
            for i, c in enumerate(columns)
            if c in {"Gözlem", "Gözlem Değeri", "OBS_VALUE"} or c.startswith("Gözlem Değeri (")
        ),
        None,
    )
    data = [(i, row) for i, row in enumerate(raw_rows) if len(row) == len(columns)]
    numeric = [
        (i, row) for i, row in data if measure is not None and number(row[measure]) is not None
    ]
    markers = Counter(
        row[measure] for _, row in data if measure is not None and row[measure] in MARKERS
    )
    columns_match = bool(table and table.columns == columns)
    samples = []
    for i, row in evenly(numeric):
        observed = parsed[i].get(columns[measure]) if columns_match and i < len(parsed) else None
        units = {
            name: row[j]
            for j, name in enumerate(columns)
            if row[j] and ("Ölçü Birimi" in name or name == "Birim Çarpanı")
        }
        if not units:
            units = {
                name: row[j]
                for j, name in enumerate(columns[:measure])
                if row[j] and UNIT.search(row[j])
            }
        samples.append(
            {
                "record": i + 2,
                "column": measure + 1,
                "header": columns[measure],
                "raw": row[measure],
                "parser": observed,
                "value_matches": observed == row[measure],
                "observed_context_matches": bool(
                    columns_match
                    and i < len(parsed)
                    and parsed[i] == dict(zip(columns, row, strict=True))
                ),
                "period": row[period] if period is not None else None,
                "period_matches": bool(
                    period is not None
                    and columns_match
                    and i < len(parsed)
                    and parsed[i].get(columns[period]) == row[period]
                ),
                "unit_context": units,
                "dimensions": dict(zip(columns[:measure], row[:measure], strict=True)),
            }
        )
    checks = {
        "layout": "table" if columns_match else "unavailable" if not table else "header mismatch",
        "coverage": {
            "source_records": len(raw_rows),
            "parser_rows": len(parsed),
            "ragged_records": len(raw_rows) - len(data),
            "source_scan_error": scan_error,
        },
        "missing_markers": {
            "raw": markers,
            "parser": Counter(
                row.get(columns[measure])
                for row in parsed
                if measure is not None and row.get(columns[measure]) in MARKERS
            ),
        },
        "labels": "matched" if columns_match else "unavailable",
        "blank_observations": {
            "raw": sum(row[measure] == "" for _, row in data) if measure is not None else None,
            "parser": sum(row.get(columns[measure]) == "" for row in parsed)
            if measure is not None and table
            else None,
        },
        "period": sum(item["period_matches"] for item in samples),
        "unit": sum(bool(item["unit_context"]) for item in samples),
    }
    return samples, checks


@dataclass
class RawSheet:
    name: str
    grid: list[list[tuple[object, str]]]
    merges: list[tuple[int, int, int, int]]


def raw_workbook(body, fmt):
    if fmt == "xlsx":
        book = openpyxl.load_workbook(io.BytesIO(body), data_only=True)
        try:
            for sheet in book.worksheets:
                yield RawSheet(
                    sheet.title,
                    [[(c.value, c.number_format) for c in row] for row in sheet.iter_rows()],
                    [
                        (r.min_row - 1, r.max_row, r.min_col - 1, r.max_col)
                        for r in sheet.merged_cells.ranges
                    ],
                )
        finally:
            book.close()
    else:
        book = xlrd.open_workbook(file_contents=body, formatting_info=True)
        try:
            for sheet in book.sheets():
                grid = []
                for row in sheet.get_rows():
                    cells = []
                    for c in row:
                        fmt_key = (
                            book.xf_list[c.xf_index].format_key if c.xf_index is not None else 0
                        )
                        fmt_str = (
                            book.format_map[fmt_key].format_str
                            if fmt_key in book.format_map
                            else "General"
                        )
                        value = c.value
                        if c.ctype == xlrd.XL_CELL_DATE:
                            value = xlrd.xldate_as_datetime(value, book.datemode)
                        elif c.ctype == xlrd.XL_CELL_BOOLEAN:
                            value = bool(value)
                        cells.append((value, fmt_str))
                    grid.append(cells)
                yield RawSheet(sheet.name, grid, list(sheet.merged_cells))
        finally:
            book.release_resources()


def raw_number(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def from_coordinate(sheet, r, c):
    raw = sheet.grid[r][c][0]
    if field(raw):
        return field(raw)
    for start, stop, left, right in sheet.merges:
        if start <= r < stop and left <= c < right:
            return field(sheet.grid[start][left][0])
    return ""


def workbook_rows(sheet):
    good = []
    for r, row in enumerate(sheet.grid):
        cells = [
            (c, value, form) for c, (value, form) in enumerate(row) if c > 0 and raw_number(value)
        ]
        if (
            r >= 3
            and (len(cells) >= 2 or (cells and field(row[0][0])))
            and any(not (1900 <= value <= 2100 and value % 1 == 0) for _, value, _ in cells)
        ):
            good.append((r, cells))
    return good


def equal_display(value, form, observed):
    extracted = number(observed)
    if extracted is None:
        return False
    source = Decimal(str(value))
    if form.casefold() == "general":
        if source == source.to_integral_value() and abs(source) < 10**15:
            return source == extracted
        return math.isclose(float(source), float(extracted), rel_tol=5e-10)
    # Compare the underlying number to the displayed precision declared by Excel.
    selected = form.split(";")
    section = selected[min(1, len(selected) - 1)] if source < 0 else selected[0]
    if "%" in section:
        if not observed.rstrip().endswith("%"):
            return False
        source *= 100
    digits = re.search(r"\.([0#?]+)", section)
    tolerance = Decimal("0.5").scaleb(-len(digits[1]) if digits else 0)
    return abs(source - extracted) <= tolerance


def align(row, cells, table):
    if not table:
        return None, 0
    columns = table.columns
    # A row must agree on every independently observed numeric cell. Anchor its
    # first source label as well when the parser exposes it.
    cell_checks = [(columns[c], value, form) for c, value, form in cells if c < len(columns)]
    if len(cell_checks) != len(cells):
        return None, 0
    label_checks = [
        (column, field(value))
        for column, (value, _) in zip(columns, row, strict=False)
        if isinstance(value, str) and field(value) and value not in MARKERS
    ]
    hits = [
        (i, out)
        for i, out in enumerate(table.rows, 1)
        if all(field(out.get(column)) == label for column, label in label_checks)
        and all(equal_display(value, form, out.get(column)) for column, value, form in cell_checks)
    ]
    return (hits[0] if len(hits) == 1 else None), len(hits)


def excel_audit(body, fmt, tables):
    raw = list(raw_workbook(body, fmt))
    table_map = {table.sheet: table for table in tables}
    candidates = {sheet.name: workbook_rows(sheet) for sheet in raw}
    pools = [
        [(sheet, r, cells, c, v, f) for r, cells in candidates[sheet.name] for c, v, f in cells]
        for sheet in raw
        if candidates[sheet.name]
    ]
    # Include each data-bearing sheet even if another sheet dominates file size.
    if len(pools) > 1:
        sample_pool = [pool[len(pool) // 2] for pool in pools]
        sample_pool.extend(
            item
            for item in evenly([item for pool in pools for item in pool])
            if item not in sample_pool
        )
        selected = sample_pool[: max(5, len(pools))]
    else:
        selected = evenly(pools[0]) if pools else []
    samples = []
    marker_checks = Counter()
    positioned = {}
    for sheet, r, cells, c, value, form in selected:
        table = table_map.get(sheet.name)
        raw_row = sheet.grid[r]
        cache = (sheet.name, r)
        if cache not in positioned:
            positioned[cache] = align(raw_row, cells, table)
        hit, matches = positioned[cache]
        parser_column = table.columns[c] if table and c < len(table.columns) else None
        output = hit[1].get(parser_column) if hit else None
        first_data = candidates[sheet.name][0][0]
        headers = [(i + 1, from_coordinate(sheet, i, c)) for i in range(first_data)]
        headers = [(i, text) for i, text in headers if text][-6:]
        row_labels = [
            from_coordinate(sheet, r, i)
            for i, (value, _) in enumerate(raw_row[:c])
            if not raw_number(value) or value % 1 == 0 and 1900 <= value <= 2100
        ]
        row_labels = [label for label in row_labels if label][:6]
        period = next((text for text in row_labels if PERIOD.search(text)), None)
        if not period:
            period = next((text for _, text in reversed(headers) if PERIOD.search(text)), None)
        units = [text for _, text in headers if UNIT.search(text)]
        units += [text for text in row_labels if UNIT.search(text)]
        if "%" in form:
            units.append("% (Excel format)")
        label_match = bool(
            parser_column
            and not parser_column.startswith("column_")
            and any(text.casefold() in field(parser_column).casefold() for _, text in headers)
        )
        samples.append(
            {
                "sheet": sheet.name,
                "cell": f"{get_column_letter(c + 1)}{r + 1}",
                "source_header_candidates": [list(item) for item in headers],
                "source_row_labels": row_labels,
                "source_period_context": period,
                "source_unit_context": units,
                "number_format": form,
                "raw": value,
                "parser_row": hit[0] if hit else None,
                "parser_column": parser_column,
                "parser_value": output,
                "parser_candidate_rows": matches,
                "value_matches": output is not None,
                "column_label_matched": label_match,
                "period_on_output": bool(
                    hit
                    and period
                    and (
                        period.casefold() in field(parser_column).casefold()
                        or any(
                            period.casefold() == field(item).casefold()
                            or (number(period) is not None and number(period) == number(item))
                            for item in hit[1].values()
                        )
                    )
                ),
                "parser_unit": table.unit if table else None,
            }
        )
    for (name, r), (hit, _) in positioned.items():
        sheet = next(s for s in raw if s.name == name)
        table = table_map.get(name)
        for c, (value, _) in enumerate(sheet.grid[r]):
            if isinstance(value, str) and value in MARKERS:
                marker_checks["source"] += 1
                if hit and c < len(table.columns):
                    observed = hit[1][table.columns[c]]
                    marker_checks["preserved" if observed == value else "changed"] += 1
                else:
                    marker_checks["unlocated"] += 1
    checks = {
        "layout": {
            s.name: {
                "parser": table_map[s.name].layout if s.name in table_map else "unavailable",
                "source_rows": len(s.grid),
                "independent_candidate_rows": len(candidates[s.name]),
                "parser_rows": table_map[s.name].total_rows if s.name in table_map else 0,
                "merged_ranges": len(s.merges),
                "warnings": table_map[s.name].warnings if s.name in table_map else [],
            }
            for s in raw
        },
        "coverage": {"source_sheets": len(raw), "parser_sheets": len(tables)},
        "missing_markers": marker_checks,
        "labels": sum(item["column_label_matched"] for item in samples),
        "period": sum(item["period_on_output"] for item in samples),
        "unit": sum(bool(item["source_unit_context"]) for item in samples),
    }
    return samples, checks


def audit(entry, source_dir):
    body = (source_dir / entry["local_file"]).read_bytes()
    if digest(body) != entry["sha256"]:
        raise ValueError(f"Source hash mismatch: {entry['local_file']}")
    fmt = entry["format"]
    settings = Settings()
    error = None
    tables = []
    research_probe = None
    parse_options = {
        "budgets": ParseLimits.from_settings(settings),
        "scan_max_rows": settings.query_max_rows,
        "scan_max_cells": settings.query_max_cells,
    }
    try:
        if len(body) > settings.max_response_bytes:
            raise SourceError(
                "response_too_large", "Default download cap exceeded", entry["source_page"]
            )
        tables = parse_source(body, fmt, entry["source_page"], **parse_options)
    except SourceError as exc:
        error = {"code": exc.code, "message": str(exc)}
    if fmt == "csv":
        samples, checks = csv_audit(body, tables[0] if tables else None)
        if error and error["code"] == "response_too_large":
            # These source bytes are already cached under the collection cap.
            # Exercise extraction offline with the normal parsing and scan limits.
            try:
                (probe_table,) = parse_source(body, fmt, entry["source_page"], **parse_options)
                probe_samples, probe_checks = csv_audit(body, probe_table)
                research_probe = {
                    "samples": probe_samples,
                    "checks": probe_checks,
                    "values_traced": sum(sample["value_matches"] for sample in probe_samples),
                }
            except SourceError as exc:
                research_probe = {"error": {"code": exc.code, "message": str(exc)}}
    else:
        samples, checks = excel_audit(body, fmt, tables)
    matched = sum(s["value_matches"] for s in samples)
    if error or not tables or all(t.layout == "raw" for t in tables):
        result = "unsupported"
    elif (
        matched < len(samples)
        or len(samples) < 5
        or (
            fmt == "csv"
            and checks["coverage"]["source_records"] != checks["coverage"]["parser_rows"]
        )
    ):
        result = "partial"
    elif (
        fmt == "csv"
        and all(sample["observed_context_matches"] for sample in samples)
        and matched == checks["period"] == checks["unit"]
        and (checks["missing_markers"]["raw"] == checks["missing_markers"]["parser"])
    ):
        result = "exact"
    else:
        result = "inferred"
    return {
        "source": entry,
        "classification": result,
        "error": error,
        "samples": samples,
        "checks": checks,
        "values_traced": matched,
    } | ({"offline_probe_above_download_cap": research_probe} if research_probe else {})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_directory", type=Path)
    args = parser.parse_args()
    entries = json.loads((args.input_directory / "manifest.json").read_bytes())
    records = [audit(item, args.input_directory) for item in entries]
    report = {
        "files": len(records),
        "independent_samples": sum(len(row["samples"]) for row in records),
        "values_traced": sum(row["values_traced"] for row in records),
        "extra_offline_values_traced": sum(
            row.get("offline_probe_above_download_cap", {}).get("values_traced", 0)
            for row in records
        ),
        "summary": dict(Counter(row["classification"] for row in records)),
        "strata": {
            domain: {
                split: Counter(
                    row["classification"]
                    for row in records
                    if row["source"]["domain"] == domain and row["source"]["split"] == split
                )
                for split in ("development", "held_out")
            }
            for domain in sorted({row["source"]["domain"] for row in records})
        },
        "records": records,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
