"""Editable Word briefs from a resolved Report using the built-in briefing layout."""

from io import BytesIO
from urllib.parse import urlsplit

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator

from tuik_mcp.models.report import SCHEMA_VERSION, Report

FONT = "DejaVu Sans"
INK = "162942"
BLUE = "135989"
MUTED = "3E5168"
COLORS = ("#135989", "#A65518", "#297268", "#794B88")
COPY = {
    "tr": {
        "draft": "DOĞRULANMAMIŞ TASLAK",
        "cutoff": "Veri kesimi",
        "release": "Son yayım",
        "observation": "Son gözlem",
        "retrieval": "Veri erişimi",
        "unknown": "belirtilmedi",
        "missing": "Değer yok",
        "page": "Sayfa",
        "chart": "Şekil",
        "table": "Tablo",
    },
    "en": {
        "draft": "UNVERIFIED DRAFT",
        "cutoff": "Data cutoff",
        "release": "Latest release",
        "observation": "Latest observation",
        "retrieval": "Data retrieval",
        "unknown": "unspecified",
        "missing": "No value",
        "page": "Page",
        "chart": "Figure",
        "table": "Table",
    },
}


def _element(tag, **attrs):
    element = OxmlElement(f"w:{tag}")
    for name, value in attrs.items():
        element.set(qn(f"w:{name}"), value)
    return element


def _style(document, name, size, color, *, bold=False):
    styles = document.styles
    style = styles[name] if name in styles else styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
    style.font.name = FONT
    style.font.size = Pt(size)
    style.font.color.rgb = RGBColor.from_string(color)
    style.font.bold = bold
    return style.paragraph_format


