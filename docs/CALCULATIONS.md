# Series calculations (#89)

`calculate_tuik_series` computes derived values from one reviewed series. It
runs before a number enters prose, a chart or an Office file. It reads the
complete normalized snapshot of a
[`get_tuik_observations`](OBSERVATIONS.md) recipe, never a displayed page.
The response states the operation, the exact input observations, the output
unit and the rounding. The same result can be recomputed from the response or
from its saved bundle alone.

The schema is `tuik.calculation/1` in
[`models/calculation.py`](../src/tuik_mcp/models/calculation.py). The
arithmetic is the pure function `compute` in
[`calculations.py`](../src/tuik_mcp/calculations.py). The tool and an auditor
both call it.

Results are **derived**. TÜİK did not publish them. Cite the formula and the
input observation IDs with any result, and keep the official values in
`series[].inputs` next to them.

## Operations

| `operation` | Formula | Inputs | Output unit |
| --- | --- | --- | --- |
| `difference` | `x[t] − x[r]` | levels in persons, currency or terajoules | input unit and multiplier |
| `pp_difference` | `x[t] − x[r]` | percent values: rates, shares, ratios, change rates | `percentage_points` |
| `percent_change` | `(x[t] − x[r]) × 100 ÷ x[r]` | levels and indexes | `percent` |
| `moving_average` | `(x[t−w+1] + … + x[t]) ÷ w`, trailing | levels, indexes, rates and shares; not change rates | input unit |
| `extrema` | min and max over the range, every tie | any reviewed measure | input unit |
| `rebase` | `x[t] × 100 ÷ x[b]` | levels and indexes, up to 20 series | `index` with `base_period` b |

`basis` picks the reference `r` of the three change operations:

- `previous_period`: `t − 1`. January compares with December of the previous year.
- `same_period_previous_year`: the same month, quarter or half one calendar year
  earlier (`t − 12`, `t − 4`, `t − 2`, or `t − 1` for years). It never means "12
  rows back". A missing month makes the result missing; nothing else is used.
- `periods`: `periods=[reference, target]` gives one result. It takes no
  `period_from` or `period_to`.

`moving_average` needs an explicit `window` between 2 and 60. `rebase` needs an
explicit `base_period`. The base must have a valid, positive value in every
selected series; otherwise the whole call is refused.

## What is refused

- **One series, one frequency.** The filters must leave exactly one series. Only
  `rebase` takes several, up to 20. The error lists the dimensions and values
  that still vary. A selection with monthly and quarterly periods is refused;
  periods are never resampled.
- **Compatible inputs only.** Within a series, and across rebased series, every
  valid input must share unit kind, multiplier, currency, measure type and
  change basis, and seasonal and calendar adjustment. Index base periods must
  match within a series. An index whose base year changes is two series in the
  source. Rebasing is the declared way to compare such series.
- **No meaningless arithmetic.** These requests are refused, each with a hint
  toward the right operation:
  - a difference of percent values or indexes;
  - a percent change of a rate;
  - a percentage-point difference of levels;
  - a moving average of change rates;
  - any operation on an unknown unit or measure.
  Rates and indexes are never summed.

## Missing, undefined and coverage

Every period from `period_from` to `period_to` gets a result. By default that
is the series' own first to last period. A one-sided bound beyond the series is
kept: `period_from` after the last period gives a range of that one period,
reported missing and incomplete, never an empty range that looks complete. Lags and windows may read earlier
inputs, which are listed too. A result is:

- `valid`, with `value` and `rounded`;
- `missing`, with a reason naming the input period: no source record, or a
  `missing`, `confidential`, `invalid`, `unmapped` or `duplicate` observation;
- `undefined`: a percent change from a zero or negative reference.

Nothing is filled, skipped, interpolated or extrapolated. `coverage` reports,
for the output range, which periods have one valid observation, using the same
check as `get_tuik_observations`. When the coverage is incomplete, `extrema`,
`moving_average` and `rebase` add an issue. The issue says the result describes
only the covered periods, not the whole range. Duplicate records for one period
never enter a formula.

## Numbers and rounding

Inputs are the exact decimals of valid observations. Sums, differences and
products are exact at any number of digits; they never take the precision of the
caller's decimal context, so `recompute` gives the same strings anywhere.
Divisions are computed on the exact rational and rounded once, half-to-even, to
10 decimal places (`rounding.value_places`) before they are reported in `value`.
`rounded` applies `decimals` (default 2) with the same mode, for display only.
Values are strings in plain notation, so a client never reads them through a
binary float.

## Recomputing a result

Each `series[]` entry holds its `inputs` (ID, period, status, exact value) and
its `results` or `extrema`, each naming the input IDs it read. The output
budget is 64 KB by default. A larger calculation fails with
`query_limit_exceeded` unless `save=true`. With `save=true`, the artifact holds
the complete calculation. The inline response, artifact summary included, stays
within the budget: it keeps the counts, coverage and extrema values, and lists at
most 10 tied periods per extremum, with `tie_count` giving the full number
(`inline_complete: false`). If even that is too large, the reply falls back to
the totals, the issues and
one resource link to `calculation.json`; series and coverage gaps are then read
from that file. Under a smaller budget, down to the supported minimum of 1 KiB,
the reply is a receipt of about 0.9 KB: `status: ok`, the counts,
`coverage_complete`, `issue_count` and the `calculation.json` link. Every reply
fits the configured budget. The receipt is sized before the artifact is written,
so a budget that cannot hold it, for example with a very long resource ID, is
refused with `query_limit_exceeded` and nothing is saved.

`save=true` stores an artifact with these files:

| File | Contents |
| --- | --- |
| `calculation.json` | The whole response: specification, formula, units, rounding, inputs and results |
| `results.csv` | One row per result or extremum, with its input IDs |
| `inputs.csv` | Each input's published text, exact value and CSV record or workbook cell |
| `README.txt` | The formula, units, rounding, issues and citation |
| `source.<format>` | The unchanged official file, when retention is enabled |

```python
import json
from tuik_mcp.calculations import recompute

bundle = json.load(open("calculation.json"))
assert recompute(bundle) == bundle["series"]
```

## Example

The year-ago change of women's seasonally adjusted unemployment rate, in
percentage points:

```json
{
  "resource_id": "flow:DF_ISGUCU_AYLIK_TEMEL_ISGUCU_C+V1.0",
  "operation": "pp_difference",
  "basis": "same_period_previous_year",
  "filters": {
    "İşgücü Göstergeleri": "İşsizlik Oranı (%)",
    "Temel Yaş": "15+",
    "Mevsimsel Düzeltme": "Mevsim etkilerinden arındırılmış",
    "Cinsiyet": "Kadın"
  },
  "period_from": "2025-01"
}
```

## Not covered yet

- Growth over arbitrary spans (CAGR), ratios between two different series and
  chained or weighted aggregates need their own reviewed definitions.
- Calculations run on reviewed recipes only. Other resources keep the raw
  table tools, and the client must label its own arithmetic.
