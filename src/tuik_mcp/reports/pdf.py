"""Render validated Report JSON as a bounded, searchable PDF using packaged fonts."""

from html import escape
from io import BytesIO
from pathlib import Path
from threading import Lock
from time import monotonic

from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    KeepTogether,
    LongTable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    TableStyle,
)
from reportlab.platypus.doctemplate import LayoutError

from tuik_mcp.clients.base import SourceError
from tuik_mcp.models.report import Chart, Evidence, Report
from tuik_mcp.reports.render import LABELS
from tuik_mcp.reports.templates import TEXT
from tuik_mcp.utils.normalization import official_url

MAX_INPUT = 2 * 1024 * 1024
MAX_PDF = 4 * 1024 * 1024
MAX_PAGES = 24
PDF_TIME_BUDGET = 20
MARGIN = 48
BODY_WIDTH = A4[0] - 2 * MARGIN - 12  # Platypus frame padding.
FONT = "TuikBriefSans"
BOLD = "TuikBriefSansBold"
INK = colors.HexColor("#173044")
ACCENT = colors.HexColor("#12686A")
GRAY = colors.HexColor("#4F5F6D")
RULE = colors.HexColor("#D1DCE3")
PAPER = colors.HexColor("#F0F5F7")
COLORS = [colors.HexColor(value) for value in ("#12686A", "#A74E11", "#55398E", "#2D5A92")]
DASHES = [None, (5, 2), (2, 2), (8, 2, 2, 2)]
FONT_LOCK = Lock()
WORDS = {
    "tr": {
        "brief": "İSTATİSTİK BÜLTENİ",
        "units": "Birim",
        "source": "Kaynak",
        "published": "Yayımlanan",
        "computed": "Hesaplanan",
        "periods": "Dönem",
        "source_note": "TÜİK kaynağını aç",
        "no_chart": "Geçerli grafik verisi yok.",
    },
    "en": {
        "brief": "STATISTICAL BRIEF",
        "units": "Unit",
        "source": "Source",
        "published": "Published",
        "computed": "Derived",
        "periods": "Period",
        "source_note": "Open the TÜİK source",
        "no_chart": "No valid chart data.",
    },
}


class LimitedBuffer(BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_PDF:
            raise ValueError("PDF exceeds the 4 MiB output limit")
        return super().write(data)


def register_fonts() -> None:
    fonts = Path(__file__).with_name("fonts")
    with FONT_LOCK:
        if BOLD not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(FONT, str(fonts / "DejaVuSans.ttf")))
            pdfmetrics.registerFont(TTFont(BOLD, str(fonts / "DejaVuSans-Bold.ttf")))


def source_list(report: Report) -> dict[tuple[str, str, str], int]:
    return {
        key: index
        for index, key in enumerate(
            dict.fromkeys(
                (item.source.resource_id, item.source.snapshot_id, item.source.source_url)
                for item in report.evidence
            ),
            start=1,
        )
    }


def source_numbers(items: list[Evidence], entries: dict) -> str:
    return ", ".join(
        f"[{number}]"
        for number in sorted(
            {
                entries[item.source.resource_id, item.source.snapshot_id, item.source.source_url]
                for item in items
            }
        )
    )


