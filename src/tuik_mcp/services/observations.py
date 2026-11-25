"""Normalize only reviewed source selections from complete, bounded snapshots."""

import hashlib
import re
from decimal import Decimal
from typing import Literal

from openpyxl.utils.cell import column_index_from_string, coordinate_from_string, get_column_letter

from tuik_mcp.clients.base import SourceError
from tuik_mcp.models.observation import (
    CsvLocator,
    Frequency,
    HeaderCell,
    Observation,
    ObservationResult,
    ObservationSelection,
    Origin,
    Snapshot,
    WorkbookLocator,
)
from tuik_mcp.observation_mappings import Mapping, Series
from tuik_mcp.parsers.limits import ParseLimits
from tuik_mcp.parsers.models import ParsedTable
from tuik_mcp.parsers.query import missing
from tuik_mcp.parsers.spreadsheet import header_text, load_sheets

MAX_PERIODS = 120
ATTRIBUTES = {
    "Birim Çarpanı",
    "Gizlilik Durumu",
    "Gözlem Durumu",
    "Ondalık Basamak Sayısı",
    "Zaman Formatı",
    "Zaman Serisi Dönüşümü",
    "Ölçü Birimi",
}
PERIOD_PARTS: dict[Frequency, int] = {"month": 12, "quarter": 4, "year": 1}


def period_number(value: str, frequency: Frequency) -> int:
    formats = {
        "month": r"([0-9]{4})-(0[1-9]|1[0-2])",
        "quarter": r"([0-9]{4})-Q([1-4])",
        "year": r"([0-9]{4})",
    }
    parts = re.fullmatch(formats[frequency], value)
    if parts is None or int(parts[1]) < 1:
        raise ValueError(f"Invalid {frequency} period: {value[:40]!r}")
    return int(parts[1]) * PERIOD_PARTS[frequency] + (
        int(parts[2]) - 1 if parts.lastindex == 2 else 0
    )


def selected_periods(selection: ObservationSelection, frequency: Frequency) -> list[str]:
    first = period_number(selection.start, frequency)
    last = period_number(selection.end, frequency)
    if not 0 <= last - first < MAX_PERIODS:
        raise ValueError(f"Select an ordered range of at most {MAX_PERIODS} periods")
    periods = []
    for ordinal in range(first, last + 1):
        year, offset = divmod(ordinal, PERIOD_PARTS[frequency])
        suffix = {"month": f"-{offset + 1:02}", "quarter": f"-Q{offset + 1}", "year": ""}
        periods.append(f"{year:04}{suffix[frequency]}")
    return periods


def numeric_value(raw: str, decimal_separator: Literal[",", "."]) -> Decimal | None:
    # A source-specific decimal separator is intentional. Never guess grouping or locale.
    point = re.escape(decimal_separator)
    value = raw.strip()
    if len(value) <= 64 and re.fullmatch(rf"[+-]?[0-9]+(?:{point}[0-9]+)?", value):
        return Decimal(value.replace(",", "."))
    return None


def observation_id(snapshot: Snapshot, mapping: Mapping, identity: str) -> str:
    seed = f"{snapshot.resource_id}\0{snapshot.sha256}\0{mapping.id}\0v1\0{identity}"
    return "observation:" + hashlib.sha256(seed.encode()).hexdigest()


