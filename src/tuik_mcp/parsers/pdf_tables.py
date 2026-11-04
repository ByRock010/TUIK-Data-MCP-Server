"""Best-effort tables from ruled PDF pages (#8); never a substitute for structured exports.

Only ruled grids are detected: whitespace-aligned text produces too many false tables
in TÜİK publications. Vertically stacked fragments that share column boundaries are
one table whose unshaded rows carry no ruling lines (zebra striping); those rows are
rebuilt from word positions and reported in the table's warnings.
"""

import re
import statistics

from .models import ParsedTable
from .pdf import normalize_text

HEURISTIC = (
    "PDF table extraction is heuristic: verify values against the page text before relying on them."
)
NO_HEADER = (
    "No header row was recognized, so columns are positional; the table may continue "
    "from an earlier page."
)
MAX_TABLES_PER_PAGE = 20
MAX_TABLE_CELLS_PER_PAGE = 10_000
# Vector charts and maps consist of thousands of segments; skip table detection there.
MAX_TABLE_EDGES = 20_000
BOUND_TOLERANCE = 2
CAPTION_DISTANCE = 30
NUMERIC = re.compile(r"[\d\s.,%()+\-–/]+")
IDENTIFIER = re.compile(r"[A-Z0-9_.\n]+")


def cell_text(value: str | None) -> str:
    if value is None:
        return ""
    text = value.strip()
    # Narrow columns wrap long codes such as INT_FAAL_..._REC|ETE without a hyphen.
    if "\n" in text and IDENTIFIER.fullmatch(text):
        text = text.replace("\n", "")
    return " ".join(normalize_text(text).split())


def column_bounds(table) -> list[float]:
    edges = sorted({cell[0] for cell in table.cells} | {table.bbox[2]})
    bounds: list[float] = []
    for x in edges:
        if not bounds or x - bounds[-1] > BOUND_TOLERANCE:
            bounds.append(x)
    return bounds


def same_bounds(a: list[float], b: list[float]) -> bool:
    return len(a) == len(b) and all(
        abs(x - y) <= BOUND_TOLERANCE for x, y in zip(a, b, strict=True)
    )


def text_rows(page, bounds: list[float], top: float, bottom: float, *, aligned: bool):
    """Words between two y positions, grouped into lines and split at column bounds."""
    x0, x1 = bounds[0], bounds[-1]
    top, bottom = max(top, page.bbox[1]), min(bottom, page.bbox[3])
    if bottom - top < 1:
        return []
    words = page.within_bbox((x0, top, x1, bottom)).extract_words()
    lines: list[list[dict]] = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(word["top"] - lines[-1][0]["top"]) <= 3:
            lines[-1].append(word)
        else:
            lines.append([word])
    rows = []
    for line in lines:
        cells = [""] * (len(bounds) - 1)
        for word in sorted(line, key=lambda w: w["x0"]):
            middle = (word["x0"] + word["x1"]) / 2
            index = next(
                (i for i in range(len(cells)) if bounds[i] <= middle < bounds[i + 1]),
                len(cells) - 1,
            )
            cells[index] = f"{cells[index]} {word['text']}".strip()
        # Below the last ruled row, accept only lines that fill several columns.
        if aligned and sum(bool(cell) for cell in cells) < 2:
            break
        rows.append(cells)
    return rows


def row_height(table) -> float:
    heights = [row.bbox[3] - row.bbox[1] for row in table.rows]
    return statistics.median(heights) if heights else 0


