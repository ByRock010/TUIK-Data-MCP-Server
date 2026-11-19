## Goal

Choose the safest useful route to official dimensions and server-filtered observations without sacrificing the key-free workflow.

## Context

Competitors use two distinct surfaces: authenticated nsiws SDMX and key-free DataBrowser backend endpoints. Our current public exports already work; authenticated access should be optional and justified by measured coverage or efficiency.

## Proposal

Compare both routes on the six initial domains: catalogue identity/version, dimensions/codelists, observation attributes, server-side filters, output bounds, licensing/terms, freshness and failure behavior. Use documented official APIs where possible; identify internal portal endpoints explicitly. Reuse existing stable IDs and avoid a parallel inconsistent catalogue. Test no-key paths with bounded samples; authenticated live testing is contingent on a legitimately configured secret outside chat.

## Acceptance criteria

- [ ] ADR includes reproducible endpoints/fixtures and a coverage/call-size table; catalogue counts are dated observations, not universal completeness claims.
- [ ] Official SDMX documentation is cited for API-key/token requirements and lifecycle; no-key vs authenticated routes are accurately separated.
- [ ] Dimension/code mapping preserves adjustment, geography, units, confidential/provisional flags and dataflow version.
- [ ] Compare exact values for overlapping public samples and retain discrepancies rather than silently preferring one route.
- [ ] Define go/no-go and selected implementation scope for #105, including missing-key behavior and evidence still requiring credentials.
- [ ] No login credentials or SMS interaction are collected by a conversational tool.

## TÜİK sources involved

[Official SDMX access documentation](https://veriportali.tuik.gov.tr/en/sdmx-web-service-documentation); public DataBrowser and export catalogue.

## Planning

- **Phase:** 4 — coverage expansion
- **Priority:** medium; **relative size:** M (not a delivery-date estimate).
- **Release scope:** expansion; does not gate the first institutional-output slice.

## Dependencies

- **Blocked by:** none; can start independently.
- **Related:** #26, #51, #52, #87
- **Epic:** #84
- **Roadmap:** #25
