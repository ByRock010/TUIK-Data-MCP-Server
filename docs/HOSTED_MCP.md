# Hosted MCP local pilot

The optional `tuik-mcp-hosted` entry point runs one Streamable HTTP worker on
`127.0.0.1:8000`. It serves `/mcp` in stateless JSON mode. The local TLS
proxy forwards requests on `https://localhost:8443`. The existing
`tuik-mcp` stdio entry point remains the default. This implements the local
transport and workspace isolation part of the [pilot ADR](adr/0006-hosted-mcp-pilot.md).

## Local configuration

Install the hosted extra and add `viz` when testing chart saves.

```sh
python -m pip install -e '.[hosted,viz]'
```

Set these environment variables on the worker.

| Variable | Example or format |
| --- | --- |
| `TUIK_HOSTED_RESOURCE_URL` | `https://localhost:8443/mcp`. Match the exact token audience and external Host. |
| `TUIK_HOSTED_ISSUER_URL` | The configured HTTPS issuer URL, including any trailing slash present in its `iss` claim. |
| `TUIK_HOSTED_INTROSPECTION_URL` | The HTTPS token introspection endpoint on the issuer's origin. |
| `TUIK_HOSTED_INTROSPECTION_CLIENT_ID` | The issuer's confidential client ID for this resource server. |
| `TUIK_HOSTED_INTROSPECTION_SECRET_FILE` | An absolute secret file path readable by the worker. Direct environment injection through `TUIK_HOSTED_INTROSPECTION_SECRET` also works. Choose one source. |
| `TUIK_HOSTED_SUBJECTS` | JSON list of up to 20 stable issuer subjects. |
| `TUIK_HOSTED_CLIENT_IDS` | JSON list of the pre-registered OAuth client application IDs. |
| `TUIK_HOSTED_ARTIFACT_DIR` | Dedicated absolute workspace root, owned by the worker and permission mode `0700`. |

Register clients with the issuer for authorization code with PKCE and
audience-bound access tokens. The issuer must return `active`, `iss`, `sub`,
`aud`, `exp`, `client_id` and `scope` through introspection. The worker
checks active state on each request and accepts a caller only when the
configured issuer, resource, registered client and roster all match.
`tuik:read` is required throughout. `tuik:reports` is additionally required
for saving charts and reading report files. The 401/403 challenges and
protected-resource metadata give clients the canonical resource and scopes.
[OAuth token introspection](https://www.rfc-editor.org/rfc/rfc7662.html) ·
[Protected-resource metadata](https://www.rfc-editor.org/rfc/rfc9728.html)

## Container and local proxy

Place the non-secret worker variables above in a local env file. Compose
sets the artifact directory and secret mount internally. The host secret
file should live in a private directory and be readable by the container
worker through the explicit read-only mount. For a metadata-only smoke,
use a synthetic secret and a reserved `.test` issuer name.

```sh
export TUIK_HOSTED_ENV_FILE=/absolute/private/worker.env
export TUIK_HOSTED_INTROSPECTION_FILE=/absolute/private/introspection
docker compose -f docs/hosted/compose.yml up --build -d
docker compose -f docs/hosted/compose.yml exec -T proxy \
  cat /data/caddy/pki/authorities/local/root.crt > /tmp/tuik-local-ca.crt
curl --cacert /tmp/tuik-local-ca.crt https://localhost:8443/readyz
curl --cacert /tmp/tuik-local-ca.crt -i https://localhost:8443/mcp
curl --cacert /tmp/tuik-local-ca.crt \
  https://localhost:8443/.well-known/oauth-protected-resource/mcp
```

The expected results are readiness HTTP 200, a Bearer challenge with HTTP
401 and a metadata document advertising the configured issuer. Compose
publishes only the local TLS port and retains artifact volume contents when
stopped with `docker compose -f docs/hosted/compose.yml down`.
Use the local CA for these isolated requests. [Caddy local TLS](https://caddyserver.com/docs/caddyfile/directives/tls)

## Data boundaries and budgets

- Each allowlisted subject has its own cursor signer, query cache and opaque
  artifact directory. Query cursors survive reconnects in the same worker.
  A restart invalidates them. Unexpired report files survive when the same
  private volume is attached.
- Source download pacing and parsed source snapshots are shared. Search
  results keyed by a user's terms and profile selections stay out of shared
  caches. The host uses the existing literal/portal search paths. Hosted table
  reads use the bounded cursor path by default. PDF extraction stays on
  stdio until hosted PDF expansion can be bounded.
- Saved charts return artifact resource links. The worker reads the PNG over
  authenticated `resources/read` and preserves its MIME type. It keeps the
  source URLs, hash, selection and retrieval time in the manifest. The pilot
  retains generated report files for up to 24 hours and omits raw source bytes.
- The single worker admits four tool/resource calls and two parse or save
  jobs at once, with one active call per subject. It charges a conservative
  32 MiB for a new parse-capable request, allowing two per subject and eight
  globally per UTC day. Cursor continuation uses its existing snapshot.
- Authenticated request rate is 10/minute and 60/day per subject, with 60/minute,
  500/day and 10,000/month globally. Missing, malformed and rejected credentials
  share a separate peer budget. The first 30 failures per minute return 401;
  further failures return 429. The loopback proxy counts as one peer. A
  well-formed bearer is still introspected on each request, even after the
  peer exhausts its failure budget. The worker allows four concurrent issuer
  calls within its 16-connection ceiling. Excess calls receive 503.
- Counters are process-local. The worker caps request bodies at 64 KiB, regular
  JSON responses at 128 KiB and resource responses at 12 MiB. Work reaching the
  HTTP deadline returns 504.
- The workspace budgets share 512 MiB across active and retained workspace
  directories, with up to 64 MiB per workspace, 8 MiB per artifact file and
  16 MiB per bundle. Expiry is checked on read and swept at startup and hourly.
  The artifact root is exclusive to one worker. Restart the worker to apply
  roster or credential changes. Keep the dedicated volume private and encrypted.
- The proxy has access logging disabled. The worker emits HTTP status, response
  bytes and latency. `/livez` and `/readyz` expose only a status and use no
  upstream requests. Stop the proxy to pause admission. Re-use the dedicated
  volume when rolling back the worker image. The local pilot takes no
  off-host backups of retained user reports.

## Verification and remaining acceptance

On 25 November 2026 the local Compose stack passed the TLS readiness and
protected-resource discovery probes. An anonymous MCP request received HTTP
401 with its Bearer discovery challenge. A different Origin received HTTP
403. The smoke used a synthetic issuer configuration and contacted no issuer.

The hosted integration suite exercises the Python SDK over Streamable HTTP
with recorded TÜİK data and synthetic introspection. It verifies authenticated
initialize/list/call, concurrent caller isolation, shared upstream downloads,
cursor continuation, chart artifact save/read, scope enforcement, restart,
request budgets and invalid origin/issuer/audience/expiry cases.

```sh
python -m pytest -q tests/test_hosted_mcp.py
```

[#103](https://github.com/ByRock010/TUIK-Data-MCP-Server/issues/103)
remains open for the institutional issuer and roster, interactive Claude Code
and VS Code verification, measured capacity and disconnect behavior, and the
deployment gates in the ADR. Operating-quota durability across process
restarts, source outage circuit breaking and production ingress alerting are
also outside this local slice.
