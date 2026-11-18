## Goal

Test cross-source value without turning the project into an unbounded replacement for every public-data MCP.

## Context

TCMB launched official EVDS-MCP on 26 October 2026. Other projects already aggregate TÜİK, TCMB, BDDK and HMB. The useful distinction is a correct combined business output, not duplicating provider access indiscriminately.

## Proposal

Prototype host-level composition or an explicit typed import boundary that combines one verified TÜİK series with one legitimate official EVDS series. Keep original provider IDs, retrieval times and licenses/attribution. Use matched periods and compatible concepts; validate external values and bound payloads. Choose one finance/industry task and compare utility/setup cost with the TÜİK-only report. Neither arbitrary network URLs nor third-party tool instructions become trusted data.

## Acceptance criteria

- [ ] An ADR compares host composition, optional adapter and no integration, with a demand/maintenance decision.
- [ ] A reproducible public/synthetic fixture demonstration traces every value to its provider; any authenticated live step is separately labeled and uses a legitimately configured credential.
- [ ] Frequency, currency, index-base and seasonal-adjustment mismatches are refused or require an explicit recorded transformation.
- [ ] No source priority is silently overwritten; conflicting values and different vintages remain inspectable.
- [ ] Exported evidence identifies each original publisher and any redistributor; supported reuse/attribution is checked before publication.
- [ ] Only create further provider implementation issues if this pilot demonstrates a concrete user benefit.

## TÜİK sources involved

[TCMB official launch announcement](https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB%2BTR/Main%2BMenu/Duyurular/Basin/2026/DUY2026-46), official EVDS/TÜİK documentation and source terms.

## Planning

- **Phase:** 4 — institutional integrations
- **Priority:** low; **relative size:** M (not a delivery-date estimate).
- **Release scope:** expansion; does not gate the first institutional-output slice.

## Dependencies

- **Blocked by:** #87
- **Related:** #108, #89, #91
- **Epic:** #84
- **Roadmap:** #25
