# Reviewed statistical observations

`get_tuik_observations` is an opt-in path for a bounded series from the
[official-source audit](verification/issue-86/README.md). Start with an empty
call or a single `mapping_id`. Each listed series has its frequency, exact
selectors and a verified example period. Raw inspection and queries still
support their existing CSV, XLS and XLSX inputs.

## Families and scope

| Mapping ID | Source and verified examples |
| --- | --- |
| `cpi` | [National CPI](https://veriportali.tuik.gov.tr/tr/databrowser/tuik/TR,DF_TUFE_SDMX_TT01,1.0), 2025 base, October 2026. Index 140.77, monthly change 2.58% and annual change 29.77%. |
| `labour` | [Core monthly labour indicators](https://veriportali.tuik.gov.tr/tr/databrowser/tuik/TR,DF_ISGUCU_AYLIK_TEMEL_ISGUCU_C,1.0), national and age 15+. September 2026 unadjusted and seasonally adjusted unemployment are 8.5% and 8.4%. The matching employment levels are 32,681 and 32,445 thousand persons. |
| `age-dependency` | [ADNKS age dependency](https://veriportali.tuik.gov.tr/tr/databrowser/tuik/TR,DF_ADNKS_T24,1.1), annual national and Ankara province total dependency ratios. 2025 values are 45.9724041956659% and 40.4653214414051%. |
| `trade-terms` | [Total foreign trade terms excluding gold](https://veriportali.tuik.gov.tr/tr/databrowser/tuik/TR,DF_ALTINSIZ_DTH,1.0), 2015 base. Monthly, quarterly and annual series are selected separately. December 2025 is 96.70166733244525 in the published ratio scale. |
| `industrial-energy` | [Industrial final energy consumption](https://veriportali.tuik.gov.tr/tr/databrowser/tuik/TR,DF_SANAYI_ENERJI_YILLIK_NIHAI_TUKETIM_TJ,1.0), national annual total. 2025 is 1,722,855.6 terajoules. |
| `cpi-basket-2026` | [2026 CPI basket weights XLSX](https://veriportali.tuik.gov.tr/tr/press/58291), `Tablo 4!D5`. COICOP code `01` keeps its leading zero. Food and non-alcoholic beverages has weight 24.4444%. |
| `population-2025` | [2025 annual population by province and sex XLS](https://veriportali.tuik.gov.tr/tr/press/53899), `t3!C11`. Ankara male population is 2,911,853 persons. The total settlement header expands from `B3` into `C3`. |
| `industrial-change-2026-09` | [September 2026 industrial production XLS](https://veriportali.tuik.gov.tr/tr/press/58189), `s2!C8` and `s2!D8`. Manufacturing's annual change is about 2.59%. Its contribution to the industry's annual change is about 2.28 percentage points. Footnote `A52` records the calendar adjustment. |
| Housing sales | Unavailable in mapping version 1. The audit leaves housing unit, type, geography and revision gates open. |

National and province observations carry different geography levels. The Ankara
crosswalk uses code `TR510` from the [İBBS edition audited in #106](
adr/0007-regional-data-and-boundaries.md). The `TR51` İBBS level 2 region is a
separate geography. The mappings do not assign adjustment from names or combine
series of different frequencies.

## Selection and provenance

`selection` requires `series`, `start` and `end`. Use `YYYY-MM`,
`YYYY-Qn` or `YYYY` according to the selected frequency. The inclusive range
contains at most 120 periods and is scanned without truncation under the existing
file and query budgets. Choose a shorter range on `observation_output_limit`.
An optional `snapshot_sha256` can require the same bytes on a later call.
Workbook mappings always require their audited hash. If the publisher changes the
bytes, inspect the raw file and review the mapping before using the new snapshot.

- `snapshot` includes the resource ID, publisher, source page, download time and
  SHA-256 of the input. A bulletin's publication date is separate from its
  observation period. A missing publication date stays unknown. The hash identifies
  the observed vintage even if the source does not publish a revision date.
- `origins` distinguishes source-declared fields, recipe interpretations, the
  caller's choice and unknown values. Constant dimensions remain in `dimensions`.
  A missing adjustment remains unknown.
- `original_value` preserves the CSV field or the workbook cell's stored numeric
  text or literal marker. `numeric_value` is a decimal string at the published
  scale. A count reported in thousand persons retains its original number with
  `measure.multiplier=1000`. Percent, percentage points and index points have
  distinct units. Explicitly confidential numeric values are withheld.
- A CSV locator is the one-based logical record and column, including the header
  record at position 1 and excluding blank separator lines. It includes original
  header names. An Excel locator gives the sheet and A1 address. Header context
  lists the expanded coordinate and its merge anchor when one was used.
- `status=complete` means every requested period has one validated value.
  `incomplete` includes missing periods, markers, confidentiality and unrecognized
  source attributes or number formats. Invalid observations carry no numeric
  value. A duplicate returns `ambiguous_observation`. Changed columns return
  `unsupported_observation_schema`. Use a complete result and check the relevant
  units, geography, base and adjustment before combining selections.

On 25 November 2026 all 17 example series were verified through the production
catalogue, download, parsing and normalization path using public source exports.
The workbook locations and hashes are also recorded in the source-table audit
manifest.
