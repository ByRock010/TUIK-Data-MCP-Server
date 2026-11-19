## Goal

Measure whether people can complete useful, source-correct work, building on the existing 8 November benchmark.

## Context

`develop` already contains `scripts/mcp_benchmark.py` and a 10-question MCP/web/closed-book comparison. Its single-run, same-family grading does not measure exported files, novice setup or different sectors. Do not recreate that harness or describe it as absent.

## Proposal

Freeze 24 tasks across finance/research, manufacturing/export, local government and academic/journalistic use (six per persona). Include current-data discovery, revisions, unavailable monthly provincial unemployment, ambiguous units, a calculation, and an artifact request. Reuse the existing prompts, logs and source-ground-truth method where applicable. Separate source correctness, model behavior, setup friction and document usability. Fix scoring, budgets and stop conditions before collecting results; retain a held-out subset.

## Acceptance criteria

- [ ] Publish the task matrix, dated official ground truth, expected supported/unsupported outcomes and a reproducible runner extension plan.
- [ ] Every task has a numeric/period/unit/source rubric and artifact tasks additionally specify editability and layout checks.
- [ ] Record a baseline against the current nine tools; mark missing capabilities as unsupported instead of scoring hypothetical implementations.
- [ ] Run or document availability of at least two named/versioned clients; model/provider, search index, warm/cold state and tool permissions are recorded separately.
- [ ] Distinguish independent human review from model-assisted grading; plan at least one external sector-user review per persona and disclose any unfilled recruitment.
- [ ] Pre-register candidate acceptance targets: zero critical numeric/source errors, >=90% supported-task success, and no concealed unsupported cases; publish denominators and uncertainty.

## TÜİK sources involved

[Existing benchmark](https://github.com/ByRock010/TUIK-Data-MCP-Server/blob/ea26bb4e8e1fd05eed815e4def469b527da9191a/docs/verification/mcp-benchmark-2026-11-08.md); official portal, tables and methodology PDFs used by the tasks.

## Planning

- **Phase:** 0 — evidence
- **Priority:** high; **relative size:** M (not a delivery-date estimate).
- **Release scope:** first institutional-output slice.

## Dependencies

- **Blocked by:** none; can start independently.
- **Related:** #78, #79, #80
- **Epic:** #84
- **Roadmap:** #25
