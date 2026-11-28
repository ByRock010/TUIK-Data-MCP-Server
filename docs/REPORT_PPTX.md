# Editable PowerPoint briefings

Install the optional package and pass `pptx: true` to `build_tuik_report`.

```sh
python -m pip install -e '.[pptx-out]'
```

The returned artifact contains `report.pptx`, the resolved `report.json`,
`evidence.csv`, the manifest and retained official source files. Retrieve the
presentation with `resources/read` or export the local bundle using
`tuik-mcp-artifacts export ARTIFACT_ID --to DIRECTORY`.

The 16:9 briefing uses editable text, native tables and native line and column
charts with embedded workbooks. Columns start at zero for all-positive or
all-negative data. Empty points remain gaps. Long tables, long evidence notes
and long series continue across slides. A chart with no values displays the
missing-data message and retains its explanatory notes and underlying evidence.
The current ReportSpec only exposes line and bar charts. Other visual types
require an expanded report schema and image fallback contract.

The slide footer gives the statistical source, period/reference period, unit
and data cutoff where available. The speaker notes retain observation or
calculation IDs, full source links, methodology and limitations. PowerPoint's
numeric cells use its ordinary floating-point chart representation. The
canonical decimal strings remain in the bundle. Unverified drafts are labeled
in the slides and speaker notes.

## Verification

The automated read-back checks native table cells, category labels, chart
values, embedded workbooks and notes against the bundled report. It checks
editable English draft labeling and the ordinary output with the extra unused.

The Turkish six-slide example uses the CPI example in [Reports](REPORTS.md).
Set the series start to January 2026 and include both line and bar sections
for `cpi_trend`. The October 2026 published and independently calculated
annual rates both render as `%29,77`.

On 28 November 2026, all six slides were rendered and visually inspected with
LibreOffice Impress 26.8.0.0.alpha0. A copy with an edited table cell and an
edited chart workbook was reopened and rendered through the same editor.
The test covered the Turkish glyphs, table labels, chart legend, footers and
missing-value paths. LibreOffice may choose different tick intervals and
fallback fonts from PowerPoint. PowerPoint itself was outside this check.

```sh
soffice -env:UserInstallation=file:///tmp/tuik-impress-profile --headless \
  --convert-to pdf --outdir renders report.pptx
pdftoppm -png renders/report.pdf renders/slide
```
