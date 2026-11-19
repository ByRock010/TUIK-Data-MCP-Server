## Goal

Provide an interpretable regional view with traceable values and explicit missing coverage.

## Context

This issue implements only the geography/indicator combinations approved by the geography ADR. Generic labels or unverified shape files are insufficient.

## Proposal

Add a bounded regional comparison and static choropleth output backed by ChartSpec and artifact manifests. Join by validated official codes and boundary vintage. Use explicit color bins, legend, units, observation period, missing-value class and source attribution. Provide exact-value table and alt summary alongside the map. Optional MCP App support can reuse this output later.

## Acceptance criteria

- [ ] Chosen geography-level joins are complete or visibly report unmatched areas; national totals never appear as extra provinces.
- [ ] Missing/suppressed values use a separate legend class rather than zero; absolute counts and per-capita/rates are labeled distinctly.
- [ ] Only source-published or validated derived regional values are mapped; no interpolation to unavailable geography.
- [ ] PNG/SVG output is visually inspected for readable Turkish labels, legend, bin boundaries, accessible palette and source note.
- [ ] Underlying table, official code crosswalk, boundary version and bundle are retrievable.
- [ ] Geometry/input/output budgets and unsupported indicators/levels produce clear errors.

## TÜİK sources involved

Boundaries and eligible public indicators approved in #106.

## Planning

- **Phase:** 4 — visual expansion
- **Priority:** medium; **relative size:** L (not a delivery-date estimate).
- **Release scope:** expansion; does not gate the first institutional-output slice.
- **Implementation gate:** Proceed only if #106 establishes permitted boundary reuse, correct code mappings and adequate indicator coverage.

## Dependencies

- **Blocked by:** #106, #90
- **Epic:** #84
- **Roadmap:** #25
