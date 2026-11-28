"""Editable PowerPoint briefings rendered from a validated report (#93).

The renderer reads the exact `report.json` bytes of the bundle and never
fetches, recalculates or writes prose: every sentence, value and citation comes
from the validated `Report`. Text stays native, editable PowerPoint text; tables
are native tables; line and bar charts are native charts with their data
embedded in the deck. Every analytical slide carries a compact source, period
and unit note, and its speaker notes carry the full citations.

After rendering, the deck is read back: every table cell and chart value must
equal the report, every internal part reference must resolve and no external
reference (a remote image, a link) may exist.
"""

import io
import math
import unicodedata
from collections.abc import Iterable
from datetime import datetime

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION, XL_MARKER_STYLE
from pptx.enum.dml import MSO_LINE_DASH_STYLE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

from tuik_mcp.models.report import Chart, Evidence, Report, Section

from .templates import TEXT

MAX_REPORT_BYTES = 2 * 1024 * 1024
MAX_SLIDES = 40
MAX_DECK_BYTES = 8 * 1024 * 1024
TABLE_ROWS_PER_SLIDE = 8
FONT = "Calibri"
# Nothing in the deck is smaller than this.
MIN_POINTS = 12
# Exactly 16:9 (13.333 × 7.5 in), the size PowerPoint uses for widescreen.
SLIDE_WIDTH = Emu(12192000)
SLIDE_HEIGHT = Emu(6858000)
MARGIN = Inches(0.5)
CONTENT_WIDTH = SLIDE_WIDTH - 2 * MARGIN
BODY_TOP = Inches(1.35)
FOOTER_TOP = Inches(6.62)
BODY_HEIGHT = FOOTER_TOP - BODY_TOP - Inches(0.1)
INK = RGBColor(0x1F, 0x29, 0x37)
MUTED = RGBColor(0x4B, 0x55, 0x63)
WARNING = RGBColor(0xB4, 0x23, 0x18)
ACCENT = RGBColor(0x1D, 0x4E, 0x89)
# Distinguishable without colour too: lines also differ by marker and dash.
SERIES_COLORS = ("1D4E89", "C2410C", "15803D", "6D28D9")
DASHES = (
    MSO_LINE_DASH_STYLE.SOLID,
    MSO_LINE_DASH_STYLE.DASH,
    MSO_LINE_DASH_STYLE.ROUND_DOT,
    MSO_LINE_DASH_STYLE.LONG_DASH_DOT,
)
MARKERS = (
    XL_MARKER_STYLE.CIRCLE,
    XL_MARKER_STYLE.SQUARE,
    XL_MARKER_STYLE.TRIANGLE,
    XL_MARKER_STYLE.DIAMOND,
)
COLUMN_WIDTHS = (3.9, 1.6, 2.6, 1.2, 3.03)

LABELS = {
    "tr": {
        "messages": "Temel mesajlar",
        "dates": "Son yayım: {release} · Son gözlem: {observation} · Erişim: {retrieved} · "
        "Veri kesimi: {cutoff}",
        "none": "yok",
        "draft": "DOĞRULANMAMIŞ TASLAK",
        "continued": "devam",
        "methods_limitations": "Yöntem ve sınırlılıklar",
        "source": "Kaynak: TÜİK",
        "period": "Dönem",
        "unit": "Birim",
        "retrieved": "Erişim",
        "report": "Rapor",
        "published": "yayım",
        "formula": "Formül",
        "notes_evidence": "Kanıt ve kaynaklar:",
    },
    "en": {
        "messages": "Key messages",
        "dates": "Latest release: {release} · Latest observation: {observation} · "
        "Retrieved: {retrieved} · Data cutoff: {cutoff}",
        "none": "none",
        "draft": "UNVERIFIED DRAFT",
        "continued": "continued",
        "methods_limitations": "Methods and limitations",
        "source": "Source: TurkStat (TÜİK)",
        "period": "Period",
        "unit": "Unit",
        "retrieved": "Retrieved",
        "report": "Report",
        "published": "release",
        "formula": "Formula",
        "notes_evidence": "Evidence and sources:",
    },
}


class RenderError(ValueError):
    """The report cannot be rendered within the deck limits, or read back exactly."""


