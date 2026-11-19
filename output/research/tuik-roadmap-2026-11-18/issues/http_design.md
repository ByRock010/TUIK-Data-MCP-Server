## Goal

Decide how a shared endpoint can preserve the local product guarantees before committing to hosting.

## Context

The product is currently local stdio. Hosted competitors reduce setup friction but introduce server-local export paths, shared quotas and credential/session concerns. Production HTTP is more than selecting a transport flag.

## Proposal

Write an ADR comparing a stateless public read-only endpoint and authenticated institutional access. Verify the current Python SDK transport/auth support and intended clients. Specify identity/session/cursor/artifact isolation, source pacing across users, limits/abuse controls, cancellation, storage TTL, uptime/diagnosis and secret handling. Estimate small pilot costs at documented load assumptions. Prototype locally against recorded sources; do not deploy a paid or public service as part of this research.

## Acceptance criteria

- [ ] ADR records the selected mode, alternatives, supported clients and a go/no-go criterion for #103.
- [ ] Define public-vs-private source/result/artifact sharing and how one user cannot retrieve another user’s retained reports or cursors.
- [ ] Budget requests, parsed bytes, concurrent jobs and artifact storage per caller plus globally; source download pacing remains shared.
- [ ] If HTTP authentication is offered, follow current MCP authorization discovery/audience rules; never forward one token to a different upstream.
- [ ] Plan Origin validation, TLS/reverse proxy, log redaction and health checks, with a deterministic local threat/compatibility test.
- [ ] Publish capacity assumptions, cost envelope and an operator runbook outline without buying infrastructure.

## TÜİK sources involved

[MCP transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports), [authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization); existing official-source adapters.

## Planning

- **Phase:** 4 — operational expansion
- **Priority:** medium; **relative size:** M (not a delivery-date estimate).
- **Release scope:** expansion; does not gate the first institutional-output slice.

## Dependencies

- **Blocked by:** none; can start independently.
- **Related:** #88, #98
- **Epic:** #84
- **Roadmap:** #25
