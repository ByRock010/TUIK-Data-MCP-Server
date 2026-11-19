## Goal

Turn an evidence-backed report into a presentation that a professional can edit and present.

## Context

The current product has PNG charts but no presentation output. Editable objects and source-preserving layout create more value than placing screenshots on slides.

## Proposal

Add an optional PPTX renderer for ReportSpec. Ship a small 16:9 briefing template: title/key messages, KPI table, trend, comparison, methods/limitations and sources. Use native editable text, tables and PowerPoint charts with embedded data for supported chart types. Use a declared image fallback for unsupported visuals, preserving underlying data in the bundle. Renderers do not recalculate or invent prose.

## Acceptance criteria

- [ ] Generate a six-slide Turkish briefing containing real validated values and source references; support an English label example.
- [ ] At least line and bar charts are native editable charts with embedded data; all ordinary text/tables remain editable.
- [ ] Every analytical slide has a legible compact source/period/unit note; speaker notes carry fuller citations and calculation/method context.
- [ ] Read-back verifies numeric cells/chart series against ReportSpec and checks missing asset references.
- [ ] Render all slides with a documented tool and visually inspect Turkish glyphs, long text, legends, clipping, overlap and readable minimum font size.
- [ ] Confirm opening/editing in PowerPoint or a named compatible editor; record editor/version and known fidelity differences.
- [ ] Dependency is optional and failures do not break the current nine tools; deliver through #88.

## TÜİK sources involved

Validated ReportSpec; [python-pptx native charts](https://python-pptx.readthedocs.io/en/latest/user/charts.html). Library choice is confirmed in implementation.

## Planning

- **Phase:** 2 — institutional outputs
- **Priority:** high; **relative size:** L (not a delivery-date estimate).
- **Release scope:** first institutional-output slice.

## Dependencies

- **Blocked by:** #91
- **Epic:** #84
- **Roadmap:** #25
