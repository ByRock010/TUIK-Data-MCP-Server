# PowerPoint briefings (#93)

`build_tuik_report` can add an editable 16:9 briefing, `report.pptx`, to the
same artifact as `report.json`. Install the optional extra first:
`pip install 'tuik-mcp[pptx-out]'`, or `pip install -e '.[pptx-out]'` from a
checkout. Then call the tool with the regular [report spec](REPORTS.md) and
`pptx: true` at the tool-argument level.

The server validates the evidence and findings once, then draws the deck from
the exact `report.json` bytes it saves beside it. The renderer never fetches,
recalculates or writes prose. Every sentence, value, period and citation on a
slide comes from the validated report. Rejected reports save nothing, so they
have no deck. A marked draft carries `DOĞRULANMAMIŞ TASLAK` / `UNVERIFIED DRAFT`
on every slide, and each unverified finding keeps its own marker.

## Slides

| Report content | Slide |
| --- | --- |
| Title, subtitle, audience, the four report dates, `summary` findings | Title and key messages, continued on extra slides when long |
| `findings` section | Bullet slide |
| `table` section | Native table, at most 8 rows per slide, continued in order |
| `chart` section (`line` or `bar`) | Native PowerPoint line or clustered-column chart with its data embedded, plus the alt text |
| `methods` followed by `limitations` | One two-column slide, continued when long |
| `sources` | Source list |

A spec with a summary, one table, a trend chart, a comparison and the generated
sections gives the six-slide briefing of the issue: title and key messages, KPI
table, trend, comparison, methods and limitations, sources.

- **Editable.** All text is native text in text boxes, tables are native
  tables and charts are native charts. Chart gaps stay gaps (`dispBlanksAs =
  gap`): a missing period is never drawn as zero or interpolated. Series differ
  by colour, line dash and marker shape, so they stay distinguishable in grey.
- **Cited.** Every analytical slide has a compact note with the publisher, the
  resource IDs, the periods, the units and the retrieval date. Speaker notes
  carry the full citation of each item on the slide: value, period, observation
  or calculation ID, official URL, release date (or "unknown"), retrieval time,
  snapshot hash, and the formula and calculation ID of a derived value.
- **Readable.** The font is Calibri (Carlito, which has the same metrics, where
  Calibri is not installed), and nothing is smaller than 12 pt. Long bullets
  wrap and continue onto further slides; a bullet is never split.
- **Language.** Slide labels, the source note and the speaker-note labels
  follow the report language (`tr` or `en`). Values keep the report's own
  localized display text.
- **Deterministic.** The same `report.json` gives the same bytes. The
  document and each embedded chart workbook carry the report's retrieval time,
  and zip entries have a fixed date.

## Read-back and limits

After rendering, the server opens the saved deck again and refuses to return it
unless the following hold:

- every table cell equals the report table, header included;
- every chart has the report's categories, series names and values, and has an
  embedded workbook;
- every internal part reference resolves;
- there is no external reference: no remote image, link or template.

`report.json` is capped at 2 MiB, the deck at 40 slides and 8 MiB. The
artifact limits of [#88](ARTIFACTS.md) also apply. A failure leaves no partial
bundle. The `python-pptx` dependency is imported only for a `pptx: true`
request. Without the extra, that request fails with `pptx_unavailable`, and
every other call, including `build_tuik_report` without `pptx`, works as
before.

## Verified editors

- **LibreOffice Impress 24.2.7.2** (headless, through its UNO API) opened the
  Turkish and English decks. A title, a table cell and a chart value were
  edited, and the deck was saved back to PPTX. The edits read back with
  python-pptx.
- **Known difference.** When LibreOffice saves the deck again, it keeps the
  chart's cached values but drops the embedded workbook, so "Edit data" in
  PowerPoint would rebuild the sheet from the chart cache. A deck saved by
  this server always embeds the workbook.
- **Not verified:** Microsoft PowerPoint. The format is standard OOXML written
  by python-pptx 1.0.