def merged_grids(page, tables):
    """Raw rows per table; stacked fragments with equal columns are joined."""
    groups: list[list] = []
    for table in sorted(tables, key=lambda t: (t.bbox[1], t.bbox[0])):
        bounds = column_bounds(table)
        previous = groups[-1][-1] if groups else None
        if (
            previous is not None
            and same_bounds(column_bounds(previous), bounds)
            and 0 <= table.bbox[1] - previous.bbox[3] <= 1.6 * row_height(previous)
        ):
            groups[-1].append(table)
        else:
            groups.append([table])
    grids = []
    for group in groups:
        bounds = column_bounds(group[0])
        rows: list[list[str | None]] = []
        rebuilt = 0
        for index, table in enumerate(group):
            if index:
                gap = text_rows(
                    page, bounds, group[index - 1].bbox[3], table.bbox[1], aligned=False
                )
                rows.extend(gap)
                rebuilt += len(gap)
            rows.extend(table.extract())
        if len(group) > 1:
            last = group[-1]
            tail = text_rows(
                page,
                bounds,
                last.bbox[3],
                last.bbox[3] + 1.5 * row_height(last),
                aligned=True,
            )
            rows.extend(tail)
            rebuilt += len(tail)
        bbox = (bounds[0], group[0].bbox[1], bounds[-1], group[-1].bbox[3])
        grids.append((bbox, rows, rebuilt))
    return grids


def caption(page, bbox) -> str | None:
    top = max(bbox[1] - CAPTION_DISTANCE, page.bbox[1])
    if bbox[1] - top < 1:
        return None
    region = page.within_bbox((page.bbox[0], top, page.bbox[2], bbox[1]))
    lines = [line.strip() for line in (region.extract_text() or "").splitlines()]
    text = next((line for line in reversed(lines) if line), None)
    return " ".join(text.split()) if text and len(text) <= 200 else None


def header_like(cells: list[str]) -> bool:
    return (
        all(cells)
        and len(set(cells)) == len(cells)
        and not any(NUMERIC.fullmatch(cell) for cell in cells)
    )


def page_tables(page, number: int) -> tuple[list[ParsedTable], list[str]]:
    """Tables on one deduplicated pdfplumber page, plus page-level warnings."""
    edges = len(page.edges)
    if edges > MAX_TABLE_EDGES:
        return [], [
            f"Table detection skipped on page {number}: {edges} ruling segments exceed "
            f"the limit ({MAX_TABLE_EDGES}); the page is likely a chart or map."
        ]
    results: list[ParsedTable] = []
    warnings: list[str] = []
    cells_left = MAX_TABLE_CELLS_PER_PAGE
    for bbox, raw, rebuilt in merged_grids(page, page.find_tables()):
        rows = [[cell_text(cell) for cell in row] for row in raw]
        width = max((len(row) for row in rows), default=0)
        if width < 2 or not any(any(row) for row in rows):
            continue  # Decorative boxes and single-column frames are not tables.
        rows = [row + [""] * (width - len(row)) for row in rows]
        # Leading blank rows (an empty merged cell, say) carry neither title nor header.
        while not any(rows[0]):
            rows, raw = rows[1:], raw[1:]
        if len(results) == MAX_TABLES_PER_PAGE:
            warnings.append(
                f"Page {number} has more than {MAX_TABLES_PER_PAGE} tables; the rest were omitted."
            )
            break
        title = None
        spanning = len(raw[0]) > 1 and sum(cell is not None for cell in raw[0]) == 1
        if spanning and len(rows) > 2 and header_like(rows[1]):
            title = next((cell for cell in rows[0] if cell), None)
            rows = rows[1:]
        notes = [HEURISTIC]
        if len(rows) > 1 and header_like(rows[0]):
            columns, data, layout = rows[0], rows[1:], "table"
        else:
            columns = [f"column_{i}" for i in range(1, width + 1)]
            data, layout = rows, "raw"
            notes.append(NO_HEADER)
        if rebuilt:
            notes.append(f"{rebuilt} row(s) without ruling lines were rebuilt from word positions.")
        kept = data[: max(cells_left // width, 0)]
        cells_left -= len(kept) * width
        if len(kept) < len(data):
            notes.append(
                f"Rows were truncated to the per-page budget of "
                f"{MAX_TABLE_CELLS_PER_PAGE} cells; total_rows counts all of them."
            )
        results.append(
            ParsedTable(
                page=number,
                title=title or caption(page, bbox),
                columns=columns,
                rows=[dict(zip(columns, row, strict=True)) for row in kept],
                total_rows=len(data),
                extraction="pdf",
                layout=layout,
                warnings=notes,
            )
        )
    return results, warnings
