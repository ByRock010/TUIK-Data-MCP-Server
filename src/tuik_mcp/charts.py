"""Validated chart data and raster/vector renderers for CSV/Excel rows.

The rows come from the same retained snapshot and query evaluation as get_tuik_data.
Numbers follow the table-query rules: a column's decimal notation must be
unambiguous, and empty cells or placeholders become gaps, never zeros.
"""

import io
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from textwrap import fill
from threading import Lock
from unicodedata import category

from tuik_mcp.models.chart import (
    MAX_BAR_POINTS as MAX_BAR_POINTS,
)
from tuik_mcp.models.chart import (
    MAX_POINTS,
    MAX_SERIES,
    ChartFormat,
    ChartKind,
    ChartOptions,
    ChartOrder,
    ChartPoint,
    ChartSeries,
    ChartSpec,
    ChartSpecSeries,
    PointState,
)
from tuik_mcp.models.query import Comparison
from tuik_mcp.parsers.query import QueryError, converter, missing
from tuik_mcp.utils.normalization import turkish_casefold

MAX_TICKS = 12
MAX_SPEC_BYTES = 2 * 1024 * 1024
MAX_RENDER_BYTES = {"png": 512 * 1024, "svg": 2 * 1024 * 1024, "pdf": 2 * 1024 * 1024}
WIDTH_IN, HEIGHT_IN, DPI = 10.0, 5.6, 110
# Okabe-Ito: distinguishable with common colour-vision deficiencies.
COLORS = ("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#000000", "#F0E442")
DASHES = ("-", "--", "-.", ":", (0, (6, 2, 1, 2, 1, 2)), (0, (4, 1)), (0, (1, 1)), (0, (2, 3)))
MARKERS = ("o", "s", "^", "D", "v", "P", "<", "X")
HATCHES = ("", "//", "..", "xx", "\\\\", "++", "oo", "--")
RENDER_LOCK = Lock()
PERCENT_UNITS = {"%", "percent", "yüzde"}
PERCENT_HEADER = re.compile(r"\(\s*%\s*\)")
AGE_SCOPE = re.compile(r"^\d+\s*\+?\s*(?:yaş|age)\b")
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
    states: list[PointState | None]
    percent: bool = False

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
    order: ChartOrder = "source"


def temporal_key(label: str) -> tuple[str, tuple[int, ...]] | None:
    """A sortable key for dates/periods, including "2026 Ağustos - August"."""
    text = label.strip()
    for kind, pattern in TEMPORAL:
        match = pattern.fullmatch(text)
        if match:
            if kind == "date":
                try:
                    date.fromisoformat(text)
                except ValueError:
                    return None
            return kind, tuple(int(part) for part in match.groups())
    match = NAMED_MONTH.match(text)
    if match and (month := MONTHS.get(turkish_casefold(match.group(2)))):
        return "month", (int(match.group(1)), month)
    return None