def _link(paragraph, source_url):
    try:
        parsed = urlsplit(source_url)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname
            and (parsed.hostname == "tuik.gov.tr" or parsed.hostname.endswith(".tuik.gov.tr"))
        )
    except ValueError:
        valid = False
    if not valid:
        paragraph.add_run(source_url)
        return
    relation = paragraph.part.relate_to(source_url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    link = _element("hyperlink")
    link.set(qn("r:id"), relation)
    run = _element("r")
    fmt = _element("rPr")
    fmt.append(_element("color", val=BLUE))
    fmt.append(_element("u", val="single"))
    run.append(fmt)
    text = _element("t")
    text.text = source_url
    run.append(text)
    link.append(run)
    paragraph._p.append(link)


def _chart_image(chart, missing):
    points_by_series = [{point.period: point for point in item.points} for item in chart.series]
    periods = sorted(set().union(*(points.keys() for points in points_by_series)))
    figure = Figure(figsize=(6.65, 2.9), dpi=144, layout="constrained")
    canvas = FigureCanvasAgg(figure)
    axis = figure.subplots()
    axis.spines[["top", "right"]].set_visible(False)
    axis.set_axisbelow(True)
    axis.grid(axis="y", color="#DEE6ED")
    axis.set_ylabel(chart.unit_label or chart.unit.kind, parse_math=False, fontsize=9)
    indexes = range(len(periods))
    present = False
    width = 0.78 / len(points_by_series)
    for i, (item, points) in enumerate(zip(chart.series, points_by_series, strict=True)):
        values = [
            float(point.value)
            if (point := points.get(period)) and point.status == "valid" and point.value is not None
            else float("nan")
            for period in periods
        ]
        present = present or any(value == value for value in values)
        if chart.type == "line":
            axis.plot(
                indexes,
                values,
                color=COLORS[i],
                label=item.label,
                linewidth=1.8,
                marker="o" if len(periods) < 20 else None,
                markersize=3,
            )
        else:
            offset = (i - (len(points_by_series) - 1) / 2) * width
            axis.bar(
                [j + offset for j in indexes], values, width, color=COLORS[i], label=item.label
            )
    if not present:
        axis.text(
            0.5, 0.5, missing, transform=axis.transAxes, ha="center", parse_math=False, fontsize=12
        )
    stride = max(1, (len(periods) + 7) // 8)
    ticks = sorted({*range(0, len(periods), stride), len(periods) - 1}) if periods else []
    axis.set_xticks(ticks, [periods[i] for i in ticks], fontsize=8)
    axis.yaxis.set_major_locator(MaxNLocator(5))
    axis.tick_params(axis="y", labelsize=8)
    if len(chart.series) > 1:
        legend = axis.legend(fontsize=8, frameon=False)
        for text in legend.get_texts():
            text.set_parse_math(False)
    output = BytesIO()
    canvas.print_png(output)
    output.seek(0)
    return output


def _table(document, table, missing):
    grid = document.add_table(rows=1, cols=3)
    grid.autofit = False
    grid.style = "Light Shading Accent 1"
    widths = (3.62, 1.16, 1.63)
    for column, width in zip(grid.columns, widths, strict=True):
        column.width = Inches(width)
    header = grid.rows[0]
    header._tr.get_or_add_trPr().append(_element("tblHeader", val="1"))
    values = [
        table.columns[:3],
        *([row.label, row.period or "—", row.value or missing] for row in table.rows),
    ]
    for index, texts in enumerate(values):
        row = grid.add_row() if index else header
        row._tr.get_or_add_trPr().append(_element("cantSplit"))
        for column, (cell, width, value) in enumerate(zip(row.cells, widths, texts, strict=True)):
            cell.width = Inches(width)
            paragraph = cell.paragraphs[0]
            paragraph.style = "Brief table"
            paragraph.paragraph_format.keep_with_next = index == 0
            if column == 2:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            run = paragraph.add_run(value)
            if index == 0:
                run.bold = True


def _setup(document, word):
    normal = _style(document, "Normal", 10.5, INK)
    normal.space_after, normal.line_spacing = Pt(6), 1.16
    title = _style(document, "Title", 27, INK, bold=True)
    title.space_after = Pt(9)
    heading = _style(document, "Heading 1", 15, BLUE, bold=True)
    heading.space_before, heading.space_after = Pt(14), Pt(5)
    heading.keep_with_next = True
    subtitle = _style(document, "Subtitle", 12, MUTED)
    subtitle.space_after = Pt(8)
    caption = _style(document, "Caption", 9, MUTED)
    caption.space_before, caption.space_after = Pt(4), Pt(4)
    cell = _style(document, "Brief table", 10, INK)
    cell.space_before, cell.space_after = Pt(4), Pt(4)

    section = document.sections[0]
    section.page_width, section.page_height = Inches(8.27), Inches(11.69)
    section.left_margin, section.right_margin = Inches(0.93), Inches(0.93)
    section.top_margin, section.bottom_margin = Inches(0.84), Inches(0.82)
    section.header_distance, section.footer_distance = Inches(0.41), Inches(0.38)

    header = section.header.paragraphs[0]
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    header.add_run("TÜİK").bold = True
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    footer.add_run(f"{word['page']} ")
    footer._p.append(_element("fldSimple", instr="PAGE"))


def render_docx(report: Report) -> bytes:
    if report.schema_version != SCHEMA_VERSION or report.status == "rejected":
        raise ValueError("DOCX requires a supported, saved report model.")
    word = COPY[report.language]
    document = Document()
    document.core_properties.title = report.title
    document.core_properties.language = "tr-TR" if report.language == "tr" else "en-US"
    _setup(document, word)
    document.add_heading(report.title, 0)
    for text in (report.subtitle, report.audience):
        if text:
            document.add_paragraph(text, style="Subtitle")
    if report.status == "draft":
        document.add_paragraph().add_run(word["draft"]).bold = True
    timeline = report.timeline
    for label, value in (
        ("cutoff", timeline.data_cutoff),
        ("release", timeline.latest_release),
        ("observation", timeline.latest_observation),
        ("retrieval", timeline.retrieved_to.isoformat()),
    ):
        paragraph = document.add_paragraph()
        paragraph.add_run(f"{word[label]} · ").bold = True
        paragraph.add_run(str(value or word["unknown"]))
    if report.problems:
        for problem in report.problems:
            document.add_paragraph(problem)

    table_count = chart_count = 0
    chart_keys = {item.key: item for item in report.evidence}
    for section in report.sections:
        if not (section.findings or section.table or section.chart or section.items):
            continue
        document.add_heading(section.title, 1)
        for finding in section.findings:
            paragraph = document.add_paragraph()
            if finding.status == "unverified":
                paragraph.add_run(f"{word['draft']} · ").bold = True
            paragraph.add_run(finding.text)
            notes = [chart_keys[key].id for key in finding.evidence if chart_keys[key].id]
            if notes:
                document.add_paragraph(" · ".join(notes), style="Caption")
        if section.table:
            table_count += 1
            document.add_paragraph(
                f"{word['table']} {table_count}", style="Caption"
            ).paragraph_format.keep_with_next = True
            _table(document, section.table, word["missing"])
        if section.chart:
            chart_count += 1
            unit = section.chart.unit_label or section.chart.unit.kind
            caption = document.add_paragraph(
                f"{word['chart']} {chart_count} · {unit}",
                style="Caption",
            )
            caption.paragraph_format.keep_with_next = True
            paragraph = document.add_paragraph()
            picture = paragraph.add_run().add_picture(
                _chart_image(section.chart, word["missing"]), width=Inches(6.36)
            )
            picture._inline.docPr.set("descr", section.chart.alt_text)
            document.add_paragraph(section.chart.alt_text, style="Caption")
        if section.kind == "sources":
            sources = list(dict.fromkeys(e.source.source_url for e in report.evidence))
            for citation in section.items:
                paragraph = document.add_paragraph()
                link = next((url for url in sources if f" {url};" in citation), None)
                before, _, after = citation.partition(link) if link else (citation, "", "")
                paragraph.add_run(before)
                if link:
                    _link(paragraph, link)
                    paragraph.add_run(after)
        else:
            for item in section.items:
                document.add_paragraph(item)

    output = BytesIO()
    document.save(output)
    return output.getvalue()
