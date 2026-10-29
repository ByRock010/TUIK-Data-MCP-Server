import csv
import io

from .errors import InvalidFiltersError, ParseError, ParseLimitError
from .models import ParsedTable


def parse_csv(
    body: bytes,
    *,
    limit: int | None = None,
    filters: dict[str, str] | None = None,
    scan_max_rows: int | None = None,
    scan_max_cells: int | None = None,
) -> list[ParsedTable]:
    """Preserve UTF-8 CSV strings and count all matching rows while bounding output."""
    filters = filters or {}
    try:
        text = body.decode("utf-8-sig")
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=";,\t")
        reader = csv.DictReader(io.StringIO(text), dialect=dialect, strict=True)
        columns = reader.fieldnames
        if not columns or len(columns) != len(set(columns)) or any(not c for c in columns):
            raise ValueError("Missing or duplicate CSV columns")
        if set(filters) - set(columns):
            raise InvalidFiltersError("Use exact column names returned by get_tuik_data")
        rows, total = [], 0
        if scan_max_cells is not None and len(columns) > scan_max_cells:
            raise ParseLimitError("CSV columns exceed the query cell budget")
        for examined, row in enumerate(reader, 1):
            if (
                scan_max_rows is not None
                and examined > scan_max_rows
                or (scan_max_cells is not None and examined * len(columns) > scan_max_cells)
            ):
                raise ParseLimitError(
                    "CSV exceeds the query row/cell scan budget; no partial count"
                )
            if None in row or any(v is None for v in row.values()):
                raise ValueError("CSV row width mismatch")
            if all(row[key] == value for key, value in filters.items()):
                total += 1
                if limit is None or len(rows) < limit:
                    rows.append(row)
        return [ParsedTable(columns=columns, rows=rows, total_rows=total, extraction="csv")]
    except InvalidFiltersError:
        raise
    except (UnicodeError, csv.Error, ValueError) as exc:
        raise ParseError("TÜİK returned unsupported or malformed CSV") from exc