def display(name: str, limit: int = 70) -> str:
    """Readable legend text: drop the parser's positional suffix, collapse spaces."""
    text = plain_text(COLUMN_SUFFIX.sub("", name))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def plain_text(text: str) -> str:
    return " ".join(
        "".join(
            " " if char.isspace() else char
            for char in text
            if char.isspace() or category(char) not in {"Cc", "Cf", "Cs"}
        ).split()
    )


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
            number = float(convert(value))
        except (QueryError, OverflowError):
            number = math.nan
        if not math.isfinite(number) or abs(number) > 1e100:
            result.append(None)
            states.append("invalid")
        else:
            result.append(number)
            states.append("ok")
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
    scatter: bool = False,
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
        if scatter:
            # Repeated numeric x values are valid. Align the nth copy of a coordinate
            # between groups while keeping every occurrence within each group.
            point_keys: list[tuple[str, int]] = [("", 0)] * len(rows)
            for indexes in groups.values():
                seen: Counter[str] = Counter()
                for index in indexes:
                    label = by_row[index]
                    point_keys[index] = (label, seen[label])
                    seen[label] += 1
        else:
            point_keys = [(label, 0) for label in by_row]
        position = {key: i for i, key in enumerate(dict.fromkeys(point_keys))}
        points = [label for label, _ in position]
        converted, states = numbers(y[0], [row[y[0]] for row in rows], decimal_separator)
        lines = []
        for name, indexes in groups.items():
            values: list[float | None] = [None] * len(points)
            point_states: list[str | None] = [None] * len(points)
            for i in indexes:
                slot = position[point_keys[i]]
                if point_states[slot] is not None:
                    raise QueryError(
                        "invalid_query",
                        f"x label {by_row[i]!r} repeats within series {name!r}; add x "
                        "columns or filters so each point is unique.",
                    )
                values[slot], point_states[slot] = converted[i], states[i]
            lines.append(
                Line(
                    name=display(name or "(boş)"),
                    column=y[0],
                    values=values,
                    states=point_states,
                    percent=any(rows[i][y[0]].strip().endswith("%") for i in indexes),
                )
            )
    else:
        points = by_row
        if not scatter and len(set(points)) != len(points):
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
                    percent=any(row[column].strip().endswith("%") for row in rows),
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
    if not scatter and all(keys) and len({key[0] for key in keys}) == 1:
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


def prepare_spec(
    plot: Plot,
    chart: ChartKind,
    options: ChartOptions,
    *,
    title: str,
    x: list[str],
    source_unit: str | None,
    resource_id: str,
    source_url: str,
    retrieved_at: datetime,
    snapshot_id: str,
    decimal_separator: str | None,
) -> ChartSpec:
    unit = plain_text(source_unit) if source_unit else None
    # Some publication sheets put a population scope in the bracketed subtitle.
    if unit and AGE_SCOPE.match(turkish_casefold(unit)):
        unit = None
    unit_source = "source_metadata" if unit else "unknown"
    if options.series_units is not None:
        units = options.series_units
        if len(units) != len(plot.lines) or len({turkish_casefold(u) for u in units}) != 1:
            raise ValueError("Declare the same unit once per plotted series. No secondary axis.")
        if unit and turkish_casefold(unit) != turkish_casefold(units[0]):
            raise ValueError("Declared series units disagree with the source unit")
        unit = units[0]
        if unit_source == "unknown":
            unit_source = "declared"
    percent_series = [
        line.percent or bool(PERCENT_HEADER.search(line.column)) for line in plot.lines
    ]
    if any(percent_series):
        if unit and turkish_casefold(unit) not in PERCENT_UNITS:
            raise ValueError("Percent-formatted data disagree with the common series unit")
        if all(percent_series):
            unit = "%"
            unit_source = "source_metadata"
        elif unit is None:
            raise ValueError("Declare a common percent unit for all selected series")

    numeric_x = None
    if chart == "scatter":
        numeric_x, _ = numbers(x[0], plot.labels, decimal_separator)
        if any(value is None for value in numeric_x):
            raise ValueError("Scatter requires a valid numeric x value on every selected row")
        if any(label.strip().endswith("%") for label in plot.labels) and (
            options.x_unit is None or turkish_casefold(options.x_unit) not in PERCENT_UNITS
        ):
            raise ValueError("Percentage scatter x values require an explicit percent x_unit")
    if chart == "ranked_bar" and len(plot.lines) == 1:
        line = plot.lines[0]
        index = sorted(
            range(len(plot.labels)),
            key=lambda i: (line.values[i] is None, -(line.values[i] or 0)),
        )
        plot.labels = [plot.labels[i] for i in index]
        line.values = [line.values[i] for i in index]
        line.states = [line.states[i] for i in index]
        plot.order = "value_descending"
    breaks = (
        regular_breaks(plot.labels) if chart == "line" and plot.order == "chronological" else []
    )

    counts = ". ".join(
        f"{line.name} has {len(line.values) - line.values.count(None)} values "
        f"and {line.values.count(None)} gaps"
        for line in plot.lines
    )
    alt = (
        f"{chart.replace('_', ' ').capitalize()} of {plain_text(title)}. "
        f"{len(plot.labels)} x positions."
    )
    alt += f" Unit: {unit}." if unit else " Unit: unspecified."
    alt += {
        "chronological": " Oldest to newest.",
        "value_descending": " Highest to lowest, with ties in their original order.",
    }.get(plot.order, "")
    alt += f" {counts}."
    if breaks:
        alt += f" Unobserved calendar intervals break the line at {len(breaks)} positions."
    if chart == "scatter":
        alt += " Points describe an association and do not establish causation."
    if chart == "heatmap":
        alt += " Gray cells marked × are missing or invalid values."
    elif chart in {"bar", "horizontal_bar", "ranked_bar"} and any(
        None in line.values for line in plot.lines
    ):
        alt += " Marks × at the baseline indicate gaps."
    return ChartSpec(
        chart=chart,
        theme=options.theme,
        title=title,
        x_columns=x,
        x_labels=plot.labels,
        x_values=numeric_x,
        x_unit=options.x_unit,
        x_order=plot.order,
        break_before=breaks,
        series=[
            ChartSpecSeries(
                name=line.name, column=line.column, values=line.values, states=line.states
            )
            for line in plot.lines
        ],
        unit=unit,
        unit_source=unit_source,
        resource_id=resource_id,
        source_url=source_url,
        retrieved_at=retrieved_at,
        snapshot_id=snapshot_id,
        source_note=options.source_note,
        alt_text=alt,
    )


