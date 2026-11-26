"""Analyst XLSX for a complete, reviewed observation selection."""

import io
import json
import re
from collections import defaultdict
from collections.abc import Iterable
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.chart.data_source import AxDataSource, NumData, NumVal, StrData, StrRef, StrVal
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.chart.text import RichText
from openpyxl.drawing.line import LineProperties
from openpyxl.drawing.text import CharacterProperties, Paragraph, ParagraphProperties
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.worksheet.worksheet import Worksheet

from tuik_mcp.models.observation import Observation

MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
INVALID_TEXT = re.compile(r"[\x00-\x1f\x7f-\x9f\ufffe\uffff]")
MAX_CHART_PERIODS = 60
NUMBER_FORMAT = "#,##0.##########"
PERCENT_FORMAT = "0.00####%"
POINT_FORMAT = '0.00####" pp"'


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def cell_value(sheet: Worksheet, row: int, column: int, value):
    cell = sheet.cell(row, column)
    if isinstance(value, str):
        # XML cannot carry some source controls. The pinned input keeps the original
        # bytes. Escaping these characters also keeps them visible in the raw sheet.
        value = INVALID_TEXT.sub(lambda match: f"\\u{ord(match[0]):04x}", value)
        if len(value.encode("utf-16-le")) // 2 > 32767:
            raise ValueError("A source cell exceeds Excel's text limit; use the original file.")
        cell.value = value
        # Force inline strings even for formula-like input or Excel error literals.
        cell.data_type = "s"
    elif value is not None:
        cell.value = value
    return cell


def sheet_table(
    sheet: Worksheet,
    name: str,
    columns: list[tuple[str, int]],
    rows: Iterable[tuple],
    *,
    start: int = 1,
) -> int:
    for column, (title, width) in enumerate(columns, 1):
        sheet.column_dimensions[get_column_letter(column)].width = width
        cell = cell_value(sheet, start, column, title)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="24536A")
    end = start
    for end, values in enumerate(rows, start + 1):
        for column, value in enumerate(values, 1):
            cell_value(sheet, end, column, value)
    if end > start:
        table = Table(displayName=name, ref=f"A{start}:{get_column_letter(len(columns))}{end}")
        table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
        sheet.add_table(table)
    sheet.freeze_panes = f"A{start + 1}"
    sheet.sheet_view.showGridLines = False
    return end


def excel_number(value: Decimal | None, unit: str) -> float | None:
    if value is None:
        return None
    # Excel stores 15 significant digits. The normalized sheet also stores the
    # full decimal string, independent of Excel's binary numerical precision.
    number = value / 100 if unit == "percent" else value
    magnitude = abs(number)
    if magnitude and not Decimal("2.2250738585072e-308") <= magnitude <= Decimal(
        "9.99999999999999e307"
    ):
        raise ValueError("A value is outside Excel's numeric range; use the exact observations.")
    return float(format(number, ".15g"))


def value_format(unit: str) -> str:
    return {"percent": PERCENT_FORMAT, "percentage_points": POINT_FORMAT}.get(unit, NUMBER_FORMAT)


def raw_rows(observations: list[Observation]):
    for observation in observations:
        locator = observation.locator
        yield (
            observation.id,
            observation.value_text,
            locator.format,
            getattr(locator, "record", None),
            getattr(locator, "column", None),
            getattr(locator, "sheet", None),
            getattr(locator, "cell", None),
            json_text([header.model_dump() for header in getattr(locator, "headers", [])]),
            json_text(observation.source_status),
            " | ".join(observation.notes),
        )


def normalized_row(observation: Observation) -> tuple:
    period, geography, unit, measure = (
        observation.period,
        observation.geography,
        observation.unit,
        observation.measure,
    )
    return (
        period.value if period else None,
        period.frequency if period else None,
        geography.label if geography else None,
        geography.code if geography else None,
        geography.level if geography else None,
        measure.label if measure else None,
        unit.kind if unit else None,
        excel_number(observation.value, unit.kind if unit else "unknown"),
        observation.status,
        str(observation.value) if observation.value is not None else None,
        unit.multiplier if unit else None,
        unit.currency if unit else None,
        unit.base_period if unit else None,
        observation.adjustment.seasonal,
        observation.adjustment.calendar,
        measure.basis if measure else None,
        json_text(observation.dimensions),
        observation.reason,
        observation.id,
    )


