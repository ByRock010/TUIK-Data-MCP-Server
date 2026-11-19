## Goal

Let users inspect filters, compare series and export selections inside a compatible chat client.

## Context

MCP Apps is an official extension for embedded interactive interfaces. Its host support and SDK compatibility must be checked; a UI must not become a prerequisite for existing stdio tools.

## Proposal

Build a small self-contained table/chart explorer backed by existing typed selections and artifacts. Show unit/frequency/adjustment, source, coverage and selected filters; expose exact values and an accessible data table. UI changes call validated server tools through the host bridge. Use ui:// resources and the official extension contract after verifying compatibility with the pinned Python SDK or a thin isolated UI adapter. Preserve PNG/structured/text fallbacks.

## Acceptance criteria

- [ ] A named/versioned compatible host loads the resource and can filter an audited table, update the chart and retrieve a selected artifact.
- [ ] A second named client without UI support completes the equivalent workflow using tools/text.
- [ ] ChartSpec and selection IDs guarantee the UI view and exported artifact use the same data; no browser-only calculations change official values.
- [ ] Keyboard operation, focus, accessible labels, contrast, responsive layout and Turkish labels are checked.
- [ ] CSP, origin/bridge messaging and allowed tool calls are constrained; assets need no public CDN or arbitrary outbound domains.
- [ ] Document tested host capabilities and SDK choice; no broad SDK upgrade or existing protocol break is hidden in UI work.

## TÜİK sources involved

[Official MCP Apps overview](https://apps.extensions.modelcontextprotocol.io/api/); existing validated table/chart data.

## Planning

- **Phase:** 4 — interactive expansion
- **Priority:** medium; **relative size:** L (not a delivery-date estimate).
- **Release scope:** expansion; does not gate the first institutional-output slice.

## Dependencies

- **Blocked by:** #90, #96
- **Related:** #92
- **Epic:** #84
- **Roadmap:** #25