def regular_breaks(labels: list[str]) -> list[int]:
    scale = {"month": 12, "quarter": 4, "half": 2, "year": 1}
    keys = [temporal_key(label) for label in labels]
    if not keys or keys[0] is None or keys[0][0] not in scale:
        return []
    kind = keys[0][0]
    if any(key is None or key[0] != kind for key in keys):
        return []
    ordinals = [
        parts[0] * scale[kind] + parts[1] if len(parts) > 1 else parts[0] for _, parts in keys
    ]
    return [i for i in range(1, len(ordinals)) if ordinals[i] - ordinals[i - 1] > 1]


def source_caption(source_url: str, retrieved_at: datetime) -> str:
    return f"Kaynak: TÜİK · {source_url[:120]} · Erişim: {retrieved_at.date().isoformat()}"


def render_spec(spec: ChartSpec, output: ChartFormat) -> tuple[bytes, int, int]:
    """Render the validated saved specification without the source table or network."""
    caption = source_caption(spec.source_url, spec.retrieved_at)
    return render(spec, spec.chart, spec.title, spec.unit, caption, output=output)


def render(
    plot: Plot | ChartSpec,
    chart: str,
    title: str,
    subtitle: str | None,
    footer: str,
    *,
    output: ChartFormat = "png",
) -> tuple[bytes, int, int]:
    """Draw with the object API (no pyplot state), so worker threads stay independent."""
    try:
        from matplotlib import rc_context
    except ImportError as exc:
        raise QueryError(
            "visualization_unavailable",
            "Charts need matplotlib: install this repository's wheel with [viz] "
            "(or pip install -e '.[viz]' from its checkout); see docs/INSTALLATION.md.",
        ) from exc
    # Vector backends read global font and metadata settings during drawing.
    with (
        RENDER_LOCK,
        rc_context(
            {
                "font.family": ["DejaVu Sans", "sans-serif"],
                "text.parse_math": False,
                "text.usetex": False,
                "pdf.fonttype": 42,
                "svg.fonttype": "none",
                "svg.hashsalt": "tuik.chart-spec/1",
            }
        ),
    ):
        result = draw(plot, chart, title, subtitle, footer, output)
    if len(result[0]) > MAX_RENDER_BYTES[output]:
        raise QueryError("chart_limit_exceeded", f"{output.upper()} exceeds the chart byte budget")
    return result


