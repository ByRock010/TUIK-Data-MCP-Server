# v0.3.0 release readiness

Prepared 2026-11-03 and updated 2026-11-05 (Europe/Istanbul), tracking #29 and
epic #24. The package remains 0.2.0 until the release gates below are satisfied.
This checklist is not a release announcement or evidence of measured search quality.

## Required evidence

| Gate | State (2026-11-05) | Required before release |
| --- | --- | --- |
| Indexer #16 | Merged; a 3,424-document frozen-snapshot rebuild and full MCP smoke passed in #74 | Retain the full rebuild report for the final release candidate; refresh the served index as appropriate |
| General MCP search #18 | Merged | Verify indexed filters, provenance, and fallback behavior |
| Cluster CI #19 | Merged (#64); the `OpenSearch integration` job runs on every PR | Add `OpenSearch integration` to the required checks for `develop` and `master` (repository owner), and retain the successful cluster run for the candidate |
| AI baseline #11 | Two-pass source review recorded; method in ADR 0004 | Retain complete AI-reviewed baseline metrics, source references and declared limitations |
| Relevance comparison #20 | Full-metadata comparison, complete 1,390-pair AI review and default decision recorded in #74 | Accept the PR through review/CI and retain the frozen corpus, labels, source evidence and metrics |
| Indexed live smoke #29 | Merged (#68); full 3,424-document candidate smoke recorded in #74 | Retain the dated full-index report and rerun for the final versioned release candidate if needed |

The [search design](SEARCH_DESIGN.md), [backend contract](SEARCH_BACKEND.md),
[indexer guide](INDEXING.md) and [evaluation protocol](SEARCH_EVALUATION.md)
define the behavior to verify. OpenSearch is optional; unconfigured/unavailable
clusters use literal dataset search or portal discovery, with explicit warnings.
An indexed acceptance run must reject this fallback, not report it as success.

The owner-adopted [AI evaluation protocol](adr/0004-ai-relevance-evaluation.md)
supersedes the original two-human-judge requirement for project acceptance.
The [source-checked baseline](verification/issue-11-ai-source-review-2026-11-04.md)
has complete AI grades and no human judge pairs; κ is not applicable. The
[full-metadata follow-up](verification/issue-20-full-corpus-2026-11-05.md) now covers
3,424 documents, retains the synonym default for the best matched-corpus graded
ranking/pooled recall, and records the built-in analyzer's MRR advantage. Its
[full MCP smoke](verification/search-full-live-smoke-2026-11-05.json) passed on
package 0.2.0 using an isolated full index; that index was then removed. These
are candidate evidence, not a version bump, production deployment or release tag.

## Candidate verification

1. Integrate the prerequisite PRs through their reviews and CI. Keep each issue
   open while its acceptance criteria are outstanding. Do not infer relevance
   scores or a winning analyzer from token tests or returned-hit counts.
2. Retain/reproduce the recorded full evaluation and adopted AI source review,
   with all five systems in `SEARCH_EVALUATION.md`, including unjudged share,
   corpus boundaries, revision reasons and the single-assessor limitations.
   Re-run configuration changes before choosing defaults. Use
   `evaluate_search.py score-ai --require-reviewed` with the full-corpus assessment,
   review and evidence files. The original two-human study remains optional and
   uses `score --require-complete`; never present AI output as human validation.
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
