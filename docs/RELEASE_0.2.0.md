# v0.2.0 release checklist

Milestone **1: v0.2.0 – Excel/PDF reading** has no configured due date. The final
candidate is `develop` at `8e566bf0f3fcd8c4bcd0fa16901c71a4b4cbbbe6` (2026-10-29),
including accepted PRs #59 and #60. Package version is **0.2.0**. The final
preparation updates documentation/evidence only; tagging and milestone closure
remain pending until the protected promotion is reviewed and merged.

## Scope

| Work | Issues | Accepted state |
| --- | --- | --- |
| Paced downloads, parser contracts, official corpus, XLS/XLSX/PDF reading and bounded caches | #2–#7, #9 | Merged, closed |
| Bulletin dataset choices, stable file identities and source monitoring | #26–#28, #38–#41 | Merged, closed |
| Table inspection and typed queries with snapshot-bound cursors | #51, #52 | Merged, closed |
| Version, acceptance preparation and file-reading epic | #10, #23 | Closed by PR #59 on `develop` |
| Optional OpenSearch foundation, metadata indexer and dataset backend | #12–#17 | Already merged; included without claiming v0.3.0 completion |
| Search evaluation preparation and reviewed automation | #11, #49, #55 | Included; human relevance judgments remain pending |

#52 was not a milestone prerequisite, but merged before the first v0.2.0 tag.
Its implementation, compatibility notes and verification therefore belong in
0.2.0 rather than `Unreleased`. The milestone had **0 open / 17 closed issues**
at the final scope audit, before adding publication PRs.

## Remaining work outside this milestone

| Issues | Remaining scope |
| --- | --- |
| #11 | Independent judgments and measured baseline quality; do not fabricate grades or κ. |
| #18, #19, #20, #29; epic #24 | Indexed general MCP search, cluster CI, relevance evaluation and v0.3.0 closure. |
| #8, #21, #22 | v0.4.0 stretch goals: PDF tables, semantic retrieval and content indexing. |
| #25 | Ongoing roadmap tracker across milestones. |

## Candidate acceptance

Run from a clean editable installation of the candidate:

```bash
ruff check src tests scripts
ruff format --check src tests scripts
pytest -q
node --test .github/scripts/*.test.cjs
TUIK_MIN_INTERVAL_SECONDS=2 python scripts/live_smoke.py --output /tmp/v020-mcp.json
TUIK_MIN_INTERVAL_SECONDS=2 python scripts/live_query_smoke.py --output /tmp/v020-query.json
pytest -m live -k 'not test_live_mcp_workflow and not test_live_snapshot_bound_queries' -q --junitxml=/tmp/v020-regression.xml
uv build --out-dir /tmp/v020-dist
```

Run the three live commands **sequentially** with existing pacing, so neither
full workflow is repeated by pytest. Verify real MCP discovery of all eight tools,
explicit bulletin → CSV and bulletin → Excel choices, PDF same-page continuation,
inspection/query provenance, and two cursor pages equal to a combined query.
Install the wheel into a fresh environment outside the repository; verify package
version, both console entry points, MCP schemas and packaged search assets.

Results and dated limitations are recorded in the
[final candidate report](verification/v0.2.0-final-2026-10-29.md) and
[verification index](VERIFICATION.md). Real OpenSearch integration needs a running
cluster and remains optional for this release. Source outages are explicit errors.

## Publication gates

- [x] Accept implementations: PR #59 (`d8c358f`) closed #10/#23; PR #60
  (`8e566bf`) closed #52. Issue closure records accepted work, not a published tag.
- [ ] Squash merge `chore/finalize-0.2.0` into `develop` after required CI and a
  teammate's review; include the final candidate evidence and expanded changelog.
- [ ] Review `develop` → `master` with that preparation included. A draft promotion
  may be opened beforehand, but final approval must cover the updated head.
  Use a **merge commit**, preserve `develop`, and keep branch protection enabled.
- [ ] Verify the promoted `master` tree contains version 0.2.0, this changelog and
  acceptance evidence. Create/push **annotated tag `v0.2.0` at that merge commit**;
  verify the dereferenced remote tag target.
- [ ] Build/verify the tagged source. Publish the GitHub release using
  [release notes](releases/v0.2.0.md), wheel, source distribution and `SHA256SUMS`.
  This does not publish to PyPI.
- [ ] Confirm milestone 1 has no open issues/PRs and close **only milestone 1**.
  Keep roadmap #25 and later milestones open.

Both protected branches require a teammate's approval and passing CI. Author
review cannot replace that approval, and there are no configured bypass actors.
If runtime changes enter `develop` before promotion, re-audit scope and repeat
relevant acceptance before approving the new head. A later publication date must
also be reflected in the changelog. No `master` → `develop` PR is needed under
[the repository release policy](../CONTRIBUTING.md#releasing).