def plain(text: str | None) -> str:
    """Text safe for the XML of a slide: no control characters, single spaces."""
    cleaned = "".join(
        " " if unicodedata.category(char) in {"Cc", "Cf", "Cs"} else char for char in text or ""
    )
    return " ".join(cleaned.split())


def chunks(
    items: list[str], chars_per_line: int, max_lines: int, first_lines: int | None = None
) -> list[list[str]]:
    """Split bullets into pages by an estimated wrapped line count; never split a bullet.

    `first_lines` is the budget of the first page when it shares its slide.
    """
    pages: list[list[str]] = [[]]
    used = 0.0
    for item in items:
        lines = max(1, math.ceil(len(item) / chars_per_line)) + 0.4
        budget = first_lines if first_lines is not None and len(pages) == 1 else max_lines
        if pages[-1] and used + lines > budget:
            pages.append([])
            used = 0.0
        pages[-1].append(item)
        used += lines
    return pages


def when(value: datetime) -> str:
    return value.date().isoformat()


class Deck:
    def __init__(self, report: Report):
        self.report = report
        self.language = report.language
        self.labels = LABELS[self.language]
        self.words = TEXT[self.language]
        self.by_key = {item.key: item for item in report.evidence}
        self.prs = Presentation()
        self.prs.slide_width = SLIDE_WIDTH
        self.prs.slide_height = SLIDE_HEIGHT
        self.blank = self.prs.slide_layouts[6]
        # What the read-back must find, in slide order.
        self.expected_tables: list[list[list[str]]] = []
        self.expected_charts: list[Chart] = []

    # Building blocks -----------------------------------------------------------

    def slide(self, title: str):
        if len(self.prs.slides) >= MAX_SLIDES:
            raise RenderError(f"The briefing needs more than {MAX_SLIDES} slides")
        slide = self.prs.slides.add_slide(self.blank)
        box = self.text_box(slide, MARGIN, Inches(0.35), CONTENT_WIDTH, Inches(0.85))
        self.paragraph(box.text_frame, title, 28, bold=True, color=ACCENT, first=True)
        box.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        if self.report.status == "draft":
            banner = self.text_box(slide, MARGIN, Inches(0.05), CONTENT_WIDTH, Inches(0.35))
            self.paragraph(
                banner.text_frame,
                self.labels["draft"],
                MIN_POINTS,
                bold=True,
                color=WARNING,
                first=True,
                align=PP_ALIGN.RIGHT,
            )
        return slide

    @staticmethod
    def text_box(slide, left, top, width, height):
        box = slide.shapes.add_textbox(left, top, width, height)
        frame = box.text_frame
        frame.word_wrap = True
        for side in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
            setattr(frame, side, Inches(0.05))
        return box

    @staticmethod
    def paragraph(
        frame,
        text: str,
        size: int,
        *,
        bold: bool = False,
        color: RGBColor = INK,
        first: bool = False,
        align=None,
        prefix: str | None = None,
        bullet: bool = False,
    ):
        paragraph = frame.paragraphs[0] if first else frame.add_paragraph()
        if align is not None:
            paragraph.alignment = align
        paragraph.space_after = Pt(6)
        if bullet:
            run = paragraph.add_run()
            run.text = "• "
            Deck.style(run.font, size, color=color)
        if prefix:
            run = paragraph.add_run()
            run.text = prefix
            Deck.style(run.font, size, bold=True, color=WARNING)
        run = paragraph.add_run()
        run.text = plain(text)
        Deck.style(run.font, size, bold=bold, color=color)
        return paragraph

    @staticmethod
    def style(font, size: int, *, bold: bool = False, color: RGBColor = INK):
        font.name = FONT
        font.size = Pt(max(size, MIN_POINTS))
        font.bold = bold
        font.color.rgb = color

    def bullets(
        self, slide, items: Iterable[tuple[str, str | None]], left, top, width, height, size
    ):
        box = self.text_box(slide, left, top, width, height)
        first = True
        for text, prefix in items:
            self.paragraph(box.text_frame, text, size, first=first, prefix=prefix, bullet=True)
            first = False
        return box

    def footer(self, slide, keys: list[str]):
        items = [self.by_key[k] for k in dict.fromkeys(keys) if k in self.by_key]
        if not items:
            return
        resources = list(dict.fromkeys(item.source.resource_id for item in items))
        shown = ", ".join(resources[:2]) + (f" +{len(resources) - 2}" if len(resources) > 2 else "")
        periods = list(dict.fromkeys(self.period_of(item) for item in items))
        units = list(dict.fromkeys(item.unit_label for item in items if item.unit_label))
        retrieved = sorted({when(item.source.retrieved_at) for item in items})
        span = retrieved[0] if len(retrieved) == 1 else f"{retrieved[0]} – {retrieved[-1]}"
        parts = [
            f"{self.labels['source']} · {shown}",
            f"{self.labels['period']}: {', '.join(periods[:4])}"
            + (" …" if len(periods) > 4 else ""),
            f"{self.labels['unit']}: {', '.join(units[:3]) or self.words['unknown']}",
            f"{self.labels['retrieved']}: {span}",
        ]
        box = self.text_box(slide, MARGIN, FOOTER_TOP, CONTENT_WIDTH, Inches(0.7))
        self.paragraph(box.text_frame, " · ".join(parts), MIN_POINTS, color=MUTED, first=True)

    @staticmethod
    def period_of(item: Evidence) -> str:
        if item.period and item.reference_period:
            return f"{item.reference_period}→{item.period}"
        if item.period:
            return item.period
        return f"{item.period_from}–{item.period_to}"

    def notes(self, slide, keys: list[str], extra: list[str] | None = None):
        lines = list(extra or [])
        items = [self.by_key[k] for k in dict.fromkeys(keys) if k in self.by_key]
        if items:
            lines.append(self.labels["notes_evidence"])
        for item in items:
            source = item.source
            value = item.display or f"{item.status}: {item.reason or self.words['missing']}"
            released = source.publication_date or self.words["unknown"]
            line = (
                f"- {item.label} [{item.key}]: {value}; {self.labels['period']}: "
                f"{self.period_of(item)}; {item.id or ''}; {source.source_url}; "
                f"{self.labels['published']}: {released}; "
                f"{self.labels['retrieved']}: {source.retrieved_at.isoformat()}; "
                f"{source.snapshot_id}"
            )
            if item.formula:
                line += f"; {self.labels['formula']}: {item.formula} [{item.calculation_id}]"
            lines.append(line)
        lines.append(f"{self.labels['report']}: {self.report.report_id}")
        slide.notes_slide.notes_text_frame.text = "\n".join(plain(line) for line in lines)

    def finding_items(self, section: Section) -> list[tuple[str, str | None]]:
        return [
            (f.text, f"{self.words['unverified']}: " if f.status == "unverified" else None)
            for f in section.findings
        ]

    # Slides ------------------------------------------------------------------------

    def title_slide(self, summaries: list[Section]):
        report, labels = self.report, self.labels
        timeline = report.timeline
        unknown = self.words["unknown"]
        slide = self.slide(report.title)
        box = self.text_box(slide, MARGIN, BODY_TOP, CONTENT_WIDTH, Inches(1.1))
        first = True
        for text in (report.subtitle, report.audience):
            if text:
                self.paragraph(box.text_frame, text, 18, color=MUTED, first=first)
                first = False
        retrieved = when(timeline.retrieved_from)
        if when(timeline.retrieved_to) != retrieved:
            retrieved += f" – {when(timeline.retrieved_to)}"
        dates = labels["dates"].format(
            release=timeline.latest_release or unknown,
            observation=timeline.latest_observation or unknown,
            retrieved=retrieved,
            cutoff=timeline.data_cutoff.isoformat() if timeline.data_cutoff else labels["none"],
        )
        self.paragraph(box.text_frame, dates, 14, color=MUTED, first=first)
        messages = [item for section in summaries for item in self.finding_items(section)]
        keys = [k for section in summaries for f in section.findings for k in f.evidence]
        pages = chunks([text for text, _ in messages], 110, 14, first_lines=9) if messages else [[]]
        offset = 0
        for number, page in enumerate(pages):
            target = (
                slide
                if number == 0
                else self.slide(f"{labels['messages']} ({labels['continued']})")
            )
            top = BODY_TOP
            if number == 0 and page:
                heading = self.text_box(target, MARGIN, Inches(2.6), CONTENT_WIDTH, Inches(0.5))
                self.paragraph(heading.text_frame, labels["messages"], 20, bold=True, first=True)
                top = Inches(3.15)
            if page:
                self.bullets(
                    target,
                    messages[offset : offset + len(page)],
                    MARGIN,
                    top,
                    CONTENT_WIDTH,
                    FOOTER_TOP - top - Inches(0.1),
                    18,
                )
            offset += len(page)
            self.footer(target, keys)
            self.notes(target, keys, [report.message])

    def findings_slide(self, section: Section):
        items = self.finding_items(section)
        keys = [k for f in section.findings for k in f.evidence]
        for number, page in enumerate(chunks([t for t, _ in items], 110, 14)):
            start = sum(len(p) for p in chunks([t for t, _ in items], 110, 14)[:number])
            title = (
                section.title if number == 0 else f"{section.title} ({self.labels['continued']})"
            )
            slide = self.slide(title)
            self.bullets(
                slide,
                items[start : start + len(page)],
                MARGIN,
                BODY_TOP,
                CONTENT_WIDTH,
                BODY_HEIGHT,
                18,
            )
            self.footer(slide, keys)
            self.notes(slide, keys)

    def table_slide(self, section: Section):
        table = section.table
        rows = [[r.label, r.period or "", r.value or "", r.status, r.id or ""] for r in table.rows]
        pages = [
            rows[i : i + TABLE_ROWS_PER_SLIDE] for i in range(0, len(rows), TABLE_ROWS_PER_SLIDE)
        ]
        keys = [r.evidence for r in table.rows]
        for number, page in enumerate(pages or [[]]):
            title = (
                section.title if number == 0 else f"{section.title} ({self.labels['continued']})"
            )
            slide = self.slide(title)
            height = Inches(0.45) * (len(page) + 1)
            shape = slide.shapes.add_table(
                len(page) + 1, len(table.columns), MARGIN, BODY_TOP, CONTENT_WIDTH, height
            )
            grid = shape.table
            for index, width in enumerate(COLUMN_WIDTHS[: len(table.columns)]):
                grid.columns[index].width = Inches(width)
            for column, name in enumerate(table.columns):
                self.cell(grid.cell(0, column), name, bold=True)
            for row_number, row in enumerate(page, 1):
                for column, text in enumerate(row):
                    self.cell(
                        grid.cell(row_number, column),
                        text,
                        align=PP_ALIGN.RIGHT if column == 2 else None,
                    )
            self.expected_tables.append([list(table.columns), *page])
            page_keys = [
                r.evidence for r in table.rows[number * TABLE_ROWS_PER_SLIDE :][: len(page)]
            ]
            self.footer(slide, page_keys)
            self.notes(slide, page_keys or keys)

    def cell(self, cell, text: str, *, bold: bool = False, align=None):
        frame = cell.text_frame
        frame.word_wrap = True
        paragraph = frame.paragraphs[0]
        if align is not None:
            paragraph.alignment = align
        run = paragraph.add_run()
        run.text = plain(text)
        self.style(
            run.font,
            13 if not bold else 14,
            bold=bold,
            color=RGBColor(0xFF, 0xFF, 0xFF) if bold else INK,
        )

    def chart_slide(self, section: Section):
        chart = section.chart
        slide = self.slide(section.title)
        periods = sorted({p.period for line in chart.series for p in line.points})
        values = {
            (line.evidence, p.period): float(p.value)
            if p.value is not None and p.status == "valid"
            else None
            for line in chart.series
            for p in line.points
        }
        decimals = max(
            (
                self.by_key[line.evidence].decimals
                for line in chart.series
                if line.evidence in self.by_key
            ),
            default=1,
        )
        number_format = "0" if decimals == 0 else "0." + "0" * min(decimals, 4)
        data = CategoryChartData(number_format=number_format)
        data.categories = periods
        for line in chart.series:
            data.add_series(
                plain(line.label), [values.get((line.evidence, period)) for period in periods]
            )
        kind = (
            XL_CHART_TYPE.LINE_MARKERS if chart.type == "line" else XL_CHART_TYPE.COLUMN_CLUSTERED
        )
        frame = slide.shapes.add_chart(kind, MARGIN, BODY_TOP, CONTENT_WIDTH, Inches(4.1), data)
        frame.name = "Chart: " + plain(section.title)[:80]
        # Alt text that screen readers and image fallbacks read.
        frame._element.nvGraphicFramePr.cNvPr.set("descr", plain(chart.alt_text)[:1000])
        graph = frame.chart
        graph.font.name = FONT
        graph.font.size = Pt(MIN_POINTS)
        graph.has_title = False
        graph.has_legend = True
        graph.legend.position = XL_LEGEND_POSITION.BOTTOM
        graph.legend.include_in_layout = False
        axis = graph.value_axis
        axis.has_major_gridlines = True
        axis.tick_labels.font.size = Pt(MIN_POINTS)
        axis.has_title = True
        axis.axis_title.text_frame.text = plain(chart.unit_label or self.words["unknown"])
        self.style(axis.axis_title.text_frame.paragraphs[0].runs[0].font, MIN_POINTS, color=MUTED)
        graph.category_axis.tick_labels.font.size = Pt(MIN_POINTS)
        for index, plot_series in enumerate(graph.plots[0].series):
            color = RGBColor.from_string(SERIES_COLORS[index % len(SERIES_COLORS)])
            if chart.type == "line":
                plot_series.format.line.color.rgb = color
                plot_series.format.line.width = Pt(2.25)
                plot_series.format.line.dash_style = DASHES[index % len(DASHES)]
                marker = plot_series.marker
                marker.style = MARKERS[index % len(MARKERS)]
                marker.size = 7
                marker.format.fill.solid()
                marker.format.fill.fore_color.rgb = color
                marker.format.line.color.rgb = color
                plot_series.smooth = False
            else:
                plot_series.format.fill.solid()
                plot_series.format.fill.fore_color.rgb = color
        # Gaps stay gaps: never interpolated or drawn as zero.
        blanks = graph._chartSpace.chart.find(qn("c:dispBlanksAs"))
        if blanks is None:
            blanks = graph._chartSpace.chart.makeelement(qn("c:dispBlanksAs"), {})
            graph._chartSpace.chart.append(blanks)
        blanks.set("val", "gap")
        alt = self.text_box(slide, MARGIN, BODY_TOP + Inches(4.2), CONTENT_WIDTH, Inches(0.9))
        self.paragraph(alt.text_frame, chart.alt_text, MIN_POINTS, color=MUTED, first=True)
        self.expected_charts.append(chart)
        keys = [line.evidence for line in chart.series]
        self.footer(slide, keys)
        self.notes(slide, keys, [chart.alt_text])

    def list_slides(self, title: str, columns: list[tuple[str, list[str]]]):
        """One or two titled bullet columns, continued over as many slides as needed."""
        width = CONTENT_WIDTH if len(columns) == 1 else (CONTENT_WIDTH - Inches(0.3)) / 2
        per_line = 130 if len(columns) == 1 else 66
        paged = [chunks(items, per_line, 19) if items else [[]] for _, items in columns]
        for number in range(max(len(pages) for pages in paged)):
            slide = self.slide(title if number == 0 else f"{title} ({self.labels['continued']})")
            for index, ((heading, _), pages) in enumerate(zip(columns, paged, strict=True)):
                left = MARGIN + index * (width + Inches(0.3))
                if len(columns) > 1:
                    head = self.text_box(slide, left, BODY_TOP, width, Inches(0.45))
                    self.paragraph(head.text_frame, heading, 18, bold=True, first=True)
                page = pages[number] if number < len(pages) else []
                top = BODY_TOP + (Inches(0.5) if len(columns) > 1 else 0)
                if page:
                    self.bullets(
                        slide,
                        [(text, None) for text in page],
                        left,
                        top,
                        width,
                        FOOTER_TOP - top,
                        14,
                    )
            self.notes(slide, [], [line for _, items in columns for line in items])

    def build(self) -> bytes:
        sections = self.report.sections
        self.title_slide([s for s in sections if s.kind == "summary"])
        rest = [s for s in sections if s.kind != "summary"]
        index = 0
        while index < len(rest):
            section = rest[index]
            following = rest[index + 1] if index + 1 < len(rest) else None
            if (
                section.kind == "methods"
                and following is not None
                and following.kind == "limitations"
            ):
                self.list_slides(
                    self.labels["methods_limitations"],
                    [(section.title, section.items), (following.title, following.items)],
                )
                index += 2
                continue
            if section.findings:
                self.findings_slide(section)
            if section.table is not None:
                self.table_slide(section)
            if section.chart is not None:
                self.chart_slide(section)
            if section.items:
                self.list_slides(section.title, [(section.title, section.items)])
            index += 1
        self.style_tables()
        output = io.BytesIO()
        self.prs.core_properties.title = plain(self.report.title)[:255]
        self.prs.core_properties.subject = self.report.report_id
        self.prs.core_properties.language = "tr-TR" if self.language == "tr" else "en-US"
        # Fixed timestamps: the same report renders the same deck.
        stamp = self.report.timeline.retrieved_to.replace(tzinfo=None, microsecond=0)
        self.prs.core_properties.created = stamp
        self.prs.core_properties.modified = stamp
        self.prs.core_properties.last_modified_by = "tuik-mcp"
        self.prs.core_properties.author = "tuik-mcp"
        self.prs.save(output)
        return output.getvalue()

    def style_tables(self):
        for slide in self.prs.slides:
            for shape in slide.shapes:
                if shape.has_table:
                    for cell in shape.table.rows[0].cells:
                        cell.fill.solid()
                        cell.fill.fore_color.rgb = ACCENT


