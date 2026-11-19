## Goal

Compute common analyst operations deterministically before numbers enter prose, charts or Office files.

## Context

The client currently performs calculations itself. Competitors already expose growth/normalization utilities, so correctness and evidence linkage must accompany equivalent convenience.

## Proposal

Support a small typed operation set: absolute difference, percentage change, percentage-point difference, m/m and y/y period matching, explicitly selected moving averages, min/max with ties, and rebasing to an explicitly shared period. Validate unit/frequency/adjustment compatibility. Read the complete selected bounded snapshot, never just a displayed first page. Return formula, exact input observation IDs, output unit, rounding and valid/missing counts in an artifact-ready result.

## Acceptance criteria

- [ ] Hand-calculated fixtures verify every operation, leap/year/quarter boundaries, missing periods, zero denominators and all tied extrema.
- [ ] Y/y uses the corresponding prior-year period, not the twelfth available row; duplicate periods require explicit dimension selection.
- [ ] No sums of rates/indexes, silent resampling, extrapolation, imputation or combining incompatible adjusted/base-year series.
- [ ] Partial scans/truncated selections cannot claim population-wide extrema or averages; fail or label the exact selected coverage.
- [ ] Rebasing several series uses a declared common period and refuses a missing/zero base; original official values remain available.
- [ ] Each result can be independently recomputed from its bundle and operation specification; tool docs distinguish derived from published values.

## TÜİK sources involved

Eligible normalized public TÜİK observations from #87.

## Planning

- **Phase:** 1 — foundations
- **Priority:** high; **relative size:** L (not a delivery-date estimate).
- **Release scope:** first institutional-output slice.

## Dependencies

- **Blocked by:** #87, #88
- **Related:** #52, #72
- **Epic:** #84
- **Roadmap:** #25
