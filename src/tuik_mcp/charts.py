"""Server-side PNG charts of CSV/Excel rows (#72).

The rows come from the same retained snapshot and query evaluation as get_tuik_data.
Numbers follow the table-query rules: a column's decimal notation must be
unambiguous, and empty cells or placeholders become gaps, never zeros.
"""

import io
import math
import re
from dataclasses import dataclass, field

from tuik_mcp.models.chart import ChartPoint, ChartSeries
from tuik_mcp.models.query import Comparison
from tuik_mcp.parsers.query import QueryError, converter, missing
from tuik_mcp.utils.normalization import turkish_casefold

MAX_SERIES = 8
MAX_POINTS = 5000
MAX_TICKS = 12
MAX_BAR_POINTS = 60
WIDTH_IN, HEIGHT_IN, DPI = 10.0, 5.6, 110
# Okabe-Ito: distinguishable with common colour-vision deficiencies.
COLORS = ("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#000000", "#F0E442")
COLUMN_SUFFIX = re.compile(r"\s*\[column \d+\]$")
MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("ocak", "january"),
            ("şubat", "february"),
            ("mart", "march"),
            ("nisan", "april"),
            ("mayıs", "may"),
            ("haziran", "june"),
            ("temmuz", "july"),
            ("ağustos", "august"),
            ("eylül", "september"),
            ("ekim", "october"),
            ("kasım", "november"),
            ("aralık", "december"),
        ),
        start=1,
    )
    for name in names
}
# Recognised x labels and their sort keys. Each kind orders only labels of that kind.
TEMPORAL = (
    ("date", re.compile(r"(\d{4})-(\d{2})-(\d{2})")),
    ("month", re.compile(r"(\d{4})(?:-|M)(0[1-9]|1[0-2])")),
    ("quarter", re.compile(r"(\d{4})-?Q([1-4])")),
    ("half", re.compile(r"(\d{4})-?[HS]([12])")),
    ("year", re.compile(r"(\d{4})")),
)
NAMED_MONTH = re.compile(r"(\d{4})\s+([^\W\d_]+)")


@dataclass
class Line:
    name: str
    column: str
    values: list[float | None]
    # Per point: "ok", "missing", "invalid", or None where a series has no row.
    states: list[str | None]

    @property
    def missing(self) -> int:
        return self.states.count("missing")

    @property
    def invalid(self) -> int:
        return self.states.count("invalid")


@dataclass
class Plot:
    labels: list[str]
    lines: list[Line]
    warnings: list[str] = field(default_factory=list)
    order: str = "source"


def temporal_key(label: str) -> tuple[str, tuple[int, ...]] | None:
    """A sortable key for dates/periods, including "2026 Ağustos - August"."""
    text = label.strip()
    for kind, pattern in TEMPORAL:
        match = pattern.fullmatch(text)
        if match:
            return kind, tuple(int(part) for part in match.groups())
    match = NAMED_MONTH.match(text)
    if match and (month := MONTHS.get(turkish_casefold(match.group(2)))):
        return "month", (int(match.group(1)), month)
    return None


