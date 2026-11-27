"""Reference Markdown rendering of a validated report (#91).

Office renderers (#93–#95) read the same `Report`. This one exists so a report
can be read and diffed as text. Every piece of host or source text is escaped:
Markdown punctuation is backslash-escaped, so no text becomes a link, an image, a
remote fetch, HTML or a heading. The renderer adds only fixed structure.
"""

import re

from tuik_mcp.models.report import Report, Section

from .templates import TEXT

# Markdown syntax, HTML, entities and autolinks (scheme `:`, `/`, `www.`, `a@b.c`).
PUNCTUATION = re.compile(r"([\\`*_{}\[\]()#!|<>~&:/.])")
WHITESPACE = re.compile(r"\s+")

LABELS = {
    "tr": {
        "audience": "Hedef kitle",
        "dates": "Veri tarihleri",
        "release": "Son yayım",
        "observation": "Son gözlem",
        "retrieved": "Erişim",
        "cutoff": "Veri kesim tarihi",
        "none": "yok",
        "status": {"ok": "doğrulandı", "draft": "taslak", "rejected": "reddedildi"},
        "chart": "Grafik verisi",
    },
    "en": {
        "audience": "Audience",
        "dates": "Data dates",
        "release": "Latest release",
        "observation": "Latest observation",
        "retrieved": "Retrieved",
        "cutoff": "Data cutoff",
        "none": "none",
        "status": {"ok": "verified", "draft": "draft", "rejected": "rejected"},
        "chart": "Chart data",
    },
}


def escape(text: str | None) -> str:
    """Inline Markdown text that renders literally."""
    return PUNCTUATION.sub(r"\\\1", WHITESPACE.sub(" ", text or "")).strip()


def table(columns: list[str], rows: list[list[str | None]]) -> list[str]:
    lines = [
        "| " + " | ".join(escape(c) for c in columns) + " |",
        "|" + "---|" * len(columns),
    ]
    lines += ["| " + " | ".join(escape(cell or "") for cell in row) + " |" for row in rows]
    return lines


def section(item: Section, language: str) -> list[str]:
    words = TEXT[language]
    lines = [f"## {escape(item.title)}", ""]
    for finding in item.findings:
        marker = f"**{words['unverified']}:** " if finding.status == "unverified" else ""
        lines.append(f"- {marker}{escape(finding.text)}")
    if item.table is not None:
        lines += table(
            item.table.columns,
            [[r.label, r.period, r.value, r.status, r.id] for r in item.table.rows],
        )
    if item.chart is not None:
        chart = item.chart
        lines += [escape(chart.alt_text), "", f"{LABELS[language]['chart']}:", ""]
        periods = sorted({p.period for line in chart.series for p in line.points})
        values = {(line.evidence, p.period): p.value for line in chart.series for p in line.points}
        lines += table(
            [words["columns"][1], *(line.label for line in chart.series)],
            [
                [period, *(values.get((line.evidence, period)) for line in chart.series)]
                for period in periods
            ],
        )
    for entry in item.items:
        lines.append(f"- {escape(entry)}")
    lines.append("")
    return lines


def markdown(report: Report) -> str:
    language = report.language
    labels = LABELS[language]
    timeline = report.timeline
    unknown = TEXT[language]["unknown"]
    lines = [f"# {escape(report.title)}", ""]
    if report.subtitle:
        lines += [f"_{escape(report.subtitle)}_", ""]
    if report.audience:
        lines += [f"{labels['audience']}: {escape(report.audience)}", ""]
    lines += [
        f"## {labels['dates']}",
        "",
        f"- {labels['release']}: {escape(timeline.latest_release or unknown)}",
        f"- {labels['observation']}: {escape(timeline.latest_observation or unknown)}",
        f"- {labels['retrieved']}: {escape(timeline.retrieved_from.isoformat())} – "
        f"{escape(timeline.retrieved_to.isoformat())}",
        f"- {labels['cutoff']}: "
        + escape(timeline.data_cutoff.isoformat() if timeline.data_cutoff else labels["none"]),
        f"- {escape(report.report_id)} · {labels['status'][report.status]}",
        "",
    ]
    for item in report.sections:
        lines += section(item, language)
    return "\n".join(lines)
