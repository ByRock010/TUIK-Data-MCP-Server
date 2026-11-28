"""Editable slides from a resolved Report. This optional module performs no I/O."""

from io import BytesIO
from textwrap import wrap
from urllib.parse import urlsplit

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.text import MSO_ANCHOR
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

from tuik_mcp.models.report import SCHEMA_VERSION, Report

INK = RGBColor.from_string("162942")
BLUE = RGBColor.from_string("135989")
MUTED = RGBColor.from_string("3E5168")
WHITE = RGBColor.from_string("FFFFFF")
PAPER = RGBColor.from_string("F2F6FA")
COLORS = ("135989", "A65518", "297268", "794B88")
WIDTH, HEIGHT = 13.333, 7.5
MAX_SLIDES = 96
CHART_PERIODS = 16
FONT = "DejaVu Sans"

LABELS = {
    "tr": {
        "cutoff": "Veri kesimi",
        "unknown": "belirtilmedi",
        "source": "TÜİK",
        "missing": "Değer yok",
        "draft": "DOĞRULANMAMIŞ TASLAK",
        "source_details": "Kaynak ve yöntem",
        "derived": "Hesaplanan değer",
    },
    "en": {
        "cutoff": "Data cutoff",
        "unknown": "unspecified",
        "source": "TÜİK",
        "missing": "No value",
        "draft": "UNVERIFIED DRAFT",
        "source_details": "Sources and methods",
        "derived": "Calculated value",
    },
}


def _font(font, size, *, color=INK, bold=False):
    font.name = FONT
    font.size = Pt(size)
    font.bold = bold
    font.color.rgb = color


def _textbox(slide, x, y, w, h, text, size, *, color=INK, bold=False, link=None):
    frame = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h)).text_frame
    frame.word_wrap = True
    frame.margin_left = frame.margin_right = Inches(0)
    frame.margin_top = frame.margin_bottom = Inches(0)
    paragraph = frame.paragraphs[0]
    run = paragraph.add_run()
    run.text = text
    if link:
        run.hyperlink.address = link
    _font(run.font, size, color=color, bold=bold)


def _lines(text, width):
    return sum(max(1, len(wrap(line, width))) for line in text.splitlines()) or 1


def _batches(items, capacity):
    page, used = [], 0
    for item, lines in items:
        if page and used + lines > capacity:
            yield page
            page, used = [], 0
        page.append((item, lines))
        used += lines
    if page:
        yield page


def _official_url(value):
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme == "https"
            and parsed.hostname
            and (parsed.hostname == "tuik.gov.tr" or parsed.hostname.endswith(".tuik.gov.tr"))
        ):
            return value
    except ValueError:
        pass
    return None


