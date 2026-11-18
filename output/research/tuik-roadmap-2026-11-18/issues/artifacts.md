## Goal

Make exported files retrievable by the user and reproducible from a pinned evidence bundle.

## Context

Existing one-hour file snapshots and query cursors are bounded transient state. A report must not silently mix files downloaded at different times, or return a hosted server path that the user cannot open. `develop` already returns structuredContent; reuse it.

## Proposal

Introduce a versioned artifact/bundle manifest containing source IDs, official URLs, retrieval/publication dates when available, source hashes, selection/query, locators, extraction/mapping versions, transformations, warnings, generated files, MIME types, sizes and hashes. Define server-owned storage, retention and restart behavior separately from query caches. Use opaque artifact IDs and MCP resource links/read support with explicit size limits, plus a documented local download route. Never let caller-supplied arbitrary paths determine writes.

## Acceptance criteria

- [ ] An export bundle pins all inputs and records differing retrieval times; available source bytes/derived inputs are retained within an explicit quota and reuse-rights policy.
- [ ] Stdio client can retrieve a real binary artifact; results contain valid MIME/name/size/hash and never imply a server-local path is a public download.
- [ ] Expiration, eviction, restart and unavailable original snapshots produce actionable outcomes; regeneration never substitutes fresh data without a new bundle/version.
- [ ] Budget total bytes, file count, lifetime and concurrent jobs; reject traversal/symlink escapes and unsafe filenames.
- [ ] Include checks for large-file delivery and clients that do not display resource links; keep model-visible summaries small.
- [ ] Provenance and warnings survive every output page/slide/sheet through a compact visible citation plus the full manifest.

## TÜİK sources involved

Existing public-source snapshots; [MCP tool results and resource links](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).

## Planning

- **Phase:** 1 — foundations
- **Priority:** high; **relative size:** L (not a delivery-date estimate).
- **Release scope:** first institutional-output slice.

## Dependencies

- **Blocked by:** none; can start independently.
- **Related:** #9, #27, #52
- **Epic:** #84
- **Roadmap:** #25
