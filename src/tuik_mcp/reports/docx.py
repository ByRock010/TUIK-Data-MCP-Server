"""Editable Word briefs rendered from a validated report (#95).

Like the other renderers, this one reads the exact `report.json` bytes of the
bundle and never fetches, recalculates or writes prose. The document uses real
heading styles (so the navigation pane works), editable paragraphs and tables
whose header rows repeat on every page, numbered captions, page headers and
footers with page numbers, and a numbered source list whose links point only
to official TÜİK hosts.

Charts are pictures drawn by the shared chart renderer (#90) from the report's
own points, next to an editable table with the same values; Word does not edit
those pictures as charts, and the document says so. A small, fixed set of
template placeholders may fill the page header and footer; any other template
construct is refused. All text is written as plain runs: no source text can
become a field, a hyperlink, a style or markup.

After rendering, the document is read back: every table must equal the report
and every external relationship must be a hyperlink to an official host.
"""

import io
import re
import unicodedata
import zipfile
from datetime import UTC
from urllib.parse import urlsplit

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from tuik_mcp.config import ALLOWED_HOSTS
from tuik_mcp.models.report import Chart, Report, Section

from .docx_template import PLACEHOLDER, DocxTemplate
from .templates import TEXT

MAX_REPORT_BYTES = 2 * 1024 * 1024
MAX_DOCUMENT_BYTES = 12 * 1024 * 1024
FONT = "Calibri"
INK = RGBColor(0x1F, 0x29, 0x37)
MUTED = RGBColor(0x4B, 0x55, 0x63)
WARNING = RGBColor(0xB4, 0x23, 0x18)
ACCENT = RGBColor(0x1D, 0x4E, 0x89)

LABELS = {
    "tr": {
        "dates": "Veri tarihleri",
        "release": "Son yayım",
        "observation": "Son gözlem",
        "retrieved": "Erişim",
        "cutoff": "Veri kesim tarihi",
        "none": "yok",
        "status": {"ok": "doğrulandı", "draft": "taslak", "rejected": "reddedildi"},
        "table": "Tablo",
        "figure": "Şekil",
        "chart_data": "Grafik verisi",
        "chart_note": (
            "Grafik, rapordaki değerlerden çizilmiş bir resimdir; Word'de grafik olarak "
            "düzenlenmez. Düzenlenebilir değerler aşağıdaki tabloda ve paketteki "
            "evidence.csv dosyasındadır."
        ),
        "no_picture": (
            "Bu grafik resim olarak çizilemedi; değerleri aşağıdaki tabloda ve "
            "evidence.csv dosyasındadır."
        ),
        "source": "Kaynak: TÜİK",
        "page": "Sayfa",
        "report": "Rapor",
        "draft": "DOĞRULANMAMIŞ TASLAK",
    },
    "en": {
        "dates": "Data dates",
        "release": "Latest release",
        "observation": "Latest observation",
        "retrieved": "Retrieved",
        "cutoff": "Data cutoff",
        "none": "none",
        "status": {"ok": "verified", "draft": "draft", "rejected": "rejected"},
        "table": "Table",
        "figure": "Figure",
        "chart_data": "Chart data",
        "chart_note": (
            "The chart is a picture drawn from the report's values; Word does not edit it "
            "as a chart. The editable values are in the table below and in the bundle's "
            "evidence.csv."
        ),
        "no_picture": (
            "This chart could not be drawn as a picture; its values are in the table "
            "below and in evidence.csv."
        ),
        "source": "Source: TurkStat (TÜİK)",
        "page": "Page",
        "report": "Report",
        "draft": "UNVERIFIED DRAFT",
    },
}


class RenderError(ValueError):
    """The report cannot be rendered within the limits, or read back exactly."""


def plain(text: str | None) -> str:
    """Text safe for a run: no control or format characters, single spaces."""
    cleaned = "".join(
        " " if unicodedata.category(char) in {"Cc", "Cf", "Cs"} else char for char in text or ""
    )
    return " ".join(cleaned.split())


