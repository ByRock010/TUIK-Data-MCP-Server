# v0.3.0 release readiness

Prepared 2026-11-03 (Europe/Istanbul), tracking #29 and epic #24. The package
remains 0.2.0 until the release gates below are satisfied. This checklist is not
a release announcement or evidence of measured search quality.

## Required evidence

| Gate | State at preparation | Required before release |
| --- | --- | --- |
| Indexer #16 | Merged | Build the full metadata index with a successful JSON report |
| General MCP search #18 | Merged | Verify indexed filters, provenance, and fallback behavior |
| Cluster CI #19 | Open PR #64, green | Merge through ordinary review and retain the successful cluster run |
| Human baseline #11 | Retrieval pool prepared; human grades pending | Two independent judges, adjudication, κ and baseline metrics |
| Relevance comparison #20 | Pending #11 and full-corpus variant judging | Pool every system, judge new candidates, report overall/category/corpus metrics and choose defaults |
| Indexed live smoke #29 | Script and bounded acceptance path prepared | Retain a dated real stdio → index → resource → CSV JSON report for the final candidate |

The [search design](SEARCH_DESIGN.md), [backend contract](SEARCH_BACKEND.md),
[indexer guide](INDEXING.md) and [evaluation protocol](SEARCH_EVALUATION.md)
define the behavior to verify. OpenSearch is optional; unconfigured/unavailable
clusters use literal dataset search or portal discovery, with explicit warnings.
An indexed acceptance run must reject this fallback, not report it as success.

The [2026-11-04 single-AI pilot](verification/issue-11-20-ai-pilot-2026-11-04.md)
provides preliminary metrics on the 342-flow subset. It has zero independent
human judge pairs and does not satisfy #11/#20. Use the blank CSV forms to hand
off the human study; the full-corpus experiment remains necessary.

## Candidate verification

1. Integrate the prerequisite PRs through their reviews and CI. Keep each issue
   open while its acceptance criteria are outstanding. Do not infer relevance
   scores or a winning analyzer from token tests or returned-hit counts.
2. Freeze the evaluation corpus, complete the two-judge study and record all five
   systems in `SEARCH_EVALUATION.md`, including unjudged share, corpus boundaries,
   adjudication and κ. Re-run any configuration change before calling it the default.
   Use `evaluate_search.py score --require-complete` in the acceptance check;
   an `ai_preliminary` report never satisfies this gate.
3. Run the documented lint, format, offline, Node and real-cluster checks from
   `CONTRIBUTING.md`. Confirm the dedicated OpenSearch CI job is green for the
   candidate, alongside the required branch, Python and Docker jobs.
4. Build/refresh the full metadata index with `tuik-mcp-index`; retain the JSON
   report and index timestamp. Keep default request pacing and download limits.
   The merged content-index work (#22) upgraded the mapping to schema version 2;
   run a full rebuild before incremental updates of an older schema. Content
   indexing is opt-in; the v0.3 relevance experiment still needs the full metadata corpus.
5. Export `TUIK_OPENSEARCH_URL` and `TUIK_OPENSEARCH_INDEX`, then run:

   ```bash
   .venv/bin/python scripts/live_smoke.py \
     --output /tmp/v0.3.0-full-mcp.json
   .venv/bin/python scripts/live_smoke.py --indexed-only \
     --index-query 'altin haric dis ticaret hadleri' \
     --output /tmp/v0.3.0-indexed-mcp.json
   ```

   The full smoke covers live portal tables/publications explicitly and checks
   indexed discovery separately. Retain the JSONs under `docs/verification/` and
   link them from a dated `VERIFICATION.md` section, stating the source revision,
   corpus, versions, pacing and limitations. A small recorded-index pilot does
   not replace the full-crawl acceptance or the relevance study.

## Preparation and publication

Follow the enforced topic → `develop` → `master` policy in `CONTRIBUTING.md`.
The older `release/0.3.0` suggestion in #29 is not an accepted topic prefix.

1. Once the evidence above is complete, branch `chore/prepare-0.3.0` from current
   `develop`. Set the package version to 0.3.0, move `[Unreleased]` entries under
   `[0.3.0] - YYYY-MM-DD` with the actual date, and add release notes and final
   verification links. Keep unfinished v0.4 work out of the release claim.
2. Open the preparation PR into `develop`; use standalone `Closes #29` and
   `Closes #24` only when all acceptance criteria and epic sub-issues are complete.
   Use `Refs` for partial preparation. Merge after required CI and one review.
3. Open `develop` → `master`; merge with a merge commit after review and CI.
4. Tag the accepted `master` merge commit with annotated `v0.3.0`, push the tag,
   then publish release notes/assets from that exact tag and close the milestone.

No version bump, final changelog release entry, tag or milestone closure is
performed by this preparation increment.
