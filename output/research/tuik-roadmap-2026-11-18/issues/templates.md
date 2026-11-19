## Goal

Demonstrate complete professional workflows with consistent branding and domain-specific statistical checks.

## Context

A generic exporter is useful but still leaves the user to design a report and choose safe comparisons. Sector recipes make the new capabilities concrete and reviewable.

## Proposal

Ship three evidence-based packs: finance/macro (prices and labour), manufacturing/export (industry and trade), and local policy/research (population and available annual regional indicators). Each pack defines supported indicators, geography/frequency, expected source/method notes, default outputs and refusal cases. Add a small validated theme schema for logo, colors, typography and page/slide headers, using the existing ChartSpec/ReportSpec. Treat unsupported sector needs as a measured gap rather than filling them with unrelated proxies.

## Acceptance criteria

- [ ] Each pack has one reproducible end-to-end example producing XLSX, PPTX and PDF from a shared pinned bundle.
- [ ] User can select Turkish/English labels and one alternate institutional theme; fonts/logos are bounded validated assets.
- [ ] Finance examples distinguish rates, index levels and percentage points; industry examples distinguish adjusted/original and index base years.
- [ ] Local examples keep province/NUTS and annual/monthly coverage explicit and do not invent provincial monthly unemployment.
- [ ] Actual or recruited representative users review examples under the #85 rubric; record who reviewed what, outcomes and unresolved feedback.
- [ ] Renders are inspected and all source/calculation claims validated; no arbitrary code or uncontrolled external template assets.

## TÜİK sources involved

Audited source families and current bulletin/methodology documentation for the three packs.

## Planning

- **Phase:** 3 — vertical product
- **Priority:** medium; **relative size:** L (not a delivery-date estimate).
- **Release scope:** first institutional-output slice.

## Dependencies

- **Blocked by:** #96, #92, #93, #94
- **Related:** #95
- **Epic:** #84
- **Roadmap:** #25
