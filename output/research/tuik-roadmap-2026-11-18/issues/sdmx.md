## Goal

Add verified official dimension-aware queries where the research establishes a clear coverage or efficiency benefit.

## Context

This is a gated adapter implementation, not a promise that all TÜİK data will become retrievable. Existing public CSV/Excel access remains a valid route.

## Proposal

Implement the route selected in #104. Provide inspectable dimensions/code labels and typed bounded filters whose resolved codes are visible. Reuse stable identity/provenance and normalized observation contracts. If authenticated SDMX is selected, isolate credentials in environment/secret configuration, cache tokens in memory with refresh/retry, and expose a clear not-configured outcome. Internal DataBrowser use must retain explicit upstream-change risk.

## Acceptance criteria

- [ ] Recorded structure/data fixtures cover code order, multilingual labels, constant dimensions, empty results and invalid codes.
- [ ] Filters demonstrably reduce upstream selection where supported; returned row/byte caps and completeness are explicit.
- [ ] Normalized values/attributes agree with overlapping verified CSV/Excel samples and retain source/version/coverage distinctions.
- [ ] Auth, timeout, 401/403, 404-vs-empty and rate limiting have differentiated actionable outcomes without leaking secrets.
- [ ] Existing tools/IDs retain documented behavior; any additive tool/schema choice is recorded and tested over MCP.
- [ ] A small paced live check verifies the selected route; auth-only checks remain explicitly unverified until legitimate credentials are available.

## TÜİK sources involved

The official endpoints and access profile chosen in #104.

## Planning

- **Phase:** 4 — coverage expansion
- **Priority:** medium; **relative size:** L (not a delivery-date estimate).
- **Release scope:** expansion; does not gate the first institutional-output slice.
- **Implementation gate:** Proceed only if #104 demonstrates useful additional coverage/efficiency and approves a supported access profile.

## Dependencies

- **Blocked by:** #104, #87
- **Epic:** #84
- **Roadmap:** #25