def render_pptx(document: bytes) -> bytes:
    """A PowerPoint deck of the validated report whose JSON is `document`."""
    if len(document) > MAX_REPORT_BYTES:
        raise RenderError(f"report.json exceeds {MAX_REPORT_BYTES} bytes")
    report = Report.model_validate_json(document)
    if report.status == "rejected":
        raise RenderError("A rejected report has no briefing")
    deck = Deck(report)
    content = deck.build()
    if len(content) > MAX_DECK_BYTES:
        raise RenderError(f"The briefing exceeds {MAX_DECK_BYTES} bytes")
    verify(content, deck.expected_tables, deck.expected_charts)
    return content


def verify(content: bytes, tables: list[list[list[str]]], charts: list[Chart]) -> None:
    """Read the saved deck back; any difference from the report is an error."""
    prs = Presentation(io.BytesIO(content))
    found_tables, found_charts = [], []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_table:
                found_tables.append([[cell.text for cell in row.cells] for row in shape.table.rows])
            if shape.has_chart:
                found_charts.append(shape.chart)
    expected = [[[plain(c) for c in row] for row in table] for table in tables]
    if found_tables != expected:
        raise RenderError("A table cell differs from the report after read-back")
    if len(found_charts) != len(charts):
        raise RenderError("A chart is missing after read-back")
    for graph, chart in zip(found_charts, charts, strict=True):
        periods = sorted({p.period for line in chart.series for p in line.points})
        if list(graph.plots[0].categories) != periods:
            raise RenderError("Chart categories differ from the report")
        series = list(graph.plots[0].series)
        if [s.name for s in series] != [plain(line.label) for line in chart.series]:
            raise RenderError("Chart series names differ from the report")
        for plotted, line in zip(series, chart.series, strict=True):
            points = {p.period: p for p in line.points}
            wanted = [
                float(points[p].value)
                if p in points and points[p].value is not None and points[p].status == "valid"
                else None
                for p in periods
            ]
            if list(plotted.values) != wanted:
                raise RenderError(f"Chart values of {line.evidence} differ from the report")
        if graph.part.chart_workbook.xlsx_part is None:
            raise RenderError("A chart has no embedded data")
    seen = set()
    stack = [prs.part]
    while stack:
        part = stack.pop()
        if part.partname in seen:
            continue
        seen.add(part.partname)
        for rel in part.rels.values():
            if rel.is_external:
                raise RenderError(f"The deck references an external target: {rel.target_ref}")
            stack.append(rel.target_part)
    if Emu(prs.slide_width) != SLIDE_WIDTH:
        raise RenderError("Unexpected slide size")
