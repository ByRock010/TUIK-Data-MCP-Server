# Printable evidence reports

Install the optional PDF authoring extra with `pip install 'tuik-mcp[pdf-out]'`.
From a checkout, use `pip install -e '.[pdf-out]'`.

Call `build_tuik_report` with the regular [report spec](REPORTS.md) and
`pdf: true` at the tool-argument level. The server validates evidence and
findings once. It renders the bytes written as `report.json` and adds
`report.pdf` to that same artifact. Read the PDF's resource URI with
`resources/read` or export the bundle locally. The existing JSON, Markdown,
evidence CSV and retained source files remain available.

The PDF shows the report cutoff, latest release, latest observation and
retrieval time separately. Tables distinguish published and computed values.
Figures show the validated unit, source references and exact plot input in
the sibling JSON/CSV. Missing chart values leave gaps. Source links point
only to the official URLs retained with structured evidence. Rejected reports
save nothing. Marked drafts carry a warning on every page.

The layout keeps headings and charts with their captions and repeats table
headers when a table spans pages. The embedded DejaVu Sans faces preserve
searchable Turkish and English text. Chart marks and axes are vector graphics.
The text, visible structure, source links and alt summaries are readable
without an image layer. The renderer does not emit PDF/UA tags or claim
PDF/UA conformance.

## Renderer choice and verification

Both candidate engines produced searchable Turkish PDF text and a table in
a local prototype. [ReportLab](https://docs.reportlab.com/) provided
repeatable table headers, embedded TrueType fonts and vector drawing through
one Python-facing renderer. [WeasyPrint](https://doc.courtbouillon.org/weasyprint/stable/)
handled the equivalent HTML/CSS sample, with a separate native rendering
stack and URL-fetch policy. The server uses ReportLab with structured
flowables and its packaged fonts. Text is escaped before being put into
report paragraphs. The renderer has no remote asset resolver and accepts no
custom file paths or templates.

The validated JSON is capped at 2 MiB and the PDF at 24 pages and 4 MiB.
The rendering deadline is checked at section and page boundaries and before
returning output.
The existing artifact limits also apply. Limit failures leave storage
without a partial report. The optional PDF dependency is loaded only on
an explicit PDF request. Core PDF reading and other tool calls keep their
existing dependency profile.

Regression tests compare searchable PDF text with the canonical report,
verify link destinations, inspect vector marks and test multipage tables,
draft warnings, missing optional dependencies and MCP file retrieval.
The bundled font license is recorded alongside the source fonts.