def display(name: str, limit: int = 70) -> str:
    """Readable legend text: drop the parser's positional suffix, collapse spaces."""
    text = " ".join(COLUMN_SUFFIX.sub("", name).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def numbers(column: str, values: list[str], decimal_separator: str | None):
    spec = Comparison(value_type="number", decimal_separator=decimal_separator)
    # Percent signs are notation, not values: "12,5%" plots as 12.5.
    cleaned = [value.strip().removesuffix("%").strip() for value in values]
    convert = converter(spec, cleaned, column)
    result, states = [], []
    for value in cleaned:
        if missing(value):
            result.append(None)
            states.append("missing")
            continue
        try:
            result.append(float(convert(value)))
            states.append("ok")
        except QueryError:
            result.append(None)
            states.append("invalid")
    return result, states


def build(
    columns: list[str],
    rows: list[dict[str, str]],
    x: list[str],
    y: list[str],
    *,
    series: str | None = None,
    last: int | None = None,
    decimal_separator: str | None = None,
    labels: list[str] | None = None,
) -> Plot:
    """Turn matched rows into labelled lines; raises QueryError for unusable requests."""
    referenced = [*x, *y, *([series] if series else [])]
    unknown = sorted(set(referenced) - set(columns))
    if unknown:
        raise QueryError("invalid_columns", f"Unknown exact column names: {unknown}")
    if len(set(referenced)) != len(referenced):
        raise QueryError("invalid_query", "x, y and series must name different columns")
    if series and len(y) != 1:
        raise QueryError("invalid_query", "series splits exactly one y column into lines")
    if labels is not None and (series or len(labels) != len(y)):
        raise QueryError("invalid_query", "labels names each y column once; omit it with series")
    if not rows:
        raise QueryError("invalid_query", "No rows match the filters; nothing to chart.")
    warnings = []
    by_row = [" ".join(row[c].strip() for c in x if row[c].strip()) for row in rows]
    if series:
        groups: dict[str, list[int]] = {}
        for index, row in enumerate(rows):
            groups.setdefault(row[series].strip(), []).append(index)
        if len(groups) > MAX_SERIES:
            raise QueryError(
                "chart_limit_exceeded",
                f"{series!r} has {len(groups)} distinct values; filter to at most "
                f"{MAX_SERIES} series.",
            )
        points = list(dict.fromkeys(by_row))
        position = {label: i for i, label in enumerate(points)}
        converted, states = numbers(y[0], [row[y[0]] for row in rows], decimal_separator)
        lines = []
        for name, indexes in groups.items():
            values: list[float | None] = [None] * len(points)
            point_states: list[str | None] = [None] * len(points)
            for i in indexes:
                slot = position[by_row[i]]
                if point_states[slot] is not None:
                    raise QueryError(
                        "invalid_query",
                        f"x label {by_row[i]!r} repeats within series {name!r}; add x "
                        "columns or filters so each point is unique.",
                    )
                values[slot], point_states[slot] = converted[i], states[i]
            lines.append(
                Line(name=display(name or "(boş)"), column=y[0], values=values, states=point_states)
            )
    else:
        points = by_row
        if len(set(points)) != len(points):
            warnings.append("Some x labels repeat; points are plotted in row order.")
        lines = []
        for n, column in enumerate(y):
            values, states = numbers(column, [row[column] for row in rows], decimal_separator)
            lines.append(
                Line(
                    name=display(labels[n]) if labels else display(column),
                    column=column,
                    values=values,
                    states=states,
                )
            )
        names = [line.name for line in lines]
        if not labels and len(set(names)) != len(names):
            # Side-by-side blocks repeat headers; keep the positional suffix to tell them apart.
            for line in lines:
                if names.count(line.name) > 1:
                    line.name = " ".join(line.column.split())[:90]
            warnings.append("y columns share a header; pass labels to name the lines.")
    keys = [temporal_key(label) for label in points]
    order = "source"
    if all(keys) and len({key[0] for key in keys}) == 1:
        # Dates/periods always run oldest to newest, so last is the most recent window
        # even for newest-first sources or a descending query sort.
        order = "chronological"
        index = sorted(range(len(points)), key=lambda i: keys[i][1])
        if index != list(range(len(points))):
            points = [points[i] for i in index]
            for line in lines:
                line.values = [line.values[i] for i in index]
                line.states = [line.states[i] for i in index]
            warnings.append("Rows were not oldest-first; points are plotted chronologically.")
    if last is not None and len(points) > last:
        if order == "source":
            warnings.append(
                "x labels are not recognised dates or periods; last kept the final "
                f"{last} points in row order, not necessarily the most recent."
            )
        points = points[-last:]
        for line in lines:
            line.values, line.states = line.values[-last:], line.states[-last:]
    if len(points) > MAX_POINTS:
        raise QueryError(
            "chart_limit_exceeded",
            f"{len(points)} x points exceed {MAX_POINTS}; use last, filters or predicates.",
        )
    for line in lines:
        if line.invalid:
            warnings.append(f"{line.name}: {line.invalid} non-numeric value(s) were left as gaps.")
    if all(value is None for line in lines for value in line.values):
        raise QueryError("invalid_query", "The selected y columns contain no numeric values.")
    for line in lines:
        if all(value is None for value in line.values):
            warnings.append(f"{line.name}: no numeric values in the plotted range.")
    return Plot(labels=points, lines=lines, warnings=warnings, order=order)


def summarize(plot: Plot) -> list[ChartSeries]:
    summaries = []
    for line in plot.lines:
        points = [
            ChartPoint(x=label, value=value)
            for label, value in zip(plot.labels, line.values, strict=True)
            if value is not None
        ]
        summaries.append(
            ChartSeries(
                name=line.name,
                column=line.column,
                points=len(points),
                missing=line.missing,
                invalid=line.invalid,
                minimum=min(points, key=lambda p: p.value) if points else None,
                maximum=max(points, key=lambda p: p.value) if points else None,
                first=points[0] if points else None,
                last=points[-1] if points else None,
            )
        )
    return summaries


def render(
    plot: Plot, chart: str, title: str, subtitle: str | None, footer: str
) -> tuple[bytes, int, int]:
    """Draw with the object API (no pyplot state), so worker threads stay independent."""
    try:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
        from matplotlib.ticker import FuncFormatter
    except ImportError as exc:
        raise QueryError(
            "visualization_unavailable",
            "Charts need matplotlib: install the viz extra (pip install 'tuik-mcp[viz]').",
        ) from exc
    figure = Figure(figsize=(WIDTH_IN, HEIGHT_IN), dpi=DPI, layout="constrained")
    canvas = FigureCanvasAgg(figure)
    axes = figure.add_subplot()
    count = len(plot.labels)
    positions = list(range(count))
    if chart == "bar":
        width = 0.8 / len(plot.lines)
        for n, line in enumerate(plot.lines):
            offset = (n - (len(plot.lines) - 1) / 2) * width
            axes.bar(
                [p + offset for p in positions],
                [math.nan if v is None else v for v in line.values],
                width=width,
                color=COLORS[n % len(COLORS)],
                label=line.name,
            )
        axes.axhline(0, color="#444444", linewidth=0.8)
    else:
        marker = "o" if count <= 40 else None
        for n, line in enumerate(plot.lines):
            color = COLORS[n % len(COLORS)]
            axes.plot(
                positions,
                [math.nan if v is None else v for v in line.values],
                color=color,
                linewidth=2,
                marker=marker,
                markersize=4,
                label=line.name,
            )
            if len(plot.lines) <= 4:
                annotate_last(axes, line, color)
    step = max(1, math.ceil(count / MAX_TICKS))
    ticks = positions[::step]
    if positions[-1] not in ticks and count > 1:
        ticks = [*ticks[:-1], positions[-1]] if len(ticks) > 1 else [*ticks, positions[-1]]
    axes.set_xticks(ticks)
    tick_labels = [display(plot.labels[i], 28) for i in ticks]
    rotate = len(ticks) > 6 or any(len(label) > 10 for label in tick_labels)
    axes.set_xticklabels(
        tick_labels, rotation=35 if rotate else 0, ha="right" if rotate else "center"
    )
    axes.yaxis.set_major_formatter(FuncFormatter(lambda value, _: turkish_number(value)))
    axes.grid(axis="y", color="#dddddd", linewidth=0.8)
    axes.set_axisbelow(True)
    for side in ("top", "right"):
        axes.spines[side].set_visible(False)
    axes.margins(x=0.02 if chart == "line" else 0.01)
    if len(plot.lines) > 1:
        axes.legend(
            frameon=False,
            fontsize=8.5,
            loc="lower right",
            bbox_to_anchor=(1, 1),
            borderaxespad=0.2,
            ncols=min(4, len(plot.lines)),
        )
    else:
        axes.set_ylabel(plot.lines[0].name, fontsize=9)
    figure.suptitle(display(title, 110), fontsize=13, x=0.01, ha="left", fontweight="bold")
    if subtitle:
        axes.set_title(display(subtitle, 140), fontsize=9, loc="left", color="#555555")
    figure.supxlabel(footer, fontsize=7.5, x=0.01, ha="left", color="#666666")
    figure.set_facecolor("white")
    canvas.draw()
    width, height = canvas.get_width_height()
    return palette_png(canvas.buffer_rgba(), width, height), width, height


def palette_png(rgba, width: int, height: int) -> bytes:
    """PNG with a 256-colour palette: flat-colour charts need a third of the RGBA bytes."""
    from PIL import Image  # A matplotlib dependency.

    image = Image.frombuffer("RGBA", (width, height), rgba, "raw", "RGBA", 0, 1).convert("RGB")
    palette = image.quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    buffer = io.BytesIO()
    palette.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def annotate_last(axes, line: Line, color: str) -> None:
    for index in range(len(line.values) - 1, -1, -1):
        if line.values[index] is not None:
            axes.annotate(
                turkish_number(line.values[index]),
                (index, line.values[index]),
                xytext=(5, 5),
                textcoords="offset points",
                fontsize=8.5,
                color=color,
                fontweight="bold",
            )
            return


def turkish_number(value: float) -> str:
    """Axis labels in Turkish notation: 35.412 and 8,4."""
    if not math.isfinite(value):
        return ""
    if abs(value - round(value)) < 1e-9:
        return f"{round(value):,}".replace(",", ".")
    text = f"{value:,.2f}".rstrip("0").rstrip(".")
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
