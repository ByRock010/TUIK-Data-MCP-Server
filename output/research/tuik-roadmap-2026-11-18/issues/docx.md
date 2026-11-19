## Goal

Provide editable institutional text for users who need to revise a brief before circulation.

## Context

PDF and PPTX solve sharing and presentation; policy, academic and consulting users also need editable narrative and tables. Keep this renderer independent so it does not delay the first PPTX/PDF slice.

## Proposal

Render ReportSpec as DOCX using real heading styles, editable paragraphs/tables, page headers/footers, captions and a source bibliography. Reuse theme tokens and chart assets. Define supported template placeholders instead of attempting arbitrary Word-template execution. Keep chart editability limitations explicit; provide underlying data through the same bundle.

## Acceptance criteria

- [ ] The exported document opens in a named Word-compatible editor with editable text and tables and preserved Turkish characters.
- [ ] Values, periods, methods and citations agree with the PDF/PPTX version of the identical ReportSpec.
- [ ] Headings/navigation, repeated table headers, page breaks and source links work; long text and multi-page tables are rendered and visually verified.
- [ ] Template content is constrained and source text is escaped; unsupported template features fail clearly.
- [ ] Optional dependencies and bundle retrieval behave like the other renderers.

## TÜİK sources involved

Validated ReportSpec and official citations retained in its bundle.

## Planning

- **Phase:** 2b — editable documents
- **Priority:** medium; **relative size:** M (not a delivery-date estimate).
- **Release scope:** expansion; does not gate the first institutional-output slice.

## Dependencies

- **Blocked by:** #91
- **Related:** #94, #93
- **Epic:** #84
- **Roadmap:** #25
