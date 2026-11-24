# ADR 0006 · Hosted MCP pilot

**Status** · Proposed for review. **Date** · 2026-11-23.
**Tracking** · [Research #102](https://github.com/ByRock010/TUIK-Data-MCP-Server/issues/102),
[implementation #103](https://github.com/ByRock010/TUIK-Data-MCP-Server/issues/103)
and [artifact storage #88](https://github.com/ByRock010/TUIK-Data-MCP-Server/issues/88).

## Decision

Proceed with the local implementation of an **authenticated, invite-only pilot**
once maintainers accept the operating envelope below. Keep the existing stdio
entry point as the default. The hosted profile uses a separate Streamable HTTP
entry point at `/mcp`, JSON responses, and no transport session ID. It serves
public TÜİK data with a private per-user workspace for cursors and reports. A
single Python worker owns upstream pacing, quotas and storage. A reverse proxy
terminates TLS. Public or paid deployment requires a separate decision after
#103 verifies the release gates.

| Alternative | Value | Decision |
| --- | --- | --- |
| Stateless, anonymous read-only HTTP | Lowest setup friction for public catalogue discovery | Defer. The endpoint would need an independent abuse budget. Anonymous callers would only get bounded, self-contained public results. Retained cursors, user-shaped results and reports would be disabled. |
| Authenticated institutional HTTP | Trusted caller identity makes private reports and per-user quotas possible | Select for the pilot. Limit the roster to 20 users with an approved institutional OAuth issuer. |
| Local stdio | Existing clients keep a local process and local artifact access | Continue to support it independently of the pilot. |

## Transport and client support

The project currently declares `mcp>=1.26,<2`. Python SDK **1.30.0** was tested
with `FastMCP.streamable_http_app()`, stateless JSON responses, `TokenVerifier`,
protected-resource metadata, and explicit `AuthSettings.validate_token_resource`.
The hosted implementation should pin a supported 1.x patch that provides subject
and resource validation and record the resolved version. Preserve the stdio
installation contract. The stable v2 line has its own migration path.
[Python SDK v1 documentation](https://py.sdk.modelcontextprotocol.io/v1/),
[SDK v1.30.0](https://github.com/modelcontextprotocol/python-sdk/tree/v1.30.0).

| Client | Streamable HTTP path | Evidence |
| --- | --- | --- |
| Python SDK 1.30.0 `ClientSession` | `streamable_http_client()` | Exercised with two identities in-process against recorded data. |
| Claude Code 2.1.158 | HTTP MCP config and remote OAuth flow | Installed version recorded. Vendor-documented support. End-to-end hosted login, query and artifact read are a #103 gate. [Claude Code MCP documentation](https://code.claude.com/docs/en/mcp). |
| VS Code 1.147.0 with the editor MCP client | An `http` server in MCP configuration with OAuth discovery | Editor version recorded. Vendor-documented support. Pin the client and any required extension when running #103 acceptance. [VS Code configuration reference](https://code.visualstudio.com/docs/agents/reference/mcp-configuration). |

Use POST for JSON-RPC with the negotiated `MCP-Protocol-Version` and the required
`Accept` types. Accept initialization notifications with HTTP 202. Authenticated
GET can return 405 when the optional SSE stream is unavailable. Require clients
to handle that status and reconnect using normal initialization. There is no
SSE replay or transport affinity. An MCP cursor belongs to the application
workspace and can survive a new connection in the same worker. A process restart
invalidates cursors and returns the documented restart-from-first-page result.
[Streamable HTTP specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).

## Authorization and data boundaries

- The reverse proxy validates `Host` and any supplied `Origin` against exact
  allowlists before authentication on the MCP endpoint. Reject a present invalid
  Origin with 403. Native clients can omit Origin and must still authenticate.
  Bind the worker to loopback or a private container network. Strip incoming
  forwarded headers at the edge and set trusted equivalents. Use HTTPS for the
  canonical service URL and OAuth issuer, with no cross-origin wildcard policy.
- Advertise RFC 9728 protected-resource metadata from the 401 challenge. The
  metadata identifies the canonical HTTPS `/mcp` resource and the approved
  authorization server. Clients discover that issuer's OAuth or OIDC metadata
  and use authorization code with PKCE and the canonical `resource` in both
  authorization and token requests. Pre-register the pilot clients. The verifier
  checks active status, signature or introspection, issuer, expiry, audience,
  scope and roster membership before any tool or resource call. `tuik:read`
  grants catalogue and data reads. Require `tuik:reports` as well for saving and
  reading private bundles. Invalid tokens get 401 and insufficient scope gets
  403 with the required scope in the challenge.
  Rotate issuer credentials outside the app and keep the app's introspection
  credential and any upstream credentials in a server-side secret store. Never
  forward a caller's bearer token to TÜİK.
  [MCP authorization specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization),
  [SDK bearer middleware](https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/server/auth/middleware/bearer_auth.py).
- Derive the workspace from the verified issuer and stable subject, scoped by
  institution if the issuer serves several. The OAuth client ID identifies the
  client application. Derive an opaque on-disk namespace from the workspace key.
  A request cannot supply or select its workspace through tool arguments,
  headers, cursor contents or artifact URIs. Fail closed when subject or scope
  is missing. Revalidate access on every request, including `resources/read`.
- Share public catalogue metadata and verified source snapshots by stable source
  ID and hash. Cache only source-derived parses that are independent of caller
  selection. Keep search histories, custom filters, rendered bytes, query
  snapshots and report manifests in their owner's workspace. Preserve original
  source URLs, retrieval times and hashes. Verify redistribution terms before
  retaining or redistributing an official source file and keep attribution.
  [TÜİK legal notice](https://www.tuik.gov.tr/Kurumsal/Yasal_Uyari).
- Reuse the existing query logic with one cursor namespace and signing key per
  workspace. Check ownership before reading snapshot state. Cursor TTL is 15
  minutes, with earlier eviction under the workspace byte cap. Share the source
  `HttpClient`, `TuikPortal` and `FileStore` process-wide so per-user state cannot
  multiply upstream pacing or parsed-source caches.
- Reuse immutable artifact manifests and deliver files through authenticated
  `resources/read` with the original MIME type. Resolve within the caller's
  namespace before reading a manifest or file. Treat other owners' IDs the same
  as unknown IDs. Cap raw files before reading or base64 encoding and set
  `Cache-Control: private, no-store` on MCP responses. Disable static serving,
  public share links and server filesystem paths. Keep bundles for at most 24
  hours on a private encrypted volume. Sweep at startup and at least hourly.
  A worker restart preserves unexpired files on the same volume. Loss of the
  volume requires regeneration from an available source snapshot.

## Admission and upstream budgets

These are initial pilot limits to enforce and measure in #103. Quota admission
and reservations must be atomic under concurrency. Daily quotas reset in UTC.

| Resource | Per verified caller | Global |
| --- | --- | --- |
| HTTP requests including initialization and artifact reads | 10/minute, 60/day | 60/minute, 500/day, 10,000/month. Apply a separate pre-auth IP limiter and a global connection ceiling. |
| Parsing | 64 MiB charged per day | 256 MiB charged per day. Each job is additionally bounded to 8 MiB fetched, 32 MiB expanded input, 100,000 cells and 100 PDF pages. Reserve the expanded bound when precise accounting is unavailable and reject formats whose expansion cannot be bounded. |
| Active MCP work | 1 tool or resource call | 4 active calls, including at most 2 parse/render jobs. Refuse excess work promptly, with no application wait queue. |
| Query snapshots | 2 MiB, 15 min | 40 MiB across all workspaces. Shared source/parse cache separately capped at 32 MiB. |
| Artifact storage and retrieval | 64 MiB retained, 1 concurrent save | 512 MiB retained, 2 concurrent saves, 8 MiB per file and 16 MiB per bundle including the manifest. Count staging and bytes reserved for in-flight writes. |
| HTTP payload | 64 KiB request, 128 KiB regular response | Resource reads use a separate 12 MiB wire limit including base64 and the JSON envelope. Account for timeouts and a 5 GiB monthly response budget at the edge. |

Use the current upstream gates across all callers, including retries and
redirects. Starts are spaced by at least 0.5 s for requests and 6 s for
file downloads. Respect TÜİK throttling and use bounded backoff. Open the circuit
on sustained upstream errors and serve only explicitly timestamped cache hits
while it is open. Disable semantic retrieval and OpenSearch in the first pilot
and expose the literal/portal fallback honestly. Avoid multiple app workers.
Any future replicas need a shared pacing and quota mechanism before they can
perform live requests.

Enforce a 30 s app deadline and a slightly longer proxy timeout. Cancel async
fetches on deadline or disconnect. A cancelled thread job keeps its slot and
reservation until it settles. Publish a completed artifact atomically, then
apply the usual expiry even if the caller disconnected after publication. Clean
up abandoned staging files at startup. JSON mode has no retained transport
session to correlate a cancellation notification from a separate request.
Verify cancellation and disconnect behavior in both pilot clients. If they
require remote cancellation independent of disconnect, add a bounded,
owner-scoped in-flight registry before rollout or reopen the transport decision.

## Capacity and operating estimate

Use 20 invited callers × 20 HTTP requests per workday × 22 days = 8,800 requests
per month. Allow 100 artifact file reads at up to 8 MiB raw each. At 128 KiB for
regular responses and allowing for base64, the estimate is roughly 2.2 GiB of
monthly egress before health checks and updates. Measure parsing at four
simultaneous requests with a mixed fixture workload and publish RSS, warm/cold
latency, quota responses and upstream request start times.

Indicative **USD 17.28/month** before tax at on-demand rates checked on
2026-11-23. This models one Compute Engine `e2-small` in `us-central1` with 2 GiB
of RAM, the existing approved issuer and DNS at zero incremental cost, and no
free credits. This is a costing example. Provider and region selection remain open for #103.

| Item | Monthly estimate |
| --- | ---: |
| VM at USD 0.016752855/hour × 730 hours ([VM pricing](https://cloud.google.com/products/compute/pricing/general-purpose?hl=en)) | USD 12.23 |
| 20 GiB standard disk at USD 0.000054795/GiB-hour × 730 hours ([disk pricing](https://cloud.google.com/compute/disks-image-pricing)) | USD 0.80 |
| One IPv4 address at USD 0.005/hour × 730 hours ([network pricing](https://cloud.google.com/vpc/network-pricing)) | USD 3.65 |
| 5 GiB North America/Europe outbound at USD 0.12/GiB ([network pricing](https://cloud.google.com/vpc/network-pricing)) | USD 0.60 |

Use a USD 25/month pilot approval envelope with alerts at 50%, 80% and 100%.
An alert is informational. The operator must pause ingress when the envelope
is forecast to be exceeded. Reprice any paid issuer, regional requirement,
external logging or backup before approval. Keep curated source fixtures,
configuration templates and image references in version control. Retained user
reports have no off-host backup by default. This is one zone and one worker,
with best-effort availability during the pilot and a target of restoring service
within one working day.

## Local evidence and #103 gates

Run `python -m pytest -q tests/test_hosted_mcp_prototype.py` from a development
install with `mcp==1.30.0`. The test app uses in-process ASGI over HTTP semantics
and `MockTransport` for the [recorded dataflow and CSV](../../tests/fixtures/README.md).
No sockets, live TÜİK requests, real credentials or model calls are needed.

The probe checks unauthenticated discovery, valid bearer authorization, wrong
resource audience, expiry, missing scope, and an invalid Origin with a valid
bearer. Two independent MCP clients initialize, discover `get_tuik_data`, and
read the same official snapshot through a single upstream file read. Each gets
its own cursor. The first caller reads the next row and its own seeded binary
bundle through MCP with the expected MIME type and bytes. The second caller
is denied both. Both fixture identities use the same OAuth client ID. The
prototype uses zero-delay pacing and a fixture verifier. It measures neither
live pacing, OAuth login, TLS nor reverse-proxy behavior.

Before granting a deployment go in #103, require all of the following.

1. Record an approved issuer and roster, two client versions that complete
   interactive auth, and a transcript of initialize/list/call/artifact read
   in each. Use the loopback/container test profile to exercise expiration,
   wrong audience, origin validation before auth, reconnect and restart.
2. Prove isolation and quotas under concurrent calls, including quota exhaustion,
   disconnect during a parse or save, old cursors after restart, and TTL cleanup.
   Verify the configured source pacing using request start times and ensure no
   unbounded work queues. Target peak process RSS under 1.2 GiB, warm p95 under
   2 s for metadata, and p95 under 15 s for a bounded fixture parse or save.
3. Verify the reverse proxy, storage ownership and secret redaction locally.
   Log only route template, status, latency, byte counts, bounded error codes
   and request IDs. Omit credentials, query strings, origins, source text,
   cursor values and artifact IDs, including SDK and proxy access logs. Use
   `/livez` for process health and `/readyz` for local storage/worker readiness.
   Keep periodic authenticated synthetic checks separate from health endpoints.
4. Publish a short operator runbook for version/issuer setup, allowlists,
   smoke checks, daily error/429/expiry/disk/billing dashboards and alert owners.
   Document admission pause, bounded drain, source outages, secret rotation,
   rollback to the prior image and access revocation. Run the existing stdio
   and container checks before requesting any live rollout.

Keep deployment at **no-go** if the issuer cannot mint audience-bound tokens,
only one intended client works, isolation or pacing fails, or safe operation
exceeds the accepted cost or memory envelope. Resolve failures in local
verification and retain stdio as the supported delivery path in the meantime.
