# v0.3.0 release readiness

Updated 2026-11-05 (Europe/Istanbul), tracking #29 and epic #24. The package and
changelog are prepared as **0.3.0**. This records candidate acceptance; protected
promotion, tagging, publication and milestone closure remain separate steps.

## Required evidence

| Gate | Candidate state | Evidence |
| --- | --- | --- |
| Indexer #16 | Merged | Full 3,424-document frozen-snapshot rebuild through the canonical indexer |
| General MCP search #18 | Merged | Indexed filters/provenance and both explicit fallback routes in real-cluster tests |
| Cluster CI #19 | Merged (#64) | `OpenSearch integration` runs on every PR; retain the successful preparation/promotion runs |
| AI baseline #11 | Merged (#71) | Disclosed two-pass source review under ADR 0004 |
| Relevance comparison #20 | Merged with approval and green CI (#74) | 3,424 documents, 1,390 reviewed pairs, retained sources/metrics and synonym-default decision |
| Indexed live smoke #29 | Implemented (#68), refreshed for package 0.3.0 | Final index/MCP evidence linked from `VERIFICATION.md` |
| Release preparation #29/#24 | Version, changelog, notes and acceptance prepared | Topic PR into `develop`; review and CI remain required |
| Publication | Pending | Reviewed `develop` → `master`, annotated tag, tagged-source artifacts and release |

The [search design](SEARCH_DESIGN.md), [backend contract](SEARCH_BACKEND.md),
[indexer guide](INDEXING.md) and [evaluation protocol](SEARCH_EVALUATION.md)
define the behavior. OpenSearch is optional; unconfigured/unavailable clusters
use literal dataset search or portal discovery, with explicit warnings. Indexed
acceptance requires actual index results and rejects fallback.

The owner-adopted [AI evaluation protocol](adr/0004-ai-relevance-evaluation.md)
supersedes the original two-human-judge requirement for project acceptance.
The [full comparison](verification/issue-20-full-corpus-2026-11-05.md) uses 3,424
metadata documents and 1,390 source-reviewed AI grades. The selected synonym
variant has the best same-corpus nDCG@10/pooled recall; built-in Turkish has
better MRR. There are zero human pairs; κ is not applicable. The standalone
AI report's `release_gate_satisfied:false` does not certify publication.

All epic implementation sub-issues (#11–#20) are closed; #29 is the final
preparation item. As with v0.2.0, closing #29/#24 on the preparation merge records
accepted development, not an already published tag. Close the milestone only
after publication. Adding `OpenSearch integration` to required branch checks
remains a repository-owner publication gate, as in the earlier checklist; this
PR does not change repository rulesets.

## Candidate verification

See [VERIFICATION.md](VERIFICATION.md) and the
[final candidate report](verification/v0.3.0-final-2026-11-05.md) for exact source
revision, commands, package/Python versions, frozen corpus and limits.

1. Reproduce `evaluate_search.py score-ai --require-reviewed` from the full
   comparison report. Its output must match the retained reviewed metric JSON.
   Do not infer relevance from token tests or hit counts. Independent human
   validation remains optional and must not be claimed from AI labels.
2. Run Ruff lint/format, offline pytest, Node policy/automation and all real
   OpenSearch tests as described in `CONTRIBUTING.md`. Retain green GitHub
   Python 3.12/3.13, Docker, branch-policy and OpenSearch results for the final PR.
3. Build wheel/sdist and verify the installed wheel outside the repository:
   version 0.3.0, both console entry points, nine MCP tools and packaged mapping,
   analyzer and synonym assets.
4. Rebuild the frozen full metadata corpus into an owned UUID alias through the
   canonical `prepare`/`rebuild` path, then run the actual full MCP smoke against
   live TÜİK with that alias. Keep two-second request/six-second download pacing
   and existing bounds. Retain JSON and delete only the owned acceptance index.
5. For a deployed index, refresh separately with `tuik-mcp-index --full
   --listing-attempts 2 --alternate-listing-mode`. Schema version 2 requires a
   full rebuild of an older mapping. Opt-in content indexing is available but
   lies outside the v0.3 metadata relevance corpus. Acceptance does not update
   the application alias or claim a second live crawl.

The full smoke explicitly uses live portal discovery for tables/publications
and separately requires ASCII indexed discovery → the same stable resource ID →
verified bounded CSV rows. A targeted recheck after an index refresh is:

```bash
export TUIK_OPENSEARCH_URL=http://127.0.0.1:9200
export TUIK_OPENSEARCH_INDEX=tuik-resources
.venv/bin/python scripts/live_smoke.py --indexed-only \
  --index-query 'altin haric dis ticaret hadleri' \
  --output /tmp/v0.3.0-indexed-mcp.json
```

## Preparation and publication

Follow topic → `develop` → `master` in `CONTRIBUTING.md`; the older
`release/0.3.0` suggestion in #29 is not an accepted topic prefix.

- [x] Branch `chore/29-prepare-0.3.0` from current `develop`; set version 0.3.0,
  add `[0.3.0] - 2026-11-05`, upgrade notes and candidate evidence.
- [ ] Merge preparation into `develop` after CI and one teammate approval.
  Its standalone `Closes #29` and `Closes #24` directives let the develop-merge
  workflow close accepted development. If code changes, repeat relevant checks.
- [ ] Open `develop` → `master`, review and pass CI; use a **merge commit**.
- [ ] Repository owner: add `OpenSearch integration` to the required checks on
  both protected branches and retain the candidate's successful cluster CI run.
- [ ] Create annotated `v0.3.0` on that accepted master merge and push the tag.
- [ ] Build artifacts from a clean checkout of the tag, not this preparation
  worktree. Publish wheel, source distribution and `SHA256SUMS` on the GitHub
  release using [the prepared notes](releases/v0.3.0.md); verify asset hashes.
- [ ] Confirm #29/#24 and all milestone items are completed, then close milestone 2.

Do not tag or publish an unreviewed topic commit. Candidate artifact hashes are
verification evidence; published hashes must come from the actual tagged source.
This workflow does not upload to PyPI or weaken branch protections.
