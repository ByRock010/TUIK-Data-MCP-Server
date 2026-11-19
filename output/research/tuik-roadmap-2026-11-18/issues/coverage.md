## Goal

Establish which real TÜİK table families are safe to use for automated analysis and reporting.

## Context

The current parser corpus is deliberately small. A competitor publishes a much broader istab probe, including medium-confidence and rejected tables. File reading alone does not establish that every period, unit and value label was interpreted correctly.

## Proposal

Create a bounded, stratified audit of at least 60 public files across six initial domains: prices, labour, population, foreign trade, housing sales and industry. Include wide/long, merged headers, multi-sheet, bilingual, revised and ambiguous layouts. Keep at least 20 files held out from rule changes. Retain source hashes and sheet/cell coordinates, independently sample value-to-header mappings, and classify exact, inferred, partial and unsupported extraction. This issue measures and prioritizes gaps; it does not introduce a universal tidy converter.

## Acceptance criteria

- [ ] Publish selection rules, file manifest, sample counts and source dates; pace downloads and reuse retained files.
- [ ] Trace at least five sampled numeric outputs per file to their source cells and header/period/unit context using an independent checking path.
- [ ] Report layout, labeling, unit, period, missing-marker and coverage errors separately, including held-out outcomes.
- [ ] Preserve evidence for ambiguous/rejected tables and rank concrete parser follow-ups by the sector tasks they block.
- [ ] Identify at least one verified source family in each initial domain, or state the domain is not yet eligible for normalized automated reports.
- [ ] Do not advertise whole-portal accuracy from this sample or treat inferred structure as official metadata.

## TÜİK sources involved

Public CSV/XLS/XLSX exports; [competitor probe](https://github.com/ahmthamza/turkiye-veri-mcp/blob/ebb2fa3b7d1363545c7eca1165eee037121fdb03/ISTAB_PROBE.md).

## Planning

- **Phase:** 0 — evidence
- **Priority:** high; **relative size:** L (not a delivery-date estimate).
- **Release scope:** first institutional-output slice.

## Dependencies

- **Blocked by:** none; can start independently.
- **Related:** #3, #4, #5, #6, #51, #52
- **Epic:** #84
- **Roadmap:** #25
