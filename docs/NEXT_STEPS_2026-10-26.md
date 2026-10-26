# Development plan after the advisor demo — 2026-10-26

Reviewed all 33 issues, the merged PR history through #48, CONTRIBUTING, the roadmap
(#25), both epics (#23/#24), the file-reading design and dated verification reports.
The advisor demo succeeded according to the project owner. This note records the
next implementation decisions; it does not supersede the issue acceptance criteria.

## State and immediately available work

- #6, #7 and #9 are merged into `develop`; PR #48 also promoted that code to `master`.
  The package is still 0.1.0 and #10 remains open: promotion alone did not complete
  the documented `develop → master`, version/changelog/tag process.
- #11 is the next research priority. Collect a real baseline and prepare pooled
  independent judging before implementing a new ranking system. Human grades and κ
  cannot be fabricated to close the issue.
- #12 is complete and the local OpenSearch 3.3.2 cluster is healthy. #13 can run now
  because it depends only on #12. Token-level analysis and an architecture ADR are
  useful while two judges complete #11; relevance decisions remain conditional.
- #14 follows #13 and opens #15/#17. #16 follows #14/#15; #18 and #19 follow #17.
  #20 then compares the judged variants before #29 and v0.3.0 release closure.
- #8, #21 and #22 remain stretch work. They do not take priority over the measured
  Turkish lexical search requested by the advisor.

## This iteration

1. **#11, `feature/11-search-evaluation`:** 48 balanced queries, paced real portal and
   literal rankings, stable evaluation identities, pooled candidates, independent
   judge sheets, validated label merge and nDCG/MRR/pooled-recall/κ reporting. Test
   calculations against hand-worked examples. Keep `Refs #11` until two human
   judges complete and adjudicate the pool and measured scores are recorded.
2. **#13, `feature/13-turkish-analysis`:** compare real `_analyze` output on at least
   30 difficult forms; package the reproducible analysis settings and search-time
   synonym rules; record field choices, limitations and OpenSearch ADR. Do not
   claim relevance superiority without #11/#20.

Each issue gets its own branch from current `develop`, Conventional Commits,
Unreleased changelog entry, tests and a PR into `develop`. CI and one teammate's
approval remain prerequisites for squash merge. No feature PR performs a release
or bypasses review. Unrelated local `docs/.DS_Store` stays outside commits.

## Next handoff

Two different people judge the prepared #11 sheets independently, record their
identity/dates, resolve disagreements with reasons and commit the results through
the #11 branch. Then publish the measured baseline and κ. In parallel, review #13's
token behavior and implement #14's schema/mapping, followed by crawler/backend work.
Schedule #10's versioned release as a separate release task after its refreshed
live evidence and normal review are ready.
