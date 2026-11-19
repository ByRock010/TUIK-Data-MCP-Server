## Goal

Deliver a polished, searchable report that can be shared and printed without a presentation editor.

## Context

PDF reading is already supported; PDF authoring is a separate output capability. A document that opens successfully can still have wrong pagination, unreadable notes or missing glyphs.

## Proposal

Implement an optional PDF renderer for ReportSpec with executive summary, figures, tables, methods, limitations and linked sources. Compare a small ReportLab and HTML/CSS renderer prototype against the actual layout needs, then record one dependency choice. Embed usable Turkish fonts; preserve vector charts where supported. Page-break rules must repeat table headers and keep headings/captions with content. Asset loading is restricted to the validated artifact bundle.

## Acceptance criteria

- [ ] A 3–5 page brief is searchable and includes visible date cutoff, units, source citations and distinguishing notes for computed values.
- [ ] Read-back/extracted text and chart/table data agree with ReportSpec; links target actual official sources.
- [ ] Render every page to images and verify no overflow, clipped tables, orphan headings, blank surprise pages or illegible footnotes.
- [ ] Stress cases cover long Turkish titles, multi-page tables, missing observations and unavailable source assets.
- [ ] No remote URL/file-system fetches from source text or templates; time/memory/output budgets are enforced.
- [ ] Record accessibility support and limitations without claiming PDF/UA conformance unless validated; export remains optional and client-retrievable.

## TÜİK sources involved

Validated ReportSpec; [WeasyPrint](https://doc.courtbouillon.org/weasyprint/stable/) and [ReportLab](https://docs.reportlab.com/) are candidate libraries.

## Planning

- **Phase:** 2 — institutional outputs
- **Priority:** high; **relative size:** M (not a delivery-date estimate).
- **Release scope:** first institutional-output slice.

## Dependencies

- **Blocked by:** #91
- **Epic:** #84
- **Roadmap:** #25
