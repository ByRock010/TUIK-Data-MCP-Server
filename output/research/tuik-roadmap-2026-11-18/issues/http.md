## Goal

Offer a tested shared-service deployment option for nontechnical users and institutional pilots.

## Context

Implementation is conditional on the hosted-design ADR. This issue does not authorize spending, purchasing a domain or publishing an unreviewed live endpoint.

## Proposal

Implement the ADR’s Streamable HTTP profile while preserving stdio. Reuse all read/analysis/export services behind it. Add session/user isolation, controlled binary artifact delivery, global source pacing, quotas, structured diagnostics and clean shutdown. Supply a local container/reverse-proxy deployment recipe and operational smoke tests; separate any real rollout into the reviewed release process.

## Acceptance criteria

- [ ] HTTP initialize/list/call and artifact retrieval work in two selected client versions; reconnect, cancellation, disconnect and process restart have defined behavior.
- [ ] Two concurrent test identities cannot access each other’s artifacts/cursors; public caches share only source-derived data permitted by the ADR.
- [ ] Origin/auth policy, invalid credentials, expiration, download authorization and quota exhaustion are tested.
- [ ] A bounded concurrent load test measures latency/memory and confirms paced source requests and no unbounded job queue.
- [ ] Logs and health endpoints expose no secrets or private report contents; operational docs include backup/retention/rollback where applicable.
- [ ] Local container verification is complete before any deployment request; all existing stdio workflows pass.

## TÜİK sources involved

Existing TÜİK adapters; approved transport/authorization profile in #102.

## Planning

- **Phase:** 4 — operational expansion
- **Priority:** medium; **relative size:** L (not a delivery-date estimate).
- **Release scope:** expansion; does not gate the first institutional-output slice.
- **Implementation gate:** Proceed only if #102 records a go decision and an accepted operating envelope. A no-go is a planning decision, not completed implementation.

## Dependencies

- **Blocked by:** #102, #88
- **Related:** #98
- **Epic:** #84
- **Roadmap:** #25
