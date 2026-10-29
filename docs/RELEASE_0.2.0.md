# v0.2.0 release readiness

Scope audited on **2026-10-29** against the open issues, milestone 1 and
`develop` at `bbd717f`. The first milestone is **v0.2.0 – Excel/PDF reading**;
GitHub has no due date configured. This document prepares a reviewable release;
it does not record an already published tag.

## Milestone scope

| Work | Issues | State at the audit |
| --- | --- | --- |
| Throttle recognition and paced downloads | #2 | Merged, closed |
| Parser contracts and recorded official file corpus | #3, #4 | Merged, closed |
| XLS/XLSX parsing and bounded row retrieval | #5, #6 | Merged, closed |
| PDF text, physical pages and continuation | #7 | Merged, closed |
| Parser limits and content-aware caches | #9 | Merged, closed |
| Explicit bulletin dataflow choices and stable file identities | #26, #27 | Merged, closed |
| Weekly source-contract checks | #28 | Merged, closed |
| XLSX recognition, source-error semantics, fixture resume, single smoke run | #38–#41 | Merged, closed |
| Version, documentation, release evidence and publication | #10 | This preparation; promotion/tag still pending |
| File-reading tracking epic | #23 | Close after #10 and the release are complete |

The already merged #51 table inspection is included in this release. Existing
OpenSearch infrastructure and the dataset backend (#12–#17), plus evaluation
preparation from #11, are also included because they are already in `develop`.
Their presence does not claim that the v0.3.0 search milestone is finished.

## Remaining open work outside this milestone

| Issues | Why they do not block v0.2.0 |
| --- | --- |
| #11 | Independent human judgments and measured baseline quality belong to v0.3.0; do not fabricate grades or κ. |
| #18, #19, #20, #29; epic #24 | General indexed MCP search, cluster CI, relevance evaluation and v0.3.0 release closure. |
| #8, #21, #22 | v0.4.0 stretch goals: PDF tables, semantic retrieval and content indexing. |
| #52 | Unassigned advanced table-query/cursor follow-up; its issue explicitly does not block v0.2.0/v0.3.0. |
| #25 | Ongoing roadmap tracker across multiple milestones. |

## Acceptance evidence

The release smoke must exercise actual MCP stdio discovery and all eight tools:

1. A bulletin offers `related_datasets`; explicitly select its returned `flow:` ID
   and verify CSV rows. Record the bulletin and selected dataset together.
2. A bulletin detail response supplies a `press:<id>:file:<key>` Excel ID; select
   that exact attachment and verify bounded rows, physical sheet and snapshot.
3. A publication ID returns PDF text with physical page/source provenance;
   methodology text continues on the same page using both continuation fields.
4. CSV and Excel inspection followed by an exact-filter query preserve content
   SHA-256, source URL and retrieval timestamp.

Commands, using a fresh editable installation of this branch:

```bash
python -m pip install -e '.[dev]'
ruff check src tests scripts
ruff format --check src tests scripts
pytest -q
node --test .github/scripts/*.test.cjs
TUIK_MIN_INTERVAL_SECONDS=2 python scripts/live_smoke.py --output /tmp/tuik-v020-live.json
pytest -m live -k 'not test_live_mcp_workflow' -q --junitxml=/tmp/tuik-v020-live.xml
```

Run the smoke and remaining live tests sequentially; do not raise upstream request
rates. The stdio child inherits `TUIK_*` settings, and the JSON records pacing
without dumping environment values. The dated results and JSON are linked from
[`VERIFICATION.md`](VERIFICATION.md). Real OpenSearch integration is optional for
this release and needs a running cluster; offline checks and the base MCP server
remain usable without it. Known source failures remain explicit errors.

## Reviewed release sequence

- [ ] Review and merge `chore/10-prepare-0.2.0` into `develop` after required CI and
  a teammate's approval. Refresh the planned changelog date if publication moves.
- [ ] Open **`develop` → `master`**, with the exact prepared version and release
  evidence. Require normal CI and review; use a **merge commit**, as enforced by
  the branch policy. The obsolete `release/0.2.0` → `master` route is not allowed.
- [ ] After that promotion is merged, verify the promoted `master` commit contains
  package version 0.2.0, the 0.2.0 changelog section and the accepted evidence.
  Create/push annotated tag `v0.2.0` at that commit, following CONTRIBUTING.
- [ ] Confirm tag target and release contents, close #10 and epic #23, then close
  milestone 1. Do not close #25 or other milestones as part of this release.

The preparation PR uses **Refs #10** and **Refs #23**. It does not close the
issues before their publication criteria are met or bypass protected-branch review.