class Briefing:
    def __init__(self, report):
        self.report = report
        self.word = LABELS[report.language]
        self.evidence = {item.key: item for item in report.evidence}
        self.deck = Presentation()
        self.deck.slide_width, self.deck.slide_height = Inches(WIDTH), Inches(HEIGHT)
        self.deck.core_properties.title = report.title

    def slide(self, title, *, cover=False):
        if len(self.deck.slides) >= MAX_SLIDES:
            raise ValueError(f"PPTX exceeds {MAX_SLIDES} slides. Narrow the report.")
        slide = self.deck.slides.add_slide(self.deck.slide_layouts[6])
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = PAPER if cover else WHITE
        _textbox(
            slide,
            0.65,
            0.48,
            12.05,
            1.22 if cover else 1.0,
            title,
            33 if cover else 27,
            color=INK,
            bold=True,
        )
        return slide

    def finish(self, slide, *, keys=(), periods=(), unit=None, detail=()):
        items = [self.evidence[key] for key in dict.fromkeys(keys)]
        periods = sorted(
            set(
                periods
                or [
                    p
                    for item in items
                    for p in (item.period, item.reference_period, item.period_from, item.period_to)
                    if p
                ]
            )
        )
        period = f"{periods[0]}–{periods[-1]}" if len(periods) > 1 else next(iter(periods), None)
        labels = dict.fromkeys(
            item.unit_label or item.unit.kind for item in items if item.unit.kind != "unknown"
        )
        units = unit or ", ".join(labels)
        cutoff = self.report.timeline.data_cutoff or self.word["unknown"]
        footer = "  ·  ".join(
            str(value)
            for value in (
                self.word["source"],
                period,
                units,
                f"{self.word['cutoff']} {cutoff}",
                self.word["draft"] if self.report.status == "draft" else None,
            )
            if value
        )
        _textbox(slide, 0.65, 6.91, 12, 0.45, footer, 11, color=MUTED)
        notes = [self.report.report_id, str(footer), *detail]
        for item in items:
            notes.append(
                f"{item.label} | {item.id or item.key} | {item.source.resource_id} | "
                f"{item.source.snapshot_id} | "
                f"{item.source.source_url}"
            )
            if item.formula:
                notes.append(f"{self.word['derived']}: {item.formula} [{item.calculation_id}]")
            notes.extend(item.notes)
        notes.extend(self.report.limitations)
        slide.notes_slide.notes_text_frame.text = "\n".join(dict.fromkeys(notes))

    def cover(self, findings):
        slide = self.slide(self.report.title, cover=True)
        labels = [value for value in (self.report.subtitle, self.report.audience) if value]
        key_messages = [self.finding(finding) for finding in findings]
        lines = [(text, _lines(text, 91) + 1) for text in (*labels, *key_messages)]
        y = 2.16
        for text, size in lines:
            _textbox(slide, 0.68, y, 11.9, size * 0.30, text, 18)
            y += size * 0.30
        self.finish(slide, keys=[k for finding in findings for k in finding.evidence])

    def finding(self, item):
        if item.status == "unverified":
            return f"{self.word['draft']} · {item.text}"
        return item.text

    def text_pages(self, title, values, *, keys=(), detail=()):
        rows = [(value, _lines(value, 104) + 1) for value in values]
        for page in _batches(rows, 18):
            slide = self.slide(title)
            y = 1.53
            for text, size in page:
                _textbox(slide, 0.69, y, 11.75, size * 0.27, text, 16)
                y += size * 0.27
            self.finish(slide, keys=keys, detail=detail)

    def table(self, title, table):
        widths = (6.7, 2.0, 3.3)
        rows = [
            (row, max(1, _lines(row.label, 46), _lines(row.period or "", 15))) for row in table.rows
        ]
        for page in _batches(rows, 10):
            slide = self.slide(title)
            grid = slide.shapes.add_table(
                len(page) + 1, 3, Inches(0.65), Inches(1.62), Inches(12), Inches(0.5)
            ).table
            for column, width in zip(grid.columns, widths, strict=True):
                column.width = Inches(width)
            values = [table.columns[:3]]
            for row, _ in page:
                values.append([row.label, row.period or "—", row.value or self.word["missing"]])
            for number, cells in enumerate(values):
                lines = page[number - 1][1] if number else 1
                grid.rows[number].height = Inches(0.21 + 0.24 * lines)
                for column, text in enumerate(cells):
                    cell = grid.cell(number, column)
                    cell.text = text
                    cell.margin_top = cell.margin_bottom = Inches(0.05)
                    cell.vertical_anchor = MSO_ANCHOR.MIDDLE
                    cell.fill.solid()
                    cell.fill.fore_color.rgb = (
                        BLUE if not number else PAPER if number % 2 else WHITE
                    )
                    paragraph = cell.text_frame.paragraphs[0]
                    _font(paragraph.font, 14, color=WHITE if not number else INK, bold=not number)
            grid.first_row = True
            self.finish(slide, keys=[row.evidence for row, _ in page])

    def chart(self, title, chart):
        data = [{p.period: p for p in line.points} for line in chart.series]
        periods = sorted(set().union(*(points.keys() for points in data)))
        kind = (
            XL_CHART_TYPE.LINE_MARKERS if chart.type == "line" else XL_CHART_TYPE.COLUMN_CLUSTERED
        )
        keys = [line.evidence for line in chart.series]
        for start in range(0, len(periods), CHART_PERIODS):
            chosen = periods[start : start + CHART_PERIODS]
            chart_data = CategoryChartData()
            chart_data.categories = chosen
            plotted = []
            for line, points in zip(chart.series, data, strict=True):
                values = [
                    float(p.value)
                    if (p := points.get(period)) and p.status == "valid" and p.value is not None
                    else None
                    for period in chosen
                ]
                plotted.extend(value for value in values if value is not None)
                chart_data.add_series(line.label, values)
            slide = self.slide(title)
            if plotted:
                graphic = slide.shapes.add_chart(
                    kind, Inches(0.65), Inches(1.57), Inches(12), Inches(5.0), chart_data
                )
                native = graphic.chart
                native._chartSpace.chart.find(qn("c:dispBlanksAs")).set("val", "gap")
                native.has_legend = True
                native.legend.position = XL_LEGEND_POSITION.BOTTOM
                native.legend.include_in_layout = False
                _font(native.legend.font, 11, color=MUTED)
                if chart.type == "bar":
                    if min(plotted) >= 0:
                        native.value_axis.minimum_scale = 0
                    elif max(plotted) <= 0:
                        native.value_axis.maximum_scale = 0
                for axis in (native.category_axis, native.value_axis):
                    _font(axis.tick_labels.font, 10, color=MUTED)
                for series, color in zip(native.series, COLORS, strict=False):
                    if chart.type == "line":
                        series.format.line.color.rgb = RGBColor.from_string(color)
                        series.format.line.width = Pt(2.25)
                    else:
                        series.format.fill.solid()
                        series.format.fill.fore_color.rgb = RGBColor.from_string(color)
            else:
                _textbox(slide, 0.68, 2.30, 11.5, 0.7, self.word["missing"], 21)
            self.finish(
                slide, keys=keys, periods=chosen, unit=chart.unit_label, detail=[chart.alt_text]
            )

    def sources(self, title, items):
        resources = {}
        for item in self.report.evidence:
            source = item.source
            resources.setdefault((source.resource_id, source.snapshot_id), source)
        entries = [(source, 2) for source in resources.values()]
        for page in _batches(entries, 14):
            slide = self.slide(title)
            y = 1.58
            for source, _ in page:
                href = _official_url(source.source_url)
                label = f"TÜİK · {source.resource_id}"
                _textbox(slide, 0.69, y, 11.85, 0.32, label, 17, color=BLUE, link=href)
                _textbox(
                    slide,
                    0.69,
                    y + 0.36,
                    11.85,
                    0.30,
                    f"{source.publication_date or self.word['unknown']}  ·  {source.snapshot_id}",
                    11,
                    color=MUTED,
                )
                y += 0.69
            self.finish(slide, detail=items)


