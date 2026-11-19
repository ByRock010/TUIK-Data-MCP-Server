## Goal

Let recurring users update the same report without losing the prior numbers or confusing new observations with revisions.

## Context

Title-based file IDs follow current TÜİK files. An old retrieval timestamp does not make historical as-of data reconstructible; immutable bundles are needed first.

## Proposal

Save a validated selection/analysis/report recipe separate from its evidence bundle. A user-triggered refresh resolves current official inputs, creates a new bundle, reruns the approved deterministic operations and reports added/removed/revised observations, schema and methodology changes. Keep the original report retrievable until its stated retention expires. Detect fresh indexing vs fresh data independently; expose last successful upstream retrieval and unavailable inputs. Scheduling and email delivery are outside this first implementation.

## Acceptance criteria

- [ ] Recorded upstream changes show new periods separately from changed historical values and missing/deleted rows.
- [ ] Old artifacts are not overwritten and manifests explain the exact old/new snapshots and mapping/recipe versions.
- [ ] A changed unit/base year/classification/column schema interrupts unsafe automatic comparisons and requests a recipe update.
- [ ] No earlier vintage is claimed unless its source snapshot was actually retained; expired evidence has a clear unreproducible status.
- [ ] Refresh is idempotent for unchanged inputs apart from explicitly defined generation metadata; upstream failures do not present stale results as current.
- [ ] Document index-freshness warnings and the source cutoff on regenerated files.

## TÜİK sources involved

Current official CSV/Excel files, bulletin release metadata and retained artifact manifests.

## Planning

- **Phase:** 3b — repeatable reporting
- **Priority:** medium; **relative size:** L (not a delivery-date estimate).
- **Release scope:** expansion; does not gate the first institutional-output slice.

## Dependencies

- **Blocked by:** #91
- **Related:** #27, #28, #78
- **Epic:** #84
- **Roadmap:** #25