def series_groups(observations: list[Observation]) -> list[list[Observation]]:
    groups: dict[str, list[Observation]] = defaultdict(list)
    for observation in observations:
        data = observation.model_dump(
            mode="json", include={"geography", "unit", "measure", "adjustment", "dimensions"}
        )
        data["frequency"] = observation.period.frequency if observation.period else None
        groups[json_text(data)].append(observation)
    return list(groups.values())


def summary_rows(groups: list[list[Observation]]):
    for group in groups:
        first = group[0]
        values = [item.value for item in group if item.value is not None]
        periods = sorted(item.period.value for item in group if item.period)
        unit = first.unit.kind if first.unit else "unknown"
        yield (
            first.measure.label if first.measure else "Unknown",
            first.geography.label if first.geography else "Unknown",
            unit,
            len(group),
            len(values),
            len(group) - len(values),
            excel_number(min(values), unit) if values else None,
            excel_number(max(values), unit) if values else None,
            periods[0] if periods else None,
            periods[-1] if periods else None,
            first.adjustment.seasonal,
            first.adjustment.calendar,
            first.unit.multiplier if first.unit else None,
            first.unit.base_period if first.unit else None,
            first.unit.currency if first.unit else None,
            first.measure.basis if first.measure else None,
            json_text(first.dimensions),
        )


