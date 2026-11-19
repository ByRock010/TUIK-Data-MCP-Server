## Goal

Determine which regional comparisons and maps can be produced faithfully before shipping a map tool.

## Context

The geographic portal and a competitor already expose boundary/data surfaces. A map that joins a province label to the wrong regional code can look persuasive while being statistically wrong.

## Proposal

Audit a small cross-section of CIP indicators/boundaries and existing public regional tables. Distinguish province, district, NUTS-1/2/3 and changing boundary vintages. Record source/license/redistribution terms, code crosswalks, coordinate reference system and indicator geography availability. Check at least population, housing and one annual labour indicator; mark unavailable levels and years explicitly. Define a bounded pilot and fallback to table comparisons.

## Acceptance criteria

- [ ] ADR contains dated endpoint/fixture evidence, access status and terms/source attribution for any retained boundaries.
- [ ] Code-level joins are validated against official labels for a complete chosen geography level; ambiguous or unmatched codes are reported.
- [ ] Period and level coverage is documented for each pilot indicator, including unavailable monthly/provincial combinations.
- [ ] Boundary year, geometry validity, simplification and permissible payload size have a defined contract.
- [ ] Define go/no-go for #107 and whether data/boundaries can be served without another credential or hosting dependency.

## TÜİK sources involved

[Official CIP](https://cip.tuik.gov.tr/); official portal regional tables and classifications.

## Planning

- **Phase:** 4 — coverage expansion
- **Priority:** medium; **relative size:** M (not a delivery-date estimate).
- **Release scope:** expansion; does not gate the first institutional-output slice.

## Dependencies

- **Blocked by:** none; can start independently.
- **Related:** #87
- **Epic:** #84
- **Roadmap:** #25
