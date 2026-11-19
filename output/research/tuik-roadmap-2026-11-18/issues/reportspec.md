## Goal

Build one report model that PowerPoint, PDF and Word render without drifting numbers or citations.

## Context

Independent exporters tend to repeat analysis, lose footnotes or let prose introduce unsupported numbers. The server should own validated evidence and layout inputs; the host model may propose text.

## Proposal

Define a versioned ReportSpec with title, audience, language, source cutoff, findings, tables, charts, methods, limitations and citation registry. Numeric findings refer to published observation IDs or deterministic calculation IDs. Validate any supplied narrative using explicit claim bindings; unresolved values are rejected or visibly marked as unverified draft content. Provide deterministic evidence-backed summary templates and optional host-provided commentary. Escape source/host text as data; do not require model keys or server-side arbitrary code.

## Acceptance criteria

- [ ] The same report specification drives all renderers; schema validation covers section limits, asset references and unsupported layouts.
- [ ] Every factual numeric claim has a resolvable source/calculation, period and unit; tested examples reject changed signs, percentages, dates and unsupported comparisons.
- [ ] Latest release, latest observation, retrieval time and data cutoff remain distinct; missing release dates are unknown.
- [ ] Methodology/adjustment changes and incomplete source coverage propagate into visible limitations.
- [ ] Untrusted text cannot introduce executable HTML/templates, remote asset fetches or instructions.
- [ ] A sample report is reproducible without an external LLM; optional narrative is explicitly identified and must pass the same claim checks.

## TÜİK sources involved

Public TÜİK observations, chart specifications and relevant bulletin/methodology locators.

## Planning

- **Phase:** 2 — institutional outputs
- **Priority:** high; **relative size:** L (not a delivery-date estimate).
- **Release scope:** first institutional-output slice.

## Dependencies

- **Blocked by:** #89, #90
- **Epic:** #84
- **Roadmap:** #25