def official(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.hostname in ALLOWED_HOSTS


def fill(template: str, values: dict[str, str]) -> str:
    return PLACEHOLDER.sub(lambda match: values[match.group(1)], template)


class Brief:
    def __init__(self, report: Report, template: DocxTemplate):
        self.report = report
        self.template = template
        self.language = report.language
        self.labels = LABELS[self.language]
        self.words = TEXT[self.language]
        self.by_key = {item.key: item for item in report.evidence}
        self.document = Document()
        self.tables_expected: list[list[list[str]]] = []
        self.links: list[str] = []
        self.counts = {"table": 0, "figure": 0}
        self.setup()

    # Page, styles, header and footer -------------------------------------------------

    def setup(self):
        section = self.document.sections[0]
        section.orientation = WD_ORIENT.PORTRAIT
        section.page_width, section.page_height = Cm(21), Cm(29.7)
        for side in ("left_margin", "right_margin"):
            setattr(section, side, Cm(2.2))
        section.top_margin, section.bottom_margin = Cm(2.2), Cm(2.0)
        styles = self.document.styles
        normal = styles["Normal"]
        normal.font.name = FONT
        normal.font.size = Pt(11)
        normal.font.color.rgb = INK
        # East Asian and complex-script slots too, so every script uses the same face.
        fonts = normal.element.rPr.get_or_add_rFonts()
        for slot in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            fonts.set(qn(slot), FONT)
        for name, size in (("Title", 24), ("Heading 1", 16), ("Heading 2", 13)):
            style = styles[name]
            style.font.name = FONT
            style.font.size = Pt(size)
            style.font.color.rgb = ACCENT
            style.paragraph_format.keep_with_next = True
        styles["Caption"].font.size = Pt(10)
        styles["Caption"].font.color.rgb = MUTED
        self.document.settings.element.append(self.language_setting())
        values = {
            "title": plain(self.report.title)[:120],
            "report_id": self.report.report_id,
            "status": self.labels["status"][self.report.status],
            "cutoff": (
                self.report.timeline.data_cutoff.isoformat()
                if self.report.timeline.data_cutoff
                else self.labels["none"]
            ),
            "organization": plain(self.template.organization or ""),
        }
        header_text = fill(self.template.header or "{title}", values)
        if self.report.status == "draft":
            header_text = f"{self.labels['draft']} · {header_text}"
        header = section.header.paragraphs[0]
        header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        self.run(
            header,
            plain(header_text),
            size=9,
            color=WARNING if self.report.status == "draft" else MUTED,
        )
        footer = section.footer.paragraphs[0]
        footer_text = fill(
            self.template.footer or f"{self.labels['source']} · {{report_id}}", values
        )
        self.run(footer, plain(footer_text) + f" · {self.labels['page']} ", size=9, color=MUTED)
        self.field(footer, "PAGE", "1", size=9)

    def language_setting(self):
        element = OxmlElement("w:themeFontLang")
        element.set(qn("w:val"), "tr-TR" if self.language == "tr" else "en-US")
        return element

    # Low-level writing -------------------------------------------------------------

    @staticmethod
    def run(paragraph, text: str, *, size=None, bold=False, color=None):
        run = paragraph.add_run(text)
        if size:
            run.font.size = Pt(size)
        run.font.bold = bold or None
        if color is not None:
            run.font.color.rgb = color
        return run

    @staticmethod
    def field(paragraph, instruction: str, result: str, *, size=None):
        """A field the server writes itself (page numbers, caption numbers); never from text."""
        element = OxmlElement("w:fldSimple")
        element.set(qn("w:instr"), f" {instruction} ")
        run = OxmlElement("w:r")
        if size:
            properties = OxmlElement("w:rPr")
            measure = OxmlElement("w:sz")
            measure.set(qn("w:val"), str(size * 2))
            properties.append(measure)
            run.append(properties)
        text = OxmlElement("w:t")
        text.text = result
        run.append(text)
        element.append(run)
        paragraph._p.append(element)

    def paragraph(self, text: str, style: str | None = None, *, prefix: str | None = None):
        paragraph = self.document.add_paragraph(style=style)
        if prefix:
            self.run(paragraph, prefix, bold=True, color=WARNING)
        self.run(paragraph, plain(text))
        return paragraph

    def caption(self, kind: str, title: str):
        self.counts[kind] += 1
        label = self.labels[kind]
        paragraph = self.document.add_paragraph(style="Caption")
        paragraph.paragraph_format.keep_with_next = True
        self.run(paragraph, f"{label} ")
        self.field(paragraph, f"SEQ {label} \\* ARABIC", str(self.counts[kind]))
        self.run(paragraph, f": {plain(title)}")

    def hyperlink(self, paragraph, url: str):
        """A link to an official TÜİK host; any other URL stays plain text."""
        if not official(url):
            self.run(paragraph, url)
            return
        relation = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
        link = OxmlElement("w:hyperlink")
        link.set(qn("r:id"), relation)
        run = OxmlElement("w:r")
        properties = OxmlElement("w:rPr")
        style = OxmlElement("w:rStyle")
        style.set(qn("w:val"), "Hyperlink")
        properties.append(style)
        color = OxmlElement("w:color")
        color.set(qn("w:val"), "1D4E89")
        properties.append(color)
        underline = OxmlElement("w:u")
        underline.set(qn("w:val"), "single")
        properties.append(underline)
        run.append(properties)
        text = OxmlElement("w:t")
        text.text = url
        text.set(qn("xml:space"), "preserve")
        run.append(text)
        link.append(run)
        paragraph._p.append(link)
        self.links.append(url)

    def table(
        self,
        columns: list[str],
        rows: list[list[str]],
        numeric: set[int],
        widths: tuple[float, ...] | None = None,
    ):
        table = self.document.add_table(rows=1, cols=len(columns))
        table.style = "Table Grid"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = widths is None
        header = table.rows[0]
        properties = header._tr.get_or_add_trPr()
        repeat = OxmlElement("w:tblHeader")
        repeat.set(qn("w:val"), "true")
        properties.append(repeat)
        for index, name in enumerate(columns):
            cell = header.cells[index]
            self.run(cell.paragraphs[0], plain(name), bold=True, size=10)
            shade = OxmlElement("w:shd")
            shade.set(qn("w:val"), "clear")
            shade.set(qn("w:fill"), "DCE6F2")
            cell._tc.get_or_add_tcPr().append(shade)
        for row in rows:
            cells = table.add_row()
            cells._tr.get_or_add_trPr().append(self.cant_split())
            for index, value in enumerate(row):
                paragraph = cells.cells[index].paragraphs[0]
                if index in numeric:
                    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                self.run(paragraph, plain(value), size=10)
        if widths is not None:
            for column, width in zip(table.columns, widths, strict=True):
                column.width = Cm(width)
            for row in table.rows:
                for cell, width in zip(row.cells, widths, strict=True):
                    cell.width = Cm(width)
        self.tables_expected.append(
            [[plain(c) for c in columns], *[[plain(v) for v in r] for r in rows]]
        )
        self.document.add_paragraph()
        return table

    @staticmethod
    def cant_split():
        element = OxmlElement("w:cantSplit")
        element.set(qn("w:val"), "true")
        return element

    # Content -----------------------------------------------------------------------

    def front(self):
        report, labels = self.report, self.labels
        timeline = report.timeline
        unknown = self.words["unknown"]
        self.document.add_paragraph(plain(report.title), style="Title")
        for text in (report.subtitle, report.audience):
            if text:
                self.paragraph(text, "Subtitle")
        retrieved = timeline.retrieved_from.isoformat()
        if timeline.retrieved_to != timeline.retrieved_from:
            retrieved += f" – {timeline.retrieved_to.isoformat()}"
        lines = [
            (labels["release"], timeline.latest_release or unknown),
            (labels["observation"], timeline.latest_observation or unknown),
            (labels["retrieved"], retrieved),
            (
                labels["cutoff"],
                timeline.data_cutoff.isoformat() if timeline.data_cutoff else labels["none"],
            ),
            (labels["report"], f"{report.report_id} · {labels['status'][report.status]}"),
        ]
        for name, value in lines:
            paragraph = self.document.add_paragraph()
            paragraph.paragraph_format.space_after = Pt(0)
            self.run(paragraph, f"{name}: ", bold=True, size=10)
            self.run(paragraph, plain(value), size=10)
        if report.status == "draft":
            paragraph = self.document.add_paragraph()
            self.run(paragraph, plain(report.message), bold=True, color=WARNING)

    def section(self, section: Section):
        if section.kind == "methods":
            self.document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        self.document.add_heading(plain(section.title), level=1)
        for finding in section.findings:
            prefix = f"{self.words['unverified']}: " if finding.status == "unverified" else None
            self.paragraph(finding.text, "List Bullet", prefix=prefix)
        if section.table is not None:
            self.caption("table", section.title)
            self.table(
                section.table.columns,
                [
                    [r.label, r.period or "", r.value or "", r.status, r.id or ""]
                    for r in section.table.rows
                ],
                numeric={2},
                widths=(4.0, 2.4, 3.0, 1.5, 5.6),
            )
            self.source_note([r.evidence for r in section.table.rows])
        if section.chart is not None:
            self.chart(section, section.chart)
        if section.kind == "sources":
            self.sources(section)
        else:
            for item in section.items:
                self.paragraph(item, "List Bullet")

    def source_note(self, keys: list[str]):
        items = [self.by_key[k] for k in dict.fromkeys(keys) if k in self.by_key]
        if not items:
            return
        resources = ", ".join(dict.fromkeys(item.source.resource_id for item in items))
        units = ", ".join(dict.fromkeys(i.unit_label for i in items if i.unit_label))
        paragraph = self.document.add_paragraph(style="Caption")
        self.run(
            paragraph,
            plain(f"{self.labels['source']} · {resources}" + (f" · {units}" if units else "")),
        )

    def chart(self, section: Section, chart: Chart):
        picture = self.picture(section, chart)
        self.caption("figure", section.title)
        if picture is not None:
            paragraph = self.document.add_paragraph()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            shape = paragraph.add_run().add_picture(io.BytesIO(picture), width=Cm(16.5))
            shape._inline.docPr.set("descr", plain(chart.alt_text)[:1000])
            note = self.labels["chart_note"]
        else:
            note = self.labels["no_picture"]
        self.paragraph(chart.alt_text)
        self.paragraph(note, "Caption")
        periods = sorted({p.period for line in chart.series for p in line.points})
        values = {(line.evidence, p.period): p.value for line in chart.series for p in line.points}
        self.document.add_heading(self.labels["chart_data"], level=2)
        self.table(
            [self.words["columns"][1], *(line.label for line in chart.series)],
            [
                [period, *(values.get((line.evidence, period)) or "" for line in chart.series)]
                for period in periods
            ],
            numeric=set(range(1, len(chart.series) + 1)),
        )
        self.source_note([line.evidence for line in chart.series])

    def picture(self, section: Section, chart: Chart) -> bytes | None:
        """The shared chart renderer's PNG of the report's own points, or None."""
        try:
            from tuik_mcp import charts
            from tuik_mcp.models.chart import ChartSpec, ChartSpecSeries
        except ImportError:  # pragma: no cover - charts ship with the package
            return None
        first = self.by_key.get(chart.series[0].evidence)
        if first is None:
            return None
        periods = sorted({p.period for line in chart.series for p in line.points})
        series = []
        for line in chart.series:
            points = {p.period: p for p in line.points}
            values, states = [], []
            for period in periods:
                point = points.get(period)
                if point is not None and point.status == "valid" and point.value is not None:
                    values.append(float(point.value))
                    states.append("ok")
                else:
                    values.append(None)
                    states.append(
                        "missing" if point is None or point.status == "missing" else "invalid"
                    )
            series.append(
                ChartSpecSeries(name=line.label, column=line.evidence, values=values, states=states)
            )
        try:
            spec = ChartSpec(
                chart=chart.type,
                theme="publication",
                title=plain(section.title)[:600] or "-",
                x_columns=["period"],
                x_labels=periods,
                x_order="chronological",
                series=series,
                unit=chart.unit_label,
                unit_source="source_metadata" if chart.unit_label else "unknown",
                resource_id=first.source.resource_id,
                source_url=first.source.source_url,
                retrieved_at=first.source.retrieved_at,
                snapshot_id=first.source.snapshot_id,
                source_note=None,
                alt_text=plain(chart.alt_text) or "-",
            )
            image, _, _ = charts.render_spec(spec, "png")
        except Exception:  # noqa: BLE001 - a picture is optional; the table carries the data
            return None
        return image

    def sources(self, section: Section):
        """A numbered source list: the report's own source lines, official URLs linked."""
        urls = sorted(
            {item.source.source_url for item in self.report.evidence}, key=len, reverse=True
        )
        pattern = re.compile("|".join(re.escape(url) for url in urls)) if urls else None
        for line in section.items:
            paragraph = self.document.add_paragraph(style="List Number")
            text = plain(line)
            position = 0
            for match in pattern.finditer(text) if pattern else []:
                self.run(paragraph, text[position : match.start()])
                self.hyperlink(paragraph, match.group(0))
                position = match.end()
            self.run(paragraph, text[position:])

    def build(self) -> bytes:
        self.front()
        for section in self.report.sections:
            self.section(section)
        properties = self.document.core_properties
        properties.title = plain(self.report.title)[:255]
        properties.subject = self.report.report_id
        properties.author = properties.last_modified_by = "tuik-mcp"
        properties.language = "tr-TR" if self.language == "tr" else "en-US"
        stamp = self.report.timeline.retrieved_to.astimezone(UTC).replace(microsecond=0)
        properties.created = properties.modified = stamp.replace(tzinfo=None)
        properties.revision = 1
        output = io.BytesIO()
        self.document.save(output)
        return output.getvalue()


def stable_zip(content: bytes) -> bytes:
    """The same package with fixed entry timestamps, so equal reports give equal bytes."""
    source = zipfile.ZipFile(io.BytesIO(content))
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        for entry in source.infolist():
            info = zipfile.ZipInfo(entry.filename, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(info, source.read(entry.filename))
    return output.getvalue()


def render_docx(document: bytes, template: DocxTemplate | dict | None = None) -> bytes:
    """A Word document of the validated report whose JSON is `document`."""
    if len(document) > MAX_REPORT_BYTES:
        raise RenderError(f"report.json exceeds {MAX_REPORT_BYTES} bytes")
    report = Report.model_validate_json(document)
    if report.status == "rejected":
        raise RenderError("A rejected report has no brief")
    template = DocxTemplate.model_validate(template or {})
    brief = Brief(report, template)
    content = stable_zip(brief.build())
    if len(content) > MAX_DOCUMENT_BYTES:
        raise RenderError(f"The brief exceeds {MAX_DOCUMENT_BYTES} bytes")
    verify(content, brief.tables_expected)
    return content


def verify(content: bytes, tables: list[list[list[str]]]) -> None:
    """Read the saved document back; any difference from the report is an error."""
    document = Document(io.BytesIO(content))
    found = [[[cell.text for cell in row.cells] for row in table.rows] for table in document.tables]
    if found != tables:
        raise RenderError("A table cell differs from the report after read-back")
    for table in document.tables:
        header = table.rows[0]._tr.trPr
        if header is None or header.find(qn("w:tblHeader")) is None:
            raise RenderError("A table header does not repeat across pages")
    parts = [
        document.part,
        *(s.header.part for s in document.sections),
        *(s.footer.part for s in document.sections),
    ]
    for part in parts:
        for relation in part.rels.values():
            if relation.is_external and not (
                relation.reltype == RT.HYPERLINK and official(relation.target_ref)
            ):
                raise RenderError(f"The brief references an external target: {relation.target_ref}")
            if not relation.is_external and relation.target_part is None:
                raise RenderError("The brief references a missing part")
    for element in [part.element for part in parts]:
        if element.findall(".//" + qn("w:instrText")) or element.findall(".//" + qn("w:fldChar")):
            raise RenderError("The brief contains a field that the renderer did not write")
        for simple in element.findall(".//" + qn("w:fldSimple")):
            if simple.get(qn("w:instr"), "").split()[:1] not in (["PAGE"], ["SEQ"]):
                raise RenderError("The brief contains a field that the renderer did not write")


__all__ = ["DocxTemplate", "RenderError", "render_docx", "verify"]
