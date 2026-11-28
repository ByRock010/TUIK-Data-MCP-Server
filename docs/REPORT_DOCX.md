# Word briefs (#95)

`build_tuik_report` can add an editable Word brief, `report.docx`, to the same
artifact as `report.json`. Install the optional extra first:
`pip install 'tuik-mcp[docx-out]'`, or `pip install -e '.[docx-out]'` from a
checkout. Then call the tool with the regular [report spec](REPORTS.md) and
`docx: true` at the tool-argument level.

The brief is drawn from the exact `report.json` bytes saved beside it, as the
PowerPoint (#93) and PDF (#94) outputs are. The renderer never fetches,
recalculates or writes prose, so the values, periods, methods, limitations and
citations are the report's own text in every format. Rejected reports save
nothing, so they have no brief.

## Structure

- **Navigation.** Real `Title`, `Heading 1` and `Heading 2` styles, so Word's
  navigation pane and a table of contents work. Headings keep with the next
  paragraph.
- **Dates and status.** The front matter lists the latest release, latest
  observation, retrieval time, data cutoff and report ID with its status. A
  marked draft adds the draft message, puts `DOĞRULANMAMIŞ TASLAK` /
  `UNVERIFIED DRAFT` in every page header, and prefixes each unverified finding.
- **Findings.** Findings and the method and limitation lines are editable
  bulleted paragraphs.
- **Tables.**
  - Report tables are editable Word tables with a numbered caption (`Tablo 1`, a `SEQ` field).
  - The header row repeats on every page, and a row never splits across pages.
  - A compact source and unit note follows each table.
- **Charts.**
  - A chart is a picture drawn by the shared chart renderer (#90) from the report's own points.
  - It has a numbered caption (`Şekil 1`), alt text and the report's description.
  - An editable "chart data" table with the same values follows it.
  - Word does not edit the picture as a chart, and the document says so. The data stay in the table and in `evidence.csv`.
  - If a chart cannot be drawn (for example a bar chart beyond the renderer's 60 points), the document says that too and keeps the table.
- **Methods, limitations and sources.**
  - These start on a new page.
  - Sources are a numbered list of the report's own source lines. Only official TÜİK URLs become links.
- **Page furniture.**
  - The page header shows the title, and the footer shows the source, report ID and a `PAGE` field.
  - Pages are A4 and the font is Calibri (Carlito, which has the same metrics, where Calibri is not installed).

## Template

`docx_template` sets plain header and footer text. These are the only
supported placeholders:

| Placeholder | Value |
| --- | --- |
| `{title}` | Report title |
| `{report_id}` | Deterministic report ID |
| `{status}` | `doğrulandı` / `taslak` (or English) |
| `{cutoff}` | Data cutoff date, or "yok" / "none" |
| `{organization}` | The template's own plain `organization` text |

The following are refused with a clear message:
- any other placeholder;
- an unmatched brace;
- braces in `organization`;
- control or format characters;
- text over 200 characters;
- any other key, such as a template file, styles, macros or fields.

Arbitrary Word templates are not executed.

## Plain text and read-back

All source, host and template text is written as plain runs, so no text can
become a field, a hyperlink, a style or markup. The renderer writes only its
own `PAGE` and `SEQ` fields.

After rendering, the document is read back. It is refused unless all of these
hold:
- every table equals the report;
- every table's header row repeats;
- every external relationship is a hyperlink to an official host;
- every field is one the renderer wrote.

Equal reports give byte-identical documents: the core properties and zip
entries carry fixed timestamps.

`report.json` is capped at 2 MiB and the brief at 12 MiB; the artifact limits
of [#88](ARTIFACTS.md) also apply. `python-docx` is imported only for a `docx:
true` request. Without the extra, that request fails with `docx_unavailable`,
and every other call works as before.

## Verified editor

- **LibreOffice Writer 24.2.7.2** (headless, through its UNO API) opened the
  Turkish brief.
  - It laid out four A4 pages.
  - Edits to the title paragraph and a table cell were saved back to DOCX.
  - Headings, the repeated table header and the official link survived.
- **Visual check.** Turkish and English briefs and a long draft were rendered
  with `soffice --headless --convert-to pdf` and inspected:
  - Turkish glyphs;
  - the table header repeating on pages 2 and 3;
  - the page break before Methods;
  - header, footer and page numbers;
  - captions, the chart picture and linked sources.
- **Not verified:** Microsoft Word.
