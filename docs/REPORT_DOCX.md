# Editable Word briefs

Install the optional package and pass `docx: true` to `build_tuik_report`.

```sh
python -m pip install -e '.[docx-out]'
```

Retrieve `report.docx` with `resources/read` from the report bundle.
`report.json` is the canonical report, and `evidence.csv` provides the exact
decimals, chart points and evidence IDs. The export preserves the statistical
values, methods, source URLs, release/retrieval dates and cutoff.

The built-in A4 briefing fills the title, subtitle, audience, timeline and
validated report sections. It uses real heading styles, editable paragraphs and
tables, repeatable header rows, page numbers, captions and source hyperlinks.
Charts are embedded static images with alternative text and a caption. They can
be regenerated from the retained chart data. Draft text carries the report's
unverified draft label. The fixed layout accepts the structured report fields
as plain content and uses the bundled Word template.

## Verification

Automated read-back checks native rows, headings, repeated header metadata,
source links, the page field, chart alternative text and draft labels.
The same saved CPI model was rendered with the PowerPoint and PDF pilots and
Word. The editable tables and PDF text displayed the published and calculated
October 2026 annual values as `%29,77`. The ordinary Turkish brief rendered in two pages.
A stress brief with a long Turkish title and twenty long rows rendered in four
pages. Table headings repeated across its page break. All pages were visually
inspected in LibreOffice Writer 26.8.0.0.alpha0 on 28 November 2026. The document
uses an installed DejaVu Sans or the editor's font fallback.

```sh
soffice -env:UserInstallation=file:///tmp/tuik-writer-profile --headless \
  --convert-to pdf --outdir renders report.docx
pdftoppm -png renders/report.pdf renders/page
```