def normalize_csv(
    table: ParsedTable,
    mapping: Mapping,
    series: Series,
    periods: list[str],
    snapshot: Snapshot,
) -> dict[str, Observation]:
    selectors = mapping.constants | series.selectors
    required = set(selectors) | {mapping.period, mapping.value}
    columns = set(table.columns)
    if table.total_rows != len(table.rows) or table.layout != "table":
        raise SourceError(
            "incomplete_observation_source", "A full source scan is required", snapshot.source_url
        )
    if required - columns or columns - required - ATTRIBUTES:
        raise SourceError(
            "unsupported_observation_schema",
            "The export columns differ from those reviewed for this mapping",
            snapshot.source_url,
        )
    csv_column = table.columns.index(mapping.value) + 1
    requested = set(periods)
    attributes = [
        column for column in table.columns if column in ATTRIBUTES and column not in selectors
    ]
    results: dict[str, Observation] = {}
    for record, row in enumerate(table.rows, start=2):
        if any(row[column] != label for column, label in selectors.items()):
            continue
        period = row[mapping.period]
        try:
            period_number(period, series.frequency)
        except ValueError as exc:
            raise SourceError(
                "unsupported_observation_schema", str(exc), snapshot.source_url
            ) from exc
        if period not in requested:
            continue
        if period in results:
            raise SourceError(
                "ambiguous_observation",
                f"More than one source record matches {period} for the selected series",
                snapshot.source_url,
            )
        raw = row[mapping.value]
        source_attributes = {column: row[column] for column in attributes if row[column]}
        confidentiality = source_attributes.get("Gizlilik Durumu")
        value = numeric_value(raw, ",")
        status: Literal["observed", "missing", "suppressed", "invalid"] = "observed"
        notes = []
        if confidentiality == "Gizli veri":
            status = "suppressed"
            if value is not None:
                raw = ""
                notes.append("Value withheld according to the source confidentiality flag.")
        elif any(
            source_attributes.get(key)
            for key in ("Ölçü Birimi", "Birim Çarpanı", "Zaman Serisi Dönüşümü", "Gözlem Durumu")
        ) or confidentiality not in (None, "Yayıma serbest"):
            status = "invalid"
            notes.append("Source attributes differ from the reviewed mapping.")
        elif missing(raw):
            status = "missing"
        elif value is None or (series.measure.unit == "person" and value != value.to_integral()):
            status = "invalid"
            notes.append("Value does not match the reviewed numeric format.")
        results[period] = Observation(
            id=observation_id(snapshot, mapping, f"{record}:{csv_column}"),
            locator=CsvLocator(record=record, column=csv_column, headers=table.columns),
            period=period,
            original_value=raw,
            numeric_value=value if status == "observed" else None,
            status=status,
            source_attributes=source_attributes,
            notes=notes,
        )
    return results


def normalize_workbook(
    body: bytes,
    mapping: Mapping,
    series: Series,
    periods: list[str],
    snapshot: Snapshot,
    limits: ParseLimits,
) -> dict[str, Observation]:
    # Callers verify the reviewed digest before the workbook's cell addresses are used.
    if series.example not in periods:
        return {}
    assert mapping.format in {"xls", "xlsx"} and series.row and series.column
    sheet = next(
        item for item in load_sheets(body, mapping.format, limits) if item.name == mapping.sheet
    )

    def read_cell(address: str):
        col, row = coordinate_from_string(address)
        return sheet.rows[row - 1][column_index_from_string(col) - 1]

    headers = [
        HeaderCell(
            cell=address, label=header_text(read_cell(anchor or address)), merged_from=anchor
        )
        for address, anchor in series.headers
    ]
    address = f"{get_column_letter(series.column)}{series.row}"
    cell = read_cell(address)
    raw = "" if cell.empty else str(cell.value)
    value = Decimal(str(cell.number)) if cell.number is not None else None
    if value is not None and not value.is_finite():
        value = None
    return {
        series.example: Observation(
            id=observation_id(snapshot, mapping, f"{sheet.name}!{address}"),
            locator=WorkbookLocator(
                kind=mapping.format, sheet=sheet.name, cell=address, headers=headers
            ),
            period=series.example,
            original_value=raw,
            numeric_value=value,
            status="observed" if value is not None else "missing" if missing(raw) else "invalid",
            notes=[header_text(read_cell(address)) for address in series.note_cells],
        )
    }


def observation_result(
    mapping: Mapping,
    series: Series,
    selection: ObservationSelection,
    periods: list[str],
    snapshot: Snapshot,
    values: dict[str, Observation],
) -> ObservationResult:
    observations = [values[period] for period in periods if period in values]
    valid = sum(item.status == "observed" for item in observations)
    origins: dict[str, Origin] = {
        "selection": "user_selected",
        "period": "source_declared",
        "frequency": "source_declared" if mapping.format == "csv" else "recipe_defined",
        "geography": "recipe_defined",
        "measure": "recipe_defined",
        "adjustment": "source_declared" if series.adjustment is not None else "unknown",
        "dimensions": "source_declared" if mapping.format == "csv" else "recipe_defined",
        "publication_date": "source_declared" if snapshot.publication_date else "unknown",
    }
    return ObservationResult(
        mapping_id=mapping.id,
        selection=selection,
        snapshot=snapshot,
        status="complete" if valid == len(periods) else "incomplete",
        frequency=series.frequency,
        geography=series.geography,
        measure=series.measure,
        adjustment=series.adjustment,
        origins=origins,
        dimensions=mapping.constants | series.selectors,
        observations=observations,
        absent_periods=[period for period in periods if period not in values],
        valid_count=valid,
        expected_count=len(periods),
        notes=[] if valid == len(periods) else ["The selected range contains unavailable inputs."],
    )
