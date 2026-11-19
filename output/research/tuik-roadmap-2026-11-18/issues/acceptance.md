## Goal

Close the first institutional-output milestone only with measured task success and usable files.

## Context

Passing parser tests and opening a PPTX do not establish an end-to-end professional workflow. The existing benchmark is useful prior evidence but cannot certify capabilities introduced by this roadmap.

## Proposal

Apply the frozen #85 rubric to the first release slice: audited observations, deterministic calculations, chart/claim validation, XLSX/PPTX/PDF, sector recipes and installation. Repeat each supported task at least three times in two named/versioned clients with fixed model/settings; cap cost and upstream traffic and distinguish mocked/offline from live runs. Use recorded artifacts for deterministic validation and a small paced live acceptance subset. Keep raw tool traces and render previews, with secrets redacted.

## Acceptance criteria

- [ ] Publish per-task outcomes, denominators, client/model versions, source/index versions, median and tail time, calls/token or cost usage, and upstream failures separately.
- [ ] Zero unresolved critical numeric, period, unit, citation or confidential-marker errors; >=90% supported-task success under the predeclared rubric.
- [ ] Unsupported tasks return the expected limitation; no fabricated report fills missing data.
- [ ] All candidate XLSX/PPTX/PDF files pass read-back and complete visual review; any additionally shipped DOCX/UI features receive their own checks.
- [ ] Compare with the original baseline and disclose regressions, excluded tasks and any missing independent user review.
- [ ] Clean-install instructions and optional dependency fallbacks pass; no unsupported claim of universal client compatibility or market supremacy.
- [ ] Publish candidate release notes/checklist and a go/no-go conclusion; tagging/promotion follow CONTRIBUTING.md after review.

## TÜİK sources involved

Frozen official-source fixtures and small paced live samples defined by #85.

## Planning

- **Phase:** 3 — vertical product
- **Priority:** high; **relative size:** L (not a delivery-date estimate).
- **Release scope:** first institutional-output slice.

## Dependencies

- **Blocked by:** #85, #97, #98
- **Related:** #78, #79, #80, #95
- **Epic:** #84
- **Roadmap:** #25
