# Evidence-bound reports (#91)

`build_tuik_report` builds one validated report that every renderer reads.
PowerPoint (#93), PDF (#94) and Word (#95) render the same `report.json`. They
never fetch, recalculate or write prose, so numbers and citations cannot drift
between formats.

The client sends a `ReportSpec` (`tuik.report-spec/1`) and the server returns a
`Report` (`tuik.report/1`). Both are defined in
[`models/report.py`](../src/tuik_mcp/models/report.py). The server does four
things:

- It resolves each evidence entry from a reviewed
  [observation recipe](OBSERVATIONS.md) or a deterministic
  [calculation](CALCULATIONS.md).
- It writes deterministic sentences for findings that have no text.
- It checks every number, date and comparison in any narrative against the
  evidence it is bound to.
- It saves the bundle.

No language model or model key is involved. The same spec over the same
snapshots gives the same `report_id`, the same evidence IDs and the same
sentences.

## Specification

| Field | Meaning |
| --- | --- |
| `title`, `subtitle`, `audience` | Plain text. They may mention evidence periods but state no other number. |
| `language` | `tr` (default) or `en`. Templates, headings, number separators and units follow it. |
| `source_cutoff` | Data cutoff date. A source released after it, or a period ending after it, is refused. |
| `on_unverified` | `reject` (default) or `mark`. See [statuses](#statuses). |
| `evidence` | 1–40 entries, each with a unique `key`. |
| `sections` | 1–12 sections in order. |

### Evidence

Every entry names a reviewed `resource_id` and `filters`. It may also give a
`snapshot_id`, a `label` without digits, and `decimals` (default 2).

| `kind` | Selection | Resolves to |
| --- | --- | --- |
| `observation` | `period`; the filters must leave one series | One published value and its observation ID (`obs:…`). |
| `calculation` | The `calculate_tuik_series` parameters plus `period`, or `extremum` (`min`/`max`) over `period_from`..`period_to` | One derived value, cited as `{calculation_id}:{period}` or `{calculation_id}:max`. |
| `series` | `period_from` and `period_to`, at most 240 periods | Published values of one series, for charts. |

A value that cannot be read is not an error. A missing, confidential, duplicate
or absent record makes the entry `missing`, with the reason, and the reason
becomes a limitation. A selection error is refused with the entry's key, for
example several series left, mixed frequencies, an unknown concept or value, or
an unreviewed resource.

### Sections

| `kind` | Takes |
| --- | --- |
| `summary`, `findings` | 1–8 `findings`: observation or calculation evidence, with optional `text` and `claims`. |
| `table` | `table.rows`: up to 20 observation or calculation keys. |
| `chart` | `chart.series`: 1–4 series keys sharing one unit, multiplier, currency and index base, and `type` `line` or `bar`. There is never a second axis. |
| `methods`, `limitations`, `sources` | Nothing. The server generates them, and appends any that the spec does not place. |

Any other combination is refused by schema validation. This includes content in
a generated section, a table holding findings, a chart of an observation, an
unknown key or a repeated generated section.

## Findings and claim checks

A finding without `text` gets a deterministic sentence for each entry:

```text
İşsizlik Oranı (%) (15+, Mevsim etkilerinden arındırılmış, Kadın), puan farkı,
2026-09 döneminde 2025-09 dönemine göre 0,30 yüzde puan arttı.
```

A finding with `text` is host narrative (`origin: "host"`). Every number in it
must be bound by a claim, and the claim's `quote` is the exact text of the
number with its sign, unit marker and scale word:

```json
{
  "evidence": ["rate", "gap"],
  "text": "Mart 2026'da işsizlik oranı %8,0 oldu; bir yıl önceye göre 0,6 yüzde puan geriledi.",
  "claims": [
    {"evidence": "rate", "quote": "%8,0"},
    {"evidence": "gap", "quote": "0,6 yüzde puan"}
  ]
}
```

Template sentences go through the same checks as host text. The checks are in
[`reports/claims.py`](../src/tuik_mcp/reports/claims.py):

| Check | Rejected example (evidence) |
| --- | --- |
| **Value.** The quote equals the evidence at the quoted precision, in whole units: `3.214 bin` and `3,2 milyon` are 3,214,000 persons. | `%8,5` (8.0); `3.214 kişi` (3,214 thousand persons) |
| **Sign.** An explicit sign must match. A negative value needs a minus sign or a decrease word in its sentence. | `+0,6 yüzde puan` and `0,6 yüzde puan oldu` (−0.6) |
| **Unit.** Percent needs `%`, `yüzde` or `percent`. Percentage points need `puan` or `percentage points`. Levels and indexes take neither. A currency written in, right before or right after the quote (`USD 3.214`, `$3.214`, `3.214 TL`) must be the evidence's currency, and a value that is not money takes none. A quote is a quantity: sign, number, scale and unit words. Any other word in it (`3.214 bin varil`) leaves the claim unverified. A unit attached right after the quote (`3.214 bin` + `ton`, `adet`, `kişi`, `TJ`) must be the evidence's unit; later prose such as `of people` or `(hanehalkı …)` is not read as a unit. | `8,0 puan` (a percent); `%0,6` (a difference in points); `3.214 bin ABD doları` and `$3.214 bin` (persons); `3.214 bin ton` (persons) |
| **Direction.** `arttı`, `geriledi`, `rose`, `down 0.6`, `unchanged`, … need a bound change in the same sentence with that sign. | `0,6 yüzde puan arttı` (−0.6); `oldu ve geriledi` with no change bound |
| **Comparison.** `daha düşük`, `higher than`, `above`, … need a bound change with that sign. `en yüksek`, `lowest`, `rekor`, … need a bound extremum of that kind. | `%8,0 ile 2025'e göre daha düşük` with only the level bound |
| **Dates.** Every period mentioned must be a period of the finding's evidence: target, reference, range or tie. This covers `2026-03`, `Mart 2026`, `2026 3. çeyrek`, `Q3 2026`, `2026-H1` and a bare year. The date nearest a quote in its clause (between commas, semicolons or conjunctions) must also be a period of that quote's own evidence. A date in a clause of its own next to the quote's clause (`Ocak 2026'da, oran %8,0`) is attached to it too. Otherwise, a date leading the sentence scopes every parallel statement after it. | `Şubat 2026'da` (2026-03); `Ocak 2026'da %8,0, Mart 2026'da %8,4` and `Ocak 2026'da, oran %8,0 oldu` (January and March swapped) |
| **Scope.** A province named in the text must appear in the bound evidence's labels or dimensions. `Türkiye`, `national` and similar words are refused for provincial evidence. An evidence `label` may name only the geography of its source series, since labels are not read as claims. | `İstanbul işsizlik oranı %8,0` (the national rate); `label: "İstanbul işsizlik oranı"` on national evidence |
| **Locale.** Numbers are read only with the report language's separators. Turkish is `1.234,5`; English is `1,234.5`. | `8.0%` in a Turkish report |
| **Unbound numbers.** Any other number fails. | `12 ayın en iyisi` |

Published labels and dimension values may contain digits, such as `15+` or
`2025=100`. They are recognised as labels and are not read as claims. A host
`label` may not contain digits. Titles, the subtitle, the audience and section
titles state no claims: a number other than an evidence period, or a direction
word, in them is a problem.

## Statuses

| `status` | When | Bundle |
| --- | --- | --- |
| `ok` | Every finding and title passed. | Saved |
| `rejected` | Something failed and `on_unverified` is `reject`. Each finding lists its `problems` and each claim lists its own. | Not saved |
| `draft` | Something failed and `on_unverified` is `mark`. Failing findings are `unverified`. `report.md` prefixes them with **DOĞRULANMAMIŞ TASLAK** / **UNVERIFIED DRAFT**, and a limitation counts them. | Saved |

## Dates

`timeline` keeps four dates apart, and none stands in for another:

- `latest_release`: the latest portal release date among the sources.
  `release_unknown` lists the sources with no release date. An unknown date
  stays unknown and is never replaced by the retrieval time.
- `latest_observation`: the latest period with a valid value.
- `retrieved_from` / `retrieved_to`: when the source files were downloaded.
- `data_cutoff`: the spec's `source_cutoff`.

## Limitations

The limitations section is generated. It always lists:

- missing values and their reasons;
- series, extrema and averages over incomplete coverage;
- calculation issues;
- seasonal or calendar adjustment the source does not state;
- adjusted and unadjusted values, or different index bases, in one finding, table or chart;
- each recipe's methodology notes;
- unknown release dates, and with a cutoff, that release before it cannot be confirmed;
- sources downloaded at different times;
- the number of unverified drafts.

## Untrusted text

Host text and source text are data. The spec refuses control, format (bidi
override) and unassigned characters. Text is never interpreted as HTML,
Markdown or a template, and no field causes a fetch. The Markdown reference
renderer backslash-escapes Markdown syntax, `<`, `>`, `&`, `:`, `/` and `.`.
As a result, no link, image, autolink, raw HTML or heading can come from text.
Office renderers must likewise insert text as literal runs.

## Bundle

| File | Contents |
| --- | --- |
| `report.json` | The complete `Report`: spec, evidence with IDs and sources, findings with claim checks, tables, chart data with alt text, methods, limitations, sources |
| `report.md` | Reference rendering with all text escaped |
| `evidence.csv` | One row per evidence entry and series point, with value, unit, inputs, snapshot and source |
| `README.txt` | Status, counts and limitations |
| `source-N.<format>` | The unchanged official files, when retention is enabled |

The inline reply stays within the output budget. It drops series points first,
then the evidence list, then every section except the failing findings, and
sets `inline_complete: false`. `report.json` is always complete.

## Example

This live spec gives an `ok` report. The derived year-ago change of the CPI
index, `calc:…:2026-10`, rounds to TÜİK's own published annual rate (`%29,77`).
The report also lists the CPI recipe note and the unknown release date as
limitations. Adding women's seasonally adjusted unemployment rate to the same
table adds two more: its calendar adjustment is not stated, and adjusted and
unadjusted values now appear side by side.

```json
{
  "title": "Fiyatlar ve işgücü",
  "language": "tr",
  "evidence": [
    {"kind": "observation", "key": "cpi_annual", "resource_id": "flow:DF_TUFE_SDMX_TT01+V1.0",
     "filters": {"COICOP 2018 (Amaca Göre Bireysel Tüketim Sınıflaması, 2018)": "TÜFE",
                 "Endeks-Değişim": "Bir önceki yılın aynı ayına göre (yıllık) değişim oranı (%)"},
     "period": "2026-10"},
    {"kind": "calculation", "key": "cpi_yoy", "resource_id": "flow:DF_TUFE_SDMX_TT01+V1.0",
     "filters": {"COICOP 2018 (Amaca Göre Bireysel Tüketim Sınıflaması, 2018)": "TÜFE",
                 "Endeks-Değişim": "Endeks"},
     "operation": "percent_change", "basis": "same_period_previous_year", "period": "2026-10"},
    {"kind": "series", "key": "cpi_trend", "resource_id": "flow:DF_TUFE_SDMX_TT01+V1.0",
     "filters": {"COICOP 2018 (Amaca Göre Bireysel Tüketim Sınıflaması, 2018)": "TÜFE",
                 "Endeks-Değişim": "Bir önceki yılın aynı ayına göre (yıllık) değişim oranı (%)"},
     "period_from": "2025-01", "period_to": "2026-10"}
  ],
  "sections": [
    {"kind": "summary", "findings": [{"evidence": ["cpi_annual", "cpi_yoy"]}]},
    {"kind": "table", "table": {"rows": ["cpi_annual", "cpi_yoy"]}},
    {"kind": "chart", "chart": {"series": ["cpi_trend"]}}
  ]
}
```

## Not covered yet

- Charts are bounded data with alt text and a table fallback. #90 adds chart
  specifications, styles and vector formats over the same data.
- Claim checks cover the number, sign, unit, date, scope and comparison words
  listed above. Scope covers provinces and national wording; other places
  (regions, districts, countries) are not recognised yet. They do not judge causal or qualitative wording; such text should not
  carry numbers.
- Reports cite reviewed recipes only. Raw tables and PDF text stay outside the
  report until a recipe covers them.
