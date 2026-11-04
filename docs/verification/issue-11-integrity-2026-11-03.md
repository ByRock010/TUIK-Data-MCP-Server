# Evaluation integrity follow-up — 2026-11-03

Based on `develop` at `e06a28a`. This fixes two outstanding notes from the review
of [PR #49](https://github.com/ByRock010/TUIK-Data-MCP-Server/pull/49), authored by
ByRock010. It does not modify the open PDF/content PRs #65/#66 by Berkayacer13.

## Missing research evidence

The evaluation PR descriptions, issue comments, PR #49's review and evaluation
JSONs in available remote branches were inspected. No completed human judgment
sheet was found. PR #49 explicitly says all labels are null. Its reviewer used
random grades to exercise the merge/scorer; those are software-test observations,
not independent relevance assessments. The v0.2.0 release assets are package
archives/checksums, with no judgment attachment.

Scoring the committed baseline after this fix still reports:

```json
{
  "status": "pending_judgments",
  "judgments": 545,
  "completed": 0,
  "agreement": {"paired": 0, "cohen_kappa": null, "agreement": null}
}
```

This is evidence of missing labels in the accessible records, not a claim about
files held privately by either team member. #11/#20 remain open.

## Fixes and checks

- A retained judgment without document metadata previously caused `score` to
  raise `KeyError`. Pooling now fills metadata from all supplied rankings first,
  then rejects missing/invalid records with a query/ID-specific validation error.
  It preserves the judgment rather than silently dropping a relevant candidate.
  Judge export checks the same document integrity.
- `collect --resume` previously kept the initial revision but replaced the
  collector hash on every attempt. It now writes both provenance fields in every
  checkpoint and rejects a changed/missing revision or collector hash before
  creating HTTP clients or modifying the existing output. Use the original code
  or collect to a separate output file. Completed historical snapshots still score.
- Before the fix, **10 regression cases failed**, including the reproduced
  `KeyError` and changed-code resumes reaching HTTP setup. One positive metadata
  recovery case already passed. After the fix, all **27 targeted evaluation
  tests** pass; the existing successful retry test also checks early checkpoint
  provenance and preservation of the initial collection metadata.
- Full offline suite: **584 passed, 4 skipped, 6 deselected** on Python 3.14.0.
  Ruff lint/format, **28 Node tests**, and `git diff --check` pass.
- No TÜİK adapter, runtime search configuration or existing judgment was changed;
  no live network test is required for this offline evaluation change.

Reproduce the historical baseline check:

```bash
.venv/bin/python scripts/evaluate_search.py score \
  --runs docs/verification/search-baseline-2026-10-26.json \
  --output /tmp/tuik-baseline-check.json
```
