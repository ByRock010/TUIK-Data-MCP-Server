## Goal

Produce publication-quality charts whose data, labels and scale choices can be reused in every renderer.

## Context

`chart_tuik_data` already produces PNG line/bar charts and summaries (#72). Extend that capability rather than opening a duplicate implementation. An image alone is insufficient for editable Office charts or audit.

## Proposal

Separate a validated ChartSpec/data specification from rendering. Keep current PNG and signature behavior; add SVG and vector PDF output through the artifact service. Support line, horizontal/ranked/grouped bar, scatter and a bounded heatmap where their inputs are valid. Specify sorting, units, gaps, labels, title, source notes, color palette and alt text. Use a shared theme contract; charts remain deterministic and use real data rather than generative-image numbers.

## Acceptance criteria

- [ ] Existing PNG calls remain compatible and new formats reference the same selected observations and snapshot.
- [ ] Bar baselines, time ordering, ties, negative values, missing gaps, misleading numeric locale and multiple-series units have explicit validated behavior.
- [ ] Accessible palettes and text descriptions are included; scatter/correlation views do not imply causation; no automatic dual-axis chart.
- [ ] Vector files preserve legible Turkish glyphs and text; long titles/notes and dense series are rendered and visually checked.
- [ ] ChartSpec includes the underlying bounded data and a portable fallback for formats without native support.
- [ ] Point/series/pixel/byte limits, unsupported chart combinations and optional dependency failures are tested and documented.

## TÜİK sources involved

Existing CSV/XLS/XLSX sources and [current chart implementation](https://github.com/ByRock010/TUIK-Data-MCP-Server/issues/72).

## Planning

- **Phase:** 2 — institutional outputs
- **Priority:** high; **relative size:** L (not a delivery-date estimate).
- **Release scope:** first institutional-output slice.

## Dependencies

- **Blocked by:** #87, #88
- **Related:** #72
- **Epic:** #84
- **Roadmap:** #25
