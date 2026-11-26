# Normalized observations (#87)

`get_tuik_observations` returns typed statistical observations for a small set
of reviewed TÜİK source families. It is opt-in and sits beside the raw table
tools: `get_tuik_data`, `inspect_tuik_data` and `chart_tuik_data` still return
the published strings unchanged. Calculations (#89) and report exports read this
contract instead of guessing units, periods or geography from column names.

The schema is `tuik.observation/1` in
[`models/observation.py`](../src/tuik_mcp/models/observation.py). The recipes are
in [`observations/recipes.py`](../src/tuik_mcp/observations/recipes.py).

## What an observation carries

| Field | Meaning |
| --- | --- |
| `id` | A stable observation ID for the snapshot and source location. |
| `value_text` | The published CSV field (`8,4`) or a text representation of the workbook cell's stored value. |
| `value` | Exact decimal as a string (`"8.4"`), only when `status` is `valid`. |
| `status`, `reason` | `valid`, `missing`, `confidential`, `invalid`, `duplicate` or `unmapped`, with the cause. |
| `locator` | Format and source coordinates. CSV gives a one-based record counting the header as record 1 and the value column's name. Excel gives sheet, A1 cell and header cells with any merge anchor. |
| `period` | `value`, `frequency` (`month`, `quarter`, `half`, `year`) and the published frequency label. |
| `geography` | Published label, level, code and its system. National measures use ISO 3166-1. Provinces use the two-digit Turkish province code. |
| `unit` | `kind`, published `label`, `multiplier` as a power of ten, `currency` and an index's `base_period`. |
| `measure` | `type` (`level`, `index`, `rate`, `ratio`, `change`, `share`) and a change's `basis`. |
| `adjustment` | `seasonal` and `calendar`: `adjusted`, `not_adjusted`, `not_applicable` or `unknown`. |
| `dimensions` | Other dimensions that vary within the file, by concept name. |
| `source_status`, `notes` | Published observation and confidentiality status text and any reviewed source notes. |

Every structured field has a `source`:

- `source`: published in the cell or row, such as a period or an explicit seasonal adjustment column.
- `recipe`: comes from the reviewed mapping, such as a province code, a unit kind or a CPI adjustment declared by family.
- `user`: an explicit caller selection. The response's `selection` echoes these.
- `unknown`: no source or recipe states it. Trade indices do not declare adjustment, so it stays `unknown`.

Concepts with one value across the file go to `constant_dimensions` as metadata,
not to each observation. `dimension_values` lists the varying values. Both use
concept names without the code suffix some exports append. For example, the
same flow can publish `Zaman (2015-01)` on one day and `Zaman` on another.

## Guarantees

- **Scoped completeness.** The whole CSV is read within the row and cell
  budgets. A budget overrun or malformed record returns an error. For a workbook,
  `source_scope=reviewed_cells` covers the explicitly mapped cells in the pinned
  snapshot. `complete` describes reading that scope. `counts` cover the entire
  selection within the scope, including records on later pages.
- **Changed sources are refused.** A recipe lists the CSV columns it expects.
  An extra, missing or doubled concept returns `schema_changed`. A workbook's
  bytes must match the audited hash and changed bytes return `snapshot_changed`.
- **No silent values.** A measure, unit, geography, frequency or status label
  without a reviewed entry is `unmapped`. An empty cell, a marker such as `-`, a
  confidential cell or a number in another notation (`1.234,5`) never gets a
  `value`. A non-empty SDMX attribute the recipe expects empty, such as
  `Birim Çarpanı`, is also `unmapped`, because it would change the meaning.
- **Duplicates are not chosen.** Records with the same period and dimensions are
  all `duplicate`, with the record numbers. This also applies when one of the
  records fails on its own: a valid `8,4` beside an `invalid` or `unmapped` twin is
  `duplicate` with no value, and the failing twin keeps its own reason plus the
  collision.
- **Periods match frequencies.** `2026` under `Aylık` or `2026-03` under `Yıllık`
  is `invalid`. A period range needs one frequency in the selection.
- **Distinct units stay distinct.** Percent and percentage points, persons and
  thousands of persons (`multiplier: 3`), index levels and percent changes,
  and adjusted and unadjusted series are separate fields, never merged.
- **Geography is never inferred from a column name.** National and province
  levels come from exact reviewed labels. The 81 official two-digit province
  codes are recipe-defined. An unknown place label is `unmapped`.

## Reviewed families

The recipes cover the verified families from the [#86 audit](verification/issue-86/README.md).
Each recipe pins exact flow IDs and versions; a new version is `unsupported`
until it is reviewed.

| Domain | Recipe | Resource | Notes |
| --- | --- | --- | --- |
| Prices | `cpi-main-groups` | `flow:DF_TUFE_SDMX_TT01+V1.0` | Index (2025=100) and four change bases. Unadjusted by recipe: TT05 is the adjusted family. |
| Labour | `labour-monthly-core` | `flow:DF_ISGUCU_AYLIK_TEMEL_ISGUCU_C+V1.0` | Thousands of persons and rates; seasonal adjustment from the published column. |
| Population | `population-age-dependency` | `flow:DF_ADNKS_T24+V1.1` | National and 81 provinces. `Yaş Grubu` changes label between years and does not separate observations. |
| Foreign trade | `trade-terms-gold-excluded` | `flow:DF_ALTINSIZ_DTH+V1.0` | Index (2015=100); monthly, quarterly and annual in one file. |
| Foreign trade | `trade-export-unit-value-gold-excluded` | `flow:DF_ALTINSIZ_IHRACAT_BDE+V1.0` | Index of US dollar unit values, not dollar amounts. |
| Industry | `industry-final-energy-tj` | `flow:DF_SANAYI_ENERJI_YILLIK_NIHAI_TUKETIM_TJ+V1.0` | Terajoules; 343 confidential cells stay empty. |
| Housing sales | none | `DF_SATIS_SEKLI_SATIS_DURUMU_V3` and related flows | `unavailable`: classification, count versus share, geography and vintage are not verified. |

The six CSV recipes were checked against 57,208 records in the public
exports on 25 November 2026. The live recipe test repeats this contract check.

### Audited workbook cells

| Recipe | Bulletin | Reviewed cells |
| --- | --- | --- |
| `cpi-basket-2026` | [October CPI](https://veriportali.tuik.gov.tr/tr/press/58291) | `Tablo 4!D5`, food weight of 24.4444%. |
| `population-2025` | [2025 ADNKS](https://veriportali.tuik.gov.tr/tr/press/53899) | `t3!C11`, 2,911,853 male residents in Ankara. The total settlement header for `C3` comes from merge anchor `B3`. |
| `industrial-change-2026-09` | [September industrial production](https://veriportali.tuik.gov.tr/tr/press/58189) | `s2!C8` and `s2!D8`, the manufacturing change in percent and its contribution in percentage points. `A52` supplies the calendar-adjustment note. |

Each discovery item gives the reusable file ID, workbook format, exact snapshot
hash and an example period and filter. The contract exposes `publication_date`
when available, separately from `source_updated_at`, retrieval time and the
observation period. A new hash is a distinct source vintage. A workbook recipe
must be reviewed again before it accepts revised bytes.

## Calls

List the recipes and held families:

```json
{}
```

Read one series, then the next page from the same snapshot:

```json
{
  "resource_id": "flow:DF_ISGUCU_AYLIK_TEMEL_ISGUCU_C+V1.0",
  "filters": {
    "İşgücü Göstergeleri": "İşsizlik Oranı (%)",
    "Mevsimsel Düzeltme": "Mevsim etkilerinden arındırılmış",
    "Temel Yaş": "15+",
    "Cinsiyet": "Toplam"
  },
  "period_from": "2024-01"
}
```

```json
{"cursor": "obs1.eyJvIjo1MCwi...", "limit": 50}
```

`next_cursor` binds the resource, snapshot hash, recipe ID and version, full
selection and position. Pass it alone, optionally
with `limit`. A cursor sent with other filters, a period bound, another resource
or another `snapshot_id` returns `invalid_cursor`. The cursor checksum detects
accidental changes to the token. If
the file has changed since the first page, the response is `snapshot_changed`;
restart without the cursor. A recipe revision returns `recipe_changed` even if
the downloaded bytes are unchanged. `snapshot_id` on a new query pins it to a
file you have already read.

A filter on an unknown concept or an unpublished value returns `invalid_input`
with the available concepts or values. Pages shrink to the 64 KB output budget.
Follow `next_cursor` rather than counting rows.

## Further scope

The workbook recipes cover the specific coordinates in the table above.
Inspect other workbook cells through the raw table tools. Production indices
and other audited CSV families need separate observation recipes. Oversized
exports depend on bounded retrieval in #105.