def draw(plot, chart, title, subtitle, footer, output) -> tuple[bytes, int, int]:
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.ticker import FuncFormatter

    spec = plot if isinstance(plot, ChartSpec) else None
    labels = spec.x_labels if spec else plot.labels
    lines = spec.series if spec else plot.lines
    colors = ("#252525",) * MAX_SERIES if spec and spec.theme == "monochrome" else COLORS
    figure = Figure(figsize=(WIDTH_IN, HEIGHT_IN), dpi=DPI, layout="constrained")
    canvas = FigureCanvasAgg(figure)
    axes = figure.add_subplot()
    count = len(labels)
    positions = list(range(count))
    horizontal = chart in {"horizontal_bar", "ranked_bar"}
    if chart in {"bar", "horizontal_bar", "ranked_bar"}:
        width = 0.8 / len(lines)
        for n, line in enumerate(lines):
            offset = (n - (len(lines) - 1) / 2) * width
            centers = [p + offset for p in positions]
            style = {"height": width} if horizontal else {"width": width}
            bar = axes.barh if horizontal else axes.bar
            bar(
                centers,
                [math.nan if v is None else v for v in line.values],
                **style,
                color=colors[n],
                label=line.name,
                hatch=HATCHES[n] if spec and len(lines) > 1 else None,
                edgecolor="white" if spec and len(lines) > 1 else None,
            )
            if spec:
                for center, value in zip(centers, line.values, strict=True):
                    if value is None:
                        point = (0, center) if horizontal else (center, 0)
                        axes.annotate(
                            "×", point, ha="center", va="center", color="#555555", zorder=3
                        )
        baseline = axes.axvline if horizontal else axes.axhline
        baseline(0, color="#444444", linewidth=0.8)
        if horizontal:
            axes.invert_yaxis()
    elif chart == "scatter":
        for n, line in enumerate(lines):
            axes.scatter(
                spec.x_values,
                [math.nan if value is None else value for value in line.values],
                marker=MARKERS[n],
                color=colors[n],
                label=line.name,
                s=20,
                alpha=0.85,
            )
        x_name = display(spec.x_columns[0])
        axes.set_xlabel(f"{x_name} ({spec.x_unit})" if spec.x_unit else x_name)
        axes.xaxis.set_major_formatter(FuncFormatter(lambda value, _: turkish_number(value)))
    elif chart == "heatmap":
        from matplotlib import colormaps
        from numpy import ma

        palette = colormaps["Greys" if spec.theme == "monochrome" else "cividis"].with_extremes(
            bad="#dddddd"
        )
        values = [[math.nan if v is None else v for v in line.values] for line in lines]
        heat = axes.pcolormesh(
            [i - 0.5 for i in range(count + 1)],
            [i - 0.5 for i in range(len(lines) + 1)],
            ma.masked_invalid(values),
            cmap=palette,
        )
        for row, line in enumerate(lines):
            for column, value in enumerate(line.values):
                if value is None:
                    axes.text(column, row, "×", ha="center", va="center", color="#454545")
        axes.invert_yaxis()
        axes.set_yticks(range(len(lines)), [display(line.name, 30) for line in lines])
        scale = figure.colorbar(heat, ax=axes, format=FuncFormatter(lambda v, _: turkish_number(v)))
        scale.solids.set_rasterized(False)
        if subtitle:
            scale.set_label(subtitle)
        axes.set_xlabel("×: eksik veya geçersiz / missing or invalid")
    else:
        marker = "o" if count <= 40 else None
        for n, line in enumerate(lines):
            color = colors[n]
            xs, ys = [], []
            breaks = set(spec.break_before) if spec else set()
            for i, value in enumerate(line.values):
                if i in breaks:
                    xs.append(i)
                    ys.append(math.nan)
                xs.append(i)
                ys.append(math.nan if value is None else value)
            axes.plot(
                xs,
                ys,
                color=color,
                linewidth=2,
                linestyle=DASHES[n] if spec else "-",
                marker=MARKERS[n] if spec and marker else marker,
                markersize=4,
                label=line.name,
            )
            if len(lines) <= 4 and not (spec and len(lines) > 1):
                annotate_last(axes, line, color)
    step = max(1, math.ceil(count / MAX_TICKS))
    ticks = positions[::step]
    if positions[-1] not in ticks and count > 1:
        ticks = [*ticks[:-1], positions[-1]] if len(ticks) > 1 else [*ticks, positions[-1]]
    if horizontal:
        axes.set_yticks(positions, [display(label, 36) for label in labels], fontsize=8)
    elif chart != "scatter":
        tick_labels = [display(labels[i], 28) for i in ticks]
        rotate = len(ticks) > 6 or any(len(label) > 10 for label in tick_labels)
        axes.set_xticks(
            ticks, tick_labels, rotation=35 if rotate else 0, ha="right" if rotate else "center"
        )
    if chart != "heatmap":
        value_axis = axes.xaxis if horizontal else axes.yaxis
        value_axis.set_major_formatter(FuncFormatter(lambda value, _: turkish_number(value)))
        axes.grid(axis="x" if horizontal else "y", color="#dddddd", linewidth=0.8)
    axes.set_axisbelow(True)
    for side in ("top", "right"):
        axes.spines[side].set_visible(False)
    axes.margins(x=0.02 if chart == "line" else 0.01)
    if chart != "heatmap" and len(lines) > 1:
        axes.legend(
            frameon=False,
            fontsize=8.5,
            loc="lower right",
            bbox_to_anchor=(1, 1),
            borderaxespad=0.2,
            ncols=min(2 if spec else 4, len(lines)),
        )
    elif chart != "heatmap":
        axis_label = axes.set_xlabel if horizontal else axes.set_ylabel
        axis_label(display(lines[0].name, 54), fontsize=9)
    heading = fill(plain_text(title), 85) if spec else display(title, 110)
    figure.suptitle(heading, fontsize=13, x=0.015, ha="left", fontweight="bold")
    if subtitle and chart != "heatmap":
        axes.set_title(display(subtitle, 140), fontsize=9, loc="left", color="#555555")
    note = spec.source_note if spec else None
    caption = fill(plain_text(footer), 120) if spec else plain_text(footer)
    if note:
        caption += "\n" + fill(plain_text(note), 120)
    figure.supxlabel(caption, fontsize=7.5, x=0.015, ha="left", color="#555555")
    figure.set_facecolor("white")
    if output != "png":
        buffer = io.BytesIO()
        metadata = (
            {"Date": None, "Title": plain_text(title), "Description": plain_text(spec.alt_text)}
            if output == "svg"
            else {
                "CreationDate": None,
                "ModDate": None,
                "Title": plain_text(title),
                "Subject": plain_text(spec.alt_text),
            }
        )
        canvas.print_figure(buffer, format=output, metadata=metadata)
        data = buffer.getvalue()
        if output == "svg":
            # The standalone file does not need a remote SVG DTD.
            data = re.sub(rb"<!DOCTYPE svg[^>]+>\s*", b"", data, count=1)
        return data, *canvas.get_width_height()
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


def annotate_last(axes, line: Line | ChartSpecSeries, color: str) -> None:
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
    if abs(value) >= 10_000_000 or 0 < abs(value) < 0.01:
        return f"{value:.2g}".replace(".", ",")
    if abs(value - round(value)) < 1e-9:
        return f"{round(value):,}".replace(",", ".")
    text = f"{value:,.2f}".rstrip("0").rstrip(".")
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