def add_chart(sheet: Worksheet, end: int, observations: list[Observation]) -> bool:
    dated = [item for item in observations if item.period is not None]
    periods = [item.period.value for item in dated]
    if (
        len(dated) != len(observations)
        or not 2 <= len(periods) <= MAX_CHART_PERIODS
        or len(set(periods)) != len(periods)
        or not all(item.value is not None for item in dated)
        or dated[0].unit is None
    ):
        return False
    ordered = sorted(dated, key=lambda item: item.period.value)
    unit = ordered[0].unit.kind
    start = end + 4
    labels = [item.period.value for item in ordered]
    values = [excel_number(item.value, unit) for item in ordered]
    cell_value(sheet, start, 1, "Period")
    cell_value(sheet, start, 2, "Value")
    for row, (label, value) in enumerate(zip(labels, values, strict=True), start + 1):
        cell_value(sheet, row, 1, label)
        cell_value(sheet, row, 2, value).number_format = value_format(unit)
    chart = LineChart()
    chart.title = f"Döneme göre değer ({unit})"
    chart.varyColors = False
    chart.smooth = False
    chart.x_axis.axPos = "b"
    chart.y_axis.axPos = "l"
    for axis in (chart.x_axis, chart.y_axis):
        axis.delete = False
        axis.crosses = "min"
        axis.majorTickMark = "out"
        axis.tickLblPos = "nextTo"
        axis.txPr = RichText(
            p=[
                Paragraph(
                    pPr=ParagraphProperties(defRPr=CharacterProperties(sz=1000, solidFill="243745"))
                )
            ]
        )
    chart.x_axis.tickLblSkip = max(1, (len(labels) + 7) // 8)
    chart.y_axis.majorGridlines.spPr = GraphicalProperties(ln=LineProperties(solidFill="D4DFE4"))
    chart.y_axis.numFmt = {
        "percent": "0.00%",
        "percentage_points": '0.00" pp"',
    }.get(unit, "General")
    chart.width, chart.height = 20, 10
    chart.legend = None
    chart.add_data(Reference(sheet, min_col=2, min_row=start + 1, max_row=start + len(labels)))
    series = chart.series[0]
    labels_ref = Reference(sheet, min_col=1, min_row=start + 1, max_row=start + len(labels))
    series.cat = AxDataSource(
        strRef=StrRef(
            f=str(labels_ref),
            strCache=StrData(
                ptCount=len(labels), pt=[StrVal(idx=i, v=label) for i, label in enumerate(labels)]
            ),
        )
    )
    series.val.numRef.numCache = NumData(
        formatCode=value_format(unit),
        ptCount=len(values),
        pt=[NumVal(idx=i, v=value) for i, value in enumerate(values)],
    )
    series.marker.symbol = "circle"
    series.marker.size = 5
    series.marker.graphicalProperties.solidFill = "24536A"
    series.marker.graphicalProperties.line.solidFill = "24536A"
    series.graphicalProperties.line.solidFill = "24536A"
    series.smooth = False
    sheet.add_chart(chart, f"D{start}")
    return True


def build(
    observations: list[Observation],
    sources: list[tuple[str, str]],
    manifest_uri: str,
    *,
    complete_range: bool,
) -> bytes:
    workbook = Workbook()
    analysis = workbook.active
    analysis.title = "Analysis"
    normalized = workbook.create_sheet("Normalized Data")
    raw = workbook.create_sheet("Raw Data")
    methods = workbook.create_sheet("Sources and Methods")

    groups = series_groups(observations)
    end = sheet_table(
        analysis,
        "SeriesSummary",
        [
            ("Measure", 39),
            ("Geography", 25),
            ("Unit", 21),
            ("Source records", 19),
            ("Valid", 12),
            ("Without value", 17),
            ("Minimum", 18),
            ("Maximum", 18),
            ("First observed period", 24),
            ("Last observed period", 24),
            ("Seasonal adjustment", 23),
            ("Calendar adjustment", 24),
            ("Multiplier", 16),
            ("Index base", 16),
            ("Currency", 15),
            ("Change basis", 26),
            ("Dimensions", 55),
        ],
        summary_rows(groups),
    )
    for row, group in enumerate(groups, 2):
        fmt = value_format(group[0].unit.kind if group[0].unit else "unknown")
        for column in (7, 8):
            analysis.cell(row, column).number_format = fmt
    chart = len(groups) == 1 and complete_range and add_chart(analysis, end, groups[0])

    sheet_table(
        normalized,
        "NormalizedObservations",
        [
            ("Period", 15),
            ("Frequency", 15),
            ("Geography", 23),
            ("Code", 12),
            ("Level", 14),
            ("Measure", 39),
            ("Unit", 21),
            ("Value", 19),
            ("Status", 17),
            ("Exact decimal", 25),
            ("Multiplier", 16),
            ("Currency", 15),
            ("Index base", 16),
            ("Seasonal adjustment", 23),
            ("Calendar adjustment", 24),
            ("Change basis", 26),
            ("Dimensions", 55),
            ("Reason", 55),
            ("Observation ID", 38),
        ],
        (normalized_row(item) for item in observations),
    )
    for row, item in enumerate(observations, 2):
        normalized.cell(row, 8).number_format = value_format(
            item.unit.kind if item.unit else "unknown"
        )

    sheet_table(
        raw,
        "OriginalObservations",
        [
            ("Observation ID", 38),
            ("Original value", 23),
            ("Format", 12),
            ("CSV record", 14),
            ("CSV column", 24),
            ("Sheet", 15),
            ("Cell", 12),
            ("Header and merge lineage", 55),
            ("Source flags", 36),
            ("Published notes", 52),
        ],
        raw_rows(observations),
    )

    notes = [
        ("Manifest URI", manifest_uri),
        (
            "Calculation",
            "Materialized values. Each summary row groups comparable observations. "
            "Minimum and maximum use valid values only. Counts include every selected observation.",
        ),
        (
            "Scale",
            "Excel stores percentages as fractions and shows percent-formatted values. "
            "Percentage points stay on the published scale. Read the multiplier with the unit.",
        ),
        (
            "Precision",
            "Numeric cells use 15 significant digits. See Exact decimal for the full "
            "normalized value and Raw Data for the source text.",
        ),
        (
            "Missing values",
            "Blank numeric cells retain their status, reason and published marker. "
            "Unpublished periods are recorded in range coverage when a range was selected.",
        ),
        (
            "Raw text",
            "Source strings are literal text. Control characters appear as Unicode escapes. "
            "Use the retained source for the original file bytes.",
        ),
        (
            "Native chart",
            "An editable Excel line chart plots the complete selected series."
            if chart
            else "A line chart requires one complete series with 2–60 distinct valid periods "
            "and a specified, fully covered range.",
        ),
    ]
    methods_end = sheet_table(
        methods,
        "WorkbookSourceNotes",
        [("Source / method", 28), ("Details", 100)],
        [*sources, *notes],
    )
    for row in range(2, methods_end + 1):
        cell = methods.cell(row, 2)
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        methods.row_dimensions[row].height = min(100, 18 * max(1, (len(cell.value) + 85) // 86))

    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()
