# Source-table extraction audit (#86)

On 23 November 2026 we audited 60 public TÜİK exports across six domains against
the production parser at `b7bdb25`. This is a targeted sample of source families
for the institutional-output roadmap. The classifications apply to the pinned
files and sampled observations. The same title can cover different bytes,
column structures and vintages.

## Selection and replay

[Selection rules](selection.json) give the order of the CSV dataflows and the
bulletin IDs and titles used for fallback. For each domain retain ten different
files, preferring the highest available version of each selected dataflow.
Reserve positions 3, 6, 9 and 10 among the ten successful picks for the held-out
split. The resulting set is 40 CSV, 19 XLS and one XLSX, with 36 development and
24 held-out files. The audit did not use these files to tune production parser
rules. The collector excludes hashes already in the repository's fixture
manifest and base CSV fixture, as well as duplicates within the audit.

[Manifest](manifest.json) records the stable dataflow or bulletin page, table
title, source date and period, UTC retrieval time, bytes and SHA-256. For
bulletins with multiple files, use the table title on the release identified by
the manifest. [Exclusions](excluded.json) preserve the attempted tables and
failure reasons. The August industrial contribution attachment matched
a previously retained hash and was excluded. The cached catalogue, metadata requests and
source bytes can be reused on a second run. The collector spaces general
requests by at least 0.65 seconds and bulletin file requests by at least 6.25
seconds, with a 16 MiB research cap. It uses public endpoints on the
[TÜİK Data Portal](https://veriportali.tuik.gov.tr/).

```sh
python -m pip install -e .
python docs/verification/issue-86/collect.py /tmp/tuik-audit-inputs
python docs/verification/issue-86/audit.py /tmp/tuik-audit-inputs > /tmp/tuik-audit.json
gzip -dc docs/verification/issue-86/evidence-2026-11-23.json.gz > /tmp/tuik-audit-published.json
```

For an exact replay, use the pinned manifest and the corresponding retained
`*.bytes` files. The current portal may serve updated data under the same IDs.
The checker requires a matching hash before reading the bytes. The repository
retains the sampled cells and outcomes. The full upstream files stay in the
local download cache.

## Independent checking method

- **CSV.** The standard library CSV reader decodes the semicolon-delimited
  export independently from the production CSV parser. Inspect five numeric
  records spread across each file in source order, matching the literal
  observation, row dimensions, labels and period. Record coordinates are
  one-based CSV records including the header. Compare total record counts,
  empty observations and literal missing markers separately. Unit context
  comes from an explicit unit column or a source indicator label such as
  `Bin Kişi`, `Endeks` or a label with `%`.
- **XLS/XLSX.** Read the raw cell grid, native cell formats and merged ranges
  directly with `xlrd` and `openpyxl`. Sample numeric cells from rows with
  observation-like values, following physical sheet, row and column order.
  Join to a uniquely matching parser row using original text labels and the
  other numeric cells on the same row. Compare values at the source format's
  displayed precision. Retain the sheet, A1 address, raw value, candidate
  header stack, row labels, period and unit clues. A label match means one
  source header fragment is visible in the parsed column label. A period
  match means the detected source period is explicit in the corresponding
  output column or row. These context checks do not establish the meaning of
  the complete header stack.
- **Coverage and statuses.** `exact` requires the sampled CSV values, complete
  row context, observation periods and source unit context to agree, together
  with whole-file row counts and literal missing-marker counts. `inferred`
  records traced values with some context derived heuristically, including
  spreadsheet header and row alignment. `partial` is available for input that
  parses with unresolved sampled values or reduced CSV coverage.
  `unsupported` records the default acquisition limit, a parser error or raw
  output. An explicitly marked offline probe can check the cached CSV bytes
  above the production download cap, using the standard parsing and scan
  limits.

Five numeric cells or records were selected per file. The grids cover long
CSV, wide spreadsheets, merged header hierarchies, bilingual headers and
multiple vintages, including the dedicated industrial contribution revision
history. The four exclusions above 16 MiB and the duplicate are separate from
the retained corpus. The inspected public workbooks each exposed one sheet.
The official multi-sheet check below adds a separate held-out workbook.

## Supplemental held-out multi-sheet workbook

On 25 November 2026 we retrieved the official [2021 ADNKS workbook](
https://www.tuik.gov.tr/indir/duyuru/favori_raporlar.xlsx). It contains six
worksheets for provinces, districts, metropolitan municipalities, municipalities,
neighborhoods and villages. The 3,736,479-byte file is outside the fixed 60-file
sample. Its snapshot hash, layout and cell evidence are in
[multisheet-evidence.json](multisheet-evidence.json).

The default path rejects the workbook at the 200,000-cell safety limit.
An offline replay with a 700,000-cell cap and all other parser limits unchanged
produced six tables. Sheet names and order agree with the independent read.
All six counts chosen near their sheet midpoints and one additional neighborhood
count traced to a unique parsed row. The additional row has the published total
but suppresses the male and female counts with `C`. Both `C` cells and three
provincial `-` markers remained literal. The counts are people as of the
workbook's title date, 31 December 2021. The title supplies the date and subject
for the whole sheet and does not establish an observation period column.

| Worksheet | Independently counted source rows | Parsed rows | Sample |
| --- | ---: | ---: | --- |
| İL NÜFUSU | 82 | 82 | C49, 2,033,441 |
| İLÇE NÜFUSU | 974 | 974 | E504, 39,486 |
| BÜYÜKŞEHİR B. NÜFUSU | 30 | 30 | C22, 1,434,357 |
| BELEDİYE NÜFUSU | 1,360 | 1,360 | J687, 3,800 |
| MAHALLE NÜFUSU | 32,125 | 32,125 | K16069, 554 and K12040, 22 |
| KÖY NÜFUSU | 18,211 | 18,211 | H9112, 139 |

The offline classification is `inferred`. The checker retains the complete
sampled header path, including the merged ancestors in the province and district
sheets. Some parser labels expose only the innermost header. The production
limit remains in force and the offline result does not confer normalized
eligibility on other years or workbooks.

```sh
python docs/verification/issue-86/multisheet.py /tmp/favori_raporlar.xlsx \
  > /tmp/tuik-audit-multisheet.json
diff -u docs/verification/issue-86/multisheet-evidence.json /tmp/tuik-audit-multisheet.json
```

## Outcomes

| Domain | Development (6 each) | Held out (4 each) |
| --- | --- | --- |
| Prices | 3 exact, 2 inferred, 1 unsupported | 2 exact, 2 inferred |
| Labour | 4 exact, 2 inferred | 2 exact, 2 inferred |
| Population | 2 exact, 4 inferred | 4 inferred |
| Foreign trade | 6 exact | 2 exact, 2 inferred |
| Housing sales | 6 inferred | 4 inferred |
| Industry | 2 exact, 3 inferred, 1 unsupported | 4 inferred |
| Total | 17 exact, 17 inferred, 2 unsupported | 6 exact, 18 inferred |

The normal parser path traced **290 of 300** sampled values. The two
unsupported CSVs returned no observations under the 10 MiB default. Direct offline extraction of the cached bytes traced all ten additional
values. These runs are recorded under `offline_probe_above_download_cap`
in the [coordinate evidence](evidence-2026-11-23.json.gz).

| Check | CSV (200 samples) | Excel (100 samples) |
| --- | --- | --- |
| Layout and coverage | 38 tables with matching headers and record counts. Two above the download limit. | 20 source sheets, all emitted as tables. 16 had merged ranges. The evidence retains source row counts, observation-like rows and parser row counts per sheet. |
| Labeling | All 190 available sample rows preserved the original labels and field text. | All 100 sample columns matched a source header fragment. Full hierarchical scope is unresolved in some columns. |
| Period | 190 matched. Ten had no parser row because of the limit. | 36 source-period clues visible in parser output. Other samples have inferred context, no unambiguous period in the sampled cell region or a sheet-wide reporting period. |
| Unit | 135 independent unit clues, including ten on capped files. | 52 independent unit clues. The file records each clue and the parser's table-level unit separately. |
| Markers | No explicit marker in the observation field. 646 blanks preserved on readable tables, plus 5,111 frozen in one capped file. | All 13 explicit markers on matched sample rows were preserved. |

### Examples and failure evidence

- **CPI weights.** In `Tablo 4!D580`, code `09221`, the raw value is
  `0.15975217443257553` and parser row 576 preserves its displayed `0.1598`
  in `Ağırlık (%) Weight (%)`. The parser's workbook unit is `2025=100`.
  A normalized report needs the column-specific percent unit and the COICOP
  code identity. [October 2026 CPI workbook](https://veriportali.tuik.gov.tr/tr/press/58291).
- **Industry headers.** In the July headline workbook, `s1!ES142` is the
  annual change for paper manufacturing (C17), calendar-adjusted. Output
  retains `11.0` and the annual-change label. The printed parser column label
  lacks both C17 and the adjustment qualifier. The table-level `2021=100`
  describes the index base, while the cell is a change in percent.
  [July 2026 industrial production release](https://veriportali.tuik.gov.tr/tr/press/58188).
- **Default byte limit.** Price-group indices were 14,439,860 bytes and main
  industrial production indices were 14,364,209 bytes. Their 85,150 and
  57,624 source records respectively are counted independently. Both receive
  `response_too_large` at 10 MiB. The bounded retrieval work in #105 is
  relevant to the second export. [CPI dataflow](https://veriportali.tuik.gov.tr/tr/databrowser/tuik/TR,DF_TUFE_SDMX_TT09,1.0), [industrial production dataflow](https://veriportali.tuik.gov.tr/tr/databrowser/tuik/TR,DF_SANAYI_URETIM_ENDEKS_ANA_C,1.0).
- **Housing.** All 50 values traced. None of the ten files met the combined
  unit, period and structure criteria for `exact`. County tables contain
  source geography and year/month context that needs a stable mapping.
  [November 2025 housing sales release](https://veriportali.tuik.gov.tr/tr/press/54149).

## Source families for normalized reporting

The following are candidates for #87, scoped to the evidence above. A report
should check the recorded schema, unit, period and source vintage for each
selected observation. Use a new check when those fields or the source shape change.

| Domain | Verified family or current gate |
| --- | --- |
| Prices | Main CPI groups, index and change variants in [`DF_TUFE_SDMX_TT01`](https://veriportali.tuik.gov.tr/tr/databrowser/tuik/TR,DF_TUFE_SDMX_TT01,1.0). Preserve measure and base year per selection. |
| Labour | National core monthly indicators in [`DF_ISGUCU_AYLIK_TEMEL_ISGUCU_C`](https://veriportali.tuik.gov.tr/tr/databrowser/tuik/TR,DF_ISGUCU_AYLIK_TEMEL_ISGUCU_C,1.0), with people and rates distinguished and adjustment selected explicitly where present. |
| Population | Age dependency ratios in [`DF_ADNKS_T24`](https://veriportali.tuik.gov.tr/tr/databrowser/tuik/TR,DF_ADNKS_T24,1.1). Absolute population requires its own source unit gate. |
| Foreign trade | Gold-excluded trade terms in [`DF_ALTINSIZ_DTH`](https://veriportali.tuik.gov.tr/tr/databrowser/tuik/TR,DF_ALTINSIZ_DTH,1.0), preserving the series and period. Dollar values use the separately verified [`DF_ALTINSIZ_IHRACAT_BDE`](https://veriportali.tuik.gov.tr/tr/databrowser/tuik/TR,DF_ALTINSIZ_IHRACAT_BDE,1.0) family. |
| Housing sales | Hold for explicit dwelling/workplace classification, count versus share, geography, and vintage checks. |
| Industry | Annual final industrial energy use in [`DF_SANAYI_ENERJI_YILLIK_NIHAI_TUKETIM_TJ`](https://veriportali.tuik.gov.tr/tr/databrowser/tuik/TR,DF_SANAYI_ENERJI_YILLIK_NIHAI_TUKETIM_TJ,1.0), in terajoules. Production index and change rates require separate checks. |

## Ranked parser follow-ups

1. **Carry source-coordinate and full hierarchy metadata for workbook
   observations.** Keep merged ancestor labels, period keys and the applicable
   unit per cell, with source versus inferred metadata distinguishable. Start
   with the July industry workbook sample and the CPI weights source. This
   enables safe category and unit selection for #85 tasks M02, A03, L02 and L03.
2. **Use bounded retrieval for selected series in oversized exports.** Verify
   completeness of filtered reads against the frozen source. Extend the
   industrial #105 evidence and check the CPI detailed fallback before using it
   for A03. M02 and A03 can still use explicitly verified bulletin or small-flow
   routes.
3. **Attach reference period and release vintage as distinct fields.** Check
   months and reported-versus-revised values on the same source family.
   Distinguish the scope of housing counts and shares and confirm unit and
   geography before enabling #87 for L04 and L06. Apply the same vintage
   distinction to the requested series in A04 and A05.
4. **Keep codes and explicit missing markers in workbook output.** Retain
   leading-zero codes, preserve displayed precision beside raw values, and test
   sheet boundaries beyond the audited ADNKS workbook. This protects grouped
   comparisons such as A03 and M02 and count-based analysis such as L02 and L03.