def graph(chart: Chart, language: str):
    periods = sorted({point.period for line in chart.series for point in line.points})
    valid = [
        float(point.value)
        for line in chart.series
        for point in line.points
        if point.status == "valid" and point.value is not None
    ]
    if not valid:
        return None
    # Limits keep coordinates finite and a huge range from producing excessive tick text.
    if any(not -1e100 <= value <= 1e100 for value in valid):
        raise ValueError("A chart value exceeds the printable coordinate range")
    low, high = min(valid), max(valid)
    if chart.type == "bar":
        low, high = min(low, 0), max(high, 0)
    padding = (high - low) * 0.07 if high != low else abs(high) * 0.07 or 1
    low, high = low - padding, high + padding
    left, bottom, width, height = 60, 34, BODY_WIDTH - 74, 132
    drawing = Drawing(BODY_WIDTH, 178)

    def y(value):
        return bottom + (value - low) * height / (high - low)

    def x(index):
        return left + (index + 0.5) * width / len(periods)

    for step in range(5):
        magnitude = low + step * (high - low) / 4
        axis_y = y(magnitude)
        drawing.add(Line(left, axis_y, left + width, axis_y, strokeColor=RULE))
        label = f"{magnitude:.3g}"
        if language == "tr":
            label = label.replace(".", ",")
        drawing.add(
            String(
                left - 7,
                axis_y - 2,
                label,
                fontName=FONT,
                fontSize=8,
                fillColor=GRAY,
                textAnchor="end",
            )
        )
    drawing.add(Line(left, bottom, left, bottom + height, strokeColor=GRAY))
    if chart.type == "bar":
        drawing.add(Line(left, y(0), left + width, y(0), strokeColor=GRAY))
    ticks = sorted({0, len(periods) - 1, *range(0, len(periods), max(1, (len(periods) + 7) // 8))})
    if len(ticks) > 1 and x(ticks[-1]) - x(ticks[-2]) < 46:
        ticks.pop(-2)
    for index in ticks:
        drawing.add(
            String(
                x(index),
                bottom - 15,
                periods[index],
                fontName=FONT,
                fontSize=7.5,
                fillColor=GRAY,
                textAnchor="middle",
            )
        )

    width_bar = width / len(periods) / (len(chart.series) + 1)
    for line_index, line in enumerate(chart.series):
        points = {point.period: point for point in line.points}
        color = COLORS[line_index]
        previous = None
        for index, period in enumerate(periods):
            point = points.get(period)
            if point is None or point.status != "valid" or point.value is None:
                previous = None
                continue
            current = (x(index), y(float(point.value)))
            if chart.type == "bar":
                offset = (line_index - len(chart.series) / 2) * width_bar
                drawing.add(
                    Rect(
                        current[0] + offset,
                        min(current[1], y(0)),
                        width_bar * 0.9,
                        abs(current[1] - y(0)),
                        fillColor=color,
                        strokeColor=None,
                    )
                )
            else:
                if previous:
                    segment = Line(*previous, *current, strokeColor=color, strokeWidth=1.4)
                    segment.strokeDashArray = DASHES[line_index]
                    drawing.add(segment)
                if len(periods) <= 32 or previous is None:
                    drawing.add(
                        Rect(
                            current[0] - 1.3,
                            current[1] - 1.3,
                            2.6,
                            2.6,
                            fillColor=color,
                            strokeColor=None,
                        )
                    )
                previous = current
    return drawing


def render_pdf(canonical_json: bytes) -> bytes:
    """Use the same canonical JSON included in the artifact. No data acquisition."""
    if len(canonical_json) > MAX_INPUT:
        raise ValueError("Validated report exceeds the PDF input limit")
    report = Report.model_validate_json(canonical_json)
    if report.status == "rejected" or report.spec is None or not report.inline_complete:
        raise ValueError("PDF rendering requires the complete report from a valid ReportSpec")
    deadline = monotonic() + PDF_TIME_BUDGET

    def check_time() -> None:
        if monotonic() >= deadline:
            raise SourceError(
                "pdf_render_limit", "PDF exceeded its rendering time budget.", report.source_url
            )

    check_time()
    register_fonts()
    language = report.language
    words, labels = WORDS[language], LABELS[language]
    localized = TEXT[language]
    unknown = localized["unknown"]
    evidence = {item.key: item for item in report.evidence}
    sources = source_list(report)
    styles = {
        "kicker": ParagraphStyle(
            "Kicker", fontName=BOLD, fontSize=8, leading=12, textColor=ACCENT, spaceAfter=6
        ),
        "title": ParagraphStyle(
            "Title", fontName=BOLD, fontSize=24, leading=30, textColor=INK, spaceAfter=12
        ),
        "heading": ParagraphStyle(
            "Heading",
            fontName=BOLD,
            fontSize=15,
            leading=19,
            textColor=INK,
            spaceBefore=12,
            spaceAfter=7,
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "Body", fontName=FONT, fontSize=10, leading=15, textColor=INK, spaceAfter=8
        ),
        "muted": ParagraphStyle(
            "Muted", fontName=FONT, fontSize=8.5, leading=12, textColor=GRAY, spaceAfter=6
        ),
        "table": ParagraphStyle("Table", fontName=FONT, fontSize=8.5, leading=11.5, textColor=INK),
        "header": ParagraphStyle(
            "Header", fontName=BOLD, fontSize=8.5, leading=11.5, textColor=INK
        ),
        "draft": ParagraphStyle(
            "Draft",
            fontName=BOLD,
            fontSize=10,
            leading=15,
            textColor=colors.HexColor("#9C2F2B"),
            spaceAfter=10,
        ),
    }

    def para(text, style="body"):
        return Paragraph(escape(text or "—"), styles[style])

    def table(rows, weights):
        contents = [
            [para(cell, "header" if index == 0 else "table") for cell in row]
            for index, row in enumerate(rows)
        ]
        item = LongTable(
            contents,
            colWidths=[BODY_WIDTH * width for width in weights],
            repeatRows=1,
            splitByRow=1,
            splitInRow=1,
            hAlign="LEFT",
        )
        item.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), PAPER),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.8, ACCENT),
                    ("LINEBELOW", (0, 1), (-1, -1), 0.4, RULE),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )
        return item

    story = [para(words["brief"], "kicker"), para(report.title, "title")]
    if report.status == "draft":
        story.append(para(localized["unverified"], "draft"))
    if report.subtitle:
        story.append(para(report.subtitle))
    if report.audience:
        story.append(para(f"{labels['audience']} · {report.audience}", "muted"))
    timeline = report.timeline
    story += [
        para(labels["dates"], "heading"),
        para(f"{labels['cutoff']} · {timeline.data_cutoff or labels['none']}"),
        para(f"{labels['release']} · {timeline.latest_release or unknown}", "muted"),
        para(f"{labels['observation']} · {timeline.latest_observation or unknown}", "muted"),
        para(
            f"{labels['retrieved']} · {timeline.retrieved_from.isoformat()} – "
            f"{timeline.retrieved_to.isoformat()}",
            "muted",
        ),
    ]
    analysis_started = methods_started = False
    for section in report.sections:
        check_time()
        if (section.table is not None or section.chart is not None) and not analysis_started:
            story.append(PageBreak())
            analysis_started = True
        if section.kind in {"methods", "limitations", "sources"} and not methods_started:
            if analysis_started:
                story.append(PageBreak())
            methods_started = True
        heading = para(section.title, "heading")
        if section.chart:
            chart = section.chart
            ordered = [evidence[line.evidence] for line in chart.series]
            unit = chart.unit_label or chart.unit.kind
            drawing = graph(chart, language)
            first = para(
                f"{words['units']} · {unit}  |  {words['source']} "
                f"{source_numbers(ordered, sources)}",
                "muted",
            )
            story.append(KeepTogether([heading, first, drawing or para(words["no_chart"])]))
            for plot_index, line in enumerate(chart.series):
                style = ParagraphStyle(
                    f"Legend{plot_index}", parent=styles["muted"], textColor=COLORS[plot_index]
                )
                story.append(Paragraph(escape(f"{plot_index + 1}. {line.label}"), style))
            story.append(para(chart.alt_text, "muted"))
            continue
        story.append(heading)
        for finding in section.findings:
            marker = localized["unverified"] + " · " if finding.status == "unverified" else ""
            refs = source_numbers([evidence[key] for key in finding.evidence], sources)
            story.append(para(f"{marker}{finding.text} {refs}".rstrip()))
        if section.table:
            rows = [section.table.columns]
            for row in section.table.rows:
                item = evidence[row.evidence]
                method = words["computed"] if item.derived else words["published"]
                ref = source_numbers([item], sources)
                period = row.period or (
                    f"{item.period_from} – {item.period_to}"
                    if item.period_from and item.period_to
                    else "—"
                )
                rows.append(
                    [
                        row.label,
                        period,
                        row.value or "—",
                        f"{row.status} · {method}",
                        f"{ref} · {row.id or row.evidence}",
                    ]
                )
            story.append(table(rows, (0.24, 0.13, 0.21, 0.16, 0.26)))
        story.extend(para(item) for item in section.items)
        if section.kind == "sources":
            for (resource, snapshot, url), number in sources.items():
                story.append(para(f"[{number}] {resource} · {snapshot}", "muted"))
                safe = official_url(url, fetch=True)
                link = f'<link href="{escape(safe, quote=True)}" color="#12686A">'
                link += escape(words["source_note"]) + "</link>"
                story.append(Paragraph(link, styles["body"]))
        story.append(Spacer(1, 5))

    def page(canvas, document):
        check_time()
        if document.page > MAX_PAGES:
            raise ValueError("PDF exceeds the 24-page output limit")
        canvas.setTitle(report.title)
        canvas.setAuthor("tuik-mcp")
        canvas.setStrokeColor(RULE)
        canvas.line(MARGIN, 43, A4[0] - MARGIN, 43)
        canvas.setFont(FONT, 8)
        canvas.setFillColor(GRAY)
        canvas.drawString(MARGIN, 29, "TÜİK · tuik-mcp")
        canvas.drawRightString(A4[0] - MARGIN, 29, str(document.page))
        if report.status == "draft":
            canvas.setFillColor(colors.HexColor("#9C2F2B"))
            canvas.drawCentredString(A4[0] / 2, 29, localized["unverified"])

    out = LimitedBuffer()
    pdf = SimpleDocTemplate(
        out, pagesize=A4, rightMargin=MARGIN, leftMargin=MARGIN, topMargin=42, bottomMargin=56
    )
    try:
        pdf.build(story, onFirstPage=page, onLaterPages=page, canvasmaker=Canvas)
    except (ValueError, LayoutError) as exc:
        raise SourceError("pdf_render_limit", str(exc), report.source_url) from exc
    check_time()
    return out.getvalue()
