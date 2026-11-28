# Downloadable and reusable charts

`chart_tuik_data` draws a chart from the same CSV or workbook snapshot and
selection as `get_tuik_data`. Inspect the resource for exact column names first.
A default line or bar request still returns a compact inline PNG and the JSON
summary, with the same legacy saved bundle when `save=true`.

Use `chart_options` to include a versioned plot specification and select
additional formats. One request can save PNG, SVG and PDF from the same
snapshot and plotted data. This example selects seasonally adjusted national
unemployment rates for ages 15 and above.

```json
{
  "resource_id": "flow:DF_ISGUCU_AYLIK_TEMEL_ISGUCU_C+V1.0",
  "x": ["Zaman"],
  "y": ["Gözlem Değeri"],
  "series": "Cinsiyet",
  "chart": "line",
  "filters": {
    "İşgücü Göstergeleri": "İşsizlik Oranı (%)",
    "Temel Yaş": "15+",
    "Mevsimsel Düzeltme": "Mevsim etkilerinden arındırılmış"
  },
  "last": 12,
  "save": true,
  "chart_options": {
    "formats": ["png", "svg", "pdf"],
    "theme": "publication",
    "series_units": ["%", "%", "%"],
    "source_note": "Resmî TÜİK yayını. Kaynak ve seçim ayrıntıları manifest dosyasındadır."
  }
}
```

Inspect the exact filters for your flow before charting. SVG and PDF require
`save=true`. The response carries
MCP resource links for the files, and carries an inline image when PNG is
selected. Read the links with `resources/read`. Vector files retain editable
text and draw chart marks as vectors. SVG viewers use DejaVu Sans when available.
The PDF embeds its Turkish-capable font. The portable table and alt text serve
clients without image or vector support.

| Kind | Selection and scale |
| --- | --- |
| `line` | Categorical x in row order, or oldest-to-newest for recognised periods of one frequency. In enhanced charts a skipped year, half-year, quarter or month breaks the line. Irregular daily files preserve their selected date labels and explicit gaps. |
| `bar` | Vertical bars grouped by series on one scale, with zero as the baseline. Enhanced mode accepts up to 60 categories. Legacy PNG still warns above 60. |
| `horizontal_bar` | Horizontal groups with an explicit zero baseline. Up to 24 categories. |
| `ranked_bar` | One series and up to 24 unique categories. Sort highest to lowest, placing gaps last. Ties retain the earlier point order. The zero baseline separates positive and negative values. |
| `scatter` | One numeric x column and up to 8 series. Every selected x value must be valid. Set `x_unit` when known. Blank y values are gaps. Points describe association and have no causal interpretation. |
| `heatmap` | Up to 8 series and 24 unique categories. One continuous scale. Gray cells marked × represent missing or invalid values. |

The default theme uses the Okabe–Ito palette for series and Cividis for
heatmap values. `monochrome` uses a gray scale. Enhanced multiple-series
charts distinguish groups using distinct dashes, hatching or point markers
where appropriate. Enhanced bars mark gaps with × at the baseline. Full
labels are retained in the data when display labels
are shortened to fit. `source_note` accepts up to 300 characters and wraps in
the caption.

A single common y unit applies to enhanced charts. A table-level unit can
supply it when every selected series is expressed in that unit. If it is
absent, pass `series_units` once per plotted series using the same unit.
The order follows the y columns or the first appearance of groups after
selection. Declarations that disagree with the source or each other are
refused. Percent notation in source cells and headers also checks the common
unit. The specification distinguishes source metadata from a caller
declaration. Check inspected measure, unit, index base and adjustment when
choosing comparable input rows. Separate charts are required for different
units. Comma, dot, grouping and ambiguous locale follow the rules from
[table queries](TABLE_QUERIES.md). A missing, invalid or non-finite value
becomes a gap. Zero is a valid observation.

## Bundle contract

Saved enhanced charts include the requested image files, `chart-spec.json`,
`chart-data.csv`, `README.txt`, the source and `manifest.json`. The manifest
pins the file and selection, and the spec records `tuik.chart-spec/1`,
the resource and snapshot, retrieval time, theme, source note and alt text.
The plotted data includes x labels, numeric x coordinates for scatter, the
x order, underlying values and their states, and positions where the line
breaks across unobserved periods. The CSV offers a simpler fallback with one
column per series. Nulls and blank fields represent gaps. The pinned source
retains the published text and exact decimal precision.

To use the same plotting input in Python:

```python
from pathlib import Path

from tuik_mcp.charts import render_spec
from tuik_mcp.models.chart import ChartSpec

spec = ChartSpec.model_validate_json(Path("chart-spec.json").read_bytes())
content, _, _ = render_spec(spec, "svg")
Path("replay.svg").write_bytes(content)
```

The server bounds charts at 5,000 positions and 8 series, with the lower
type-specific limits above. PNG uses a fixed 1,100 × 616 pixel canvas with
a 512 KiB file cap. SVG, PDF and serialized ChartSpec each have a 2 MiB cap.
Artifact storage also applies its existing file, bundle and retention
budgets. Narrow the input if an output exceeds a limit. Install the
optional `viz` extra to render. The existing table tools remain available
without it. See [artifact retrieval](ARTIFACTS.md).
