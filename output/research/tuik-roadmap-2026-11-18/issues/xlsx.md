## Goal

Let analysts open and inspect the complete selected evidence in Excel without copy/pasting chat tables.

## Context

XLS/XLSX reading already exists; this issue adds output workbooks. Excel is also a practical handoff to Power Query and BI tools.

## Proposal

Export bounded XLSX with separate raw data, normalized data, analysis and Sources/Methods sheets. Preserve codes as text, dates/period semantics and source flags. Include typed numeric cells, usable tables/filters, frozen headers and supported native charts. Raw strings remain available for audit. If formulas are emitted, use a small allowlisted generator with defined cached-value/recalculation behavior; otherwise materialize the verified calculations and label them clearly.

## Acceptance criteria

- [ ] Workbook totals, typed values and chart data match the pinned inputs and selected result, including selections spanning multiple query pages.
- [ ] Turkish decimals, leading-zero geography codes, missing/confidential markers and percent scales survive opening in Excel/LibreOffice.
- [ ] Source strings beginning with =, +, -, @ or dangerous control characters remain literal text where appropriate, without corrupting legitimate numeric cells.
- [ ] Visible source/period/unit/adjustment notes and full manifest links identify derived values and extraction uncertainty.
- [ ] Round-trip inspection and a rendered workbook check detect clipped labels, broken charts and unintended external links/macros.
- [ ] Export goes through #88 with quotas and a client-retrievable file; openpyxl remains shared with existing input support.

## TÜİK sources involved

Existing public CSV/XLS/XLSX snapshots; [openpyxl chart documentation](https://openpyxl.readthedocs.io/en/stable/charts/introduction.html).

## Planning

- **Phase:** 2 — institutional outputs
- **Priority:** high; **relative size:** M (not a delivery-date estimate).
- **Release scope:** first institutional-output slice.

## Dependencies

- **Blocked by:** #87, #88
- **Epic:** #84
- **Roadmap:** #25
