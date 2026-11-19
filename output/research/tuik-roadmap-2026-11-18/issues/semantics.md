## Goal

Give calculations and exporters an unambiguous statistical input while preserving source values and legacy table tools.

## Context

Profiles currently infer types and table queries preserve strings. They do not declare a universal time-series schema, official dimensions, revision vintage or comparable statistical concepts. The chart tool already handles explicit columns and formats; retain that lower-level path.

## Proposal

Add a versioned, opt-in normalized observation contract and reviewed mapping recipes for eligible audited families. Carry original value, validated numeric value, source row/cell locator, period and frequency, geography code/level, unit/multiplier/currency, measure type (level/index/rate), base year, seasonal/calendar adjustment, dimensions, status/notes and exact snapshot. Mark source-declared, recipe-defined, user-selected and unknown fields separately. Preserve constant dimensions as metadata. Never infer that a series is provincial or seasonally adjusted solely from a column name.

## Acceptance criteria

- [ ] Define and validate the schema, mapping version and unsupported/ambiguous results; existing tools and raw strings remain compatible.
- [ ] Mappings cover verified examples from the six initial domains; failed audit domains remain explicitly unavailable.
- [ ] CSV observations retain original row identity; XLS/XLSX observations retain sheet/cell and header context, including merged-header expansion lineage.
- [ ] Tests distinguish percent from percentage points, persons from thousands, month from year, national from NUTS/province, and original from adjusted series.
- [ ] Unknown units, missing markers, duplicates and incomplete coverage cannot become valid numeric inputs silently.
- [ ] Provide a bounded discovery/inspection path for mappings and examples; do not create one tool per indicator.

## TÜİK sources involved

Existing public exports and classification metadata; the eligible source families from #86.

## Planning

- **Phase:** 1 — foundations
- **Priority:** high; **relative size:** L (not a delivery-date estimate).
- **Release scope:** first institutional-output slice.

## Dependencies

- **Blocked by:** #86
- **Related:** #51, #52, #72
- **Epic:** #84
- **Roadmap:** #25