def render_pptx(report: Report) -> bytes:
    if report.schema_version != SCHEMA_VERSION or report.status == "rejected":
        raise ValueError("PPTX requires a supported, saved report model.")
    layout = Briefing(report)
    summaries = [section for section in report.sections if section.kind == "summary"]
    lead = summaries[0] if summaries else None
    findings = [] if lead is None else lead.findings
    cover_values = [
        *[value for value in (report.subtitle, report.audience) if value],
        *[layout.finding(item) for item in findings],
    ]
    use_lead = sum(_lines(value, 91) + 1 for value in cover_values) <= 14
    layout.cover(findings if use_lead else [])
    generated = []
    sources = []
    for section in report.sections:
        if section is lead and use_lead:
            continue
        if section.findings:
            layout.text_pages(
                section.title,
                [layout.finding(item) for item in section.findings],
                keys=[key for item in section.findings for key in item.evidence],
            )
        elif section.table:
            layout.table(section.title, section.table)
        elif section.chart:
            layout.chart(section.title, section.chart)
        elif section.kind == "sources":
            sources.append(section)
        elif section.items:
            generated.extend(section.items)
    if generated:
        layout.text_pages(layout.word["source_details"], generated, detail=generated)
    for section in sources:
        layout.sources(section.title, section.items)
    output = BytesIO()
    layout.deck.save(output)
    return output.getvalue()
