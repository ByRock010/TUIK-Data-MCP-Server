# Metadata indexer (#16)

`tuik-mcp-index` runs the [metadata crawler](METADATA_CRAWL.md) in process and writes
its validated [`IndexDocument`s](SEARCH_INDEX.md) to OpenSearch behind the
`TUIK_OPENSEARCH_INDEX` alias (default `tuik-resources`). It talks to the cluster's
REST API through httpx; the optional `opensearch-py` extra is not required. MCP
tools are unchanged: routing searches to the index remains #17/#18.

```bash
export TUIK_OPENSEARCH_URL=http://localhost:9200

# First build, and any later rebuild: fresh details, new physical index, alias swap.
tuik-mcp-index --full --report /tmp/index-full.json

# Routine refresh: reuse unchanged details, upsert new/changed documents.
tuik-mcp-index --incremental --report /tmp/index-incremental.json

# Plan only: crawl and read the cluster; write neither index nor crawl state.
tuik-mcp-index --full --dry-run
```

The command uses the crawler's pacing (two-second request spacing, 429 cooldown),
so a full refresh of ~1,500 bulletins can take an hour or more. `--state` defaults
to the crawler's `.tuik-crawl-state.json`; one writer per state file and alias is
supported.

## Modes

**Full** (`--full`) creates the next physical index `<alias>-v<n>` (one above the
highest existing version) from the packaged settings and strict mapping, bulk loads
every document, refreshes and checks the document count, then moves the alias in
one `_aliases` request. Readers see either the previous index or the complete new
one. A rejected document, count mismatch or failed swap leaves the alias unchanged
and deletes the unused build, unless the alias already serves it (for example when
the swap response was lost). A concrete index named like the alias is refused.

Previous physical indices are retained for rollback and are never deleted
implicitly. Roll back by pointing the alias at an earlier version; remove old
versions explicitly when no longer needed:

```bash
curl -X POST localhost:9200/_aliases -H 'content-type: application/json' -d '{"actions": [
  {"remove": {"index": "tuik-resources-v2", "alias": "tuik-resources"}},
  {"add": {"index": "tuik-resources-v1", "alias": "tuik-resources"}}]}'
curl -X DELETE localhost:9200/tuik-resources-v1
```

**Incremental** (default) requires the alias to serve exactly one index whose
mapping `_meta.schema_version` equals the code's `SCHEMA_VERSION`; otherwise run a
full build. Documents are compared with their stored `_source`, ignoring
`indexed_at`: only new or changed documents are written, so unchanged documents
keep their previous `indexed_at`. Dataflow documents usually count as changed
because every crawl refreshes the catalog `retrieved_at`. Documents present in the
index but absent from the crawl are reported as `stale_ids`, never deleted: a full
build is the removal path. A rejected document fails the run after the accepted
writes (upserts are idempotent and the next run retries everything).

## State and failure semantics

The cluster and alias are checked before crawling, so an incremental run without a
usable alias, or a full build blocked by a concrete index named like the alias,
fails immediately instead of after an hour-long crawl.
The crawl must complete (`next_state` available) before anything is written; a
source failure, incomplete listing or interrupted crawl writes no index. Crawl
state is committed only after the index write (and, for full builds, the alias
swap) succeeds. Dry runs and failures leave state untouched. Because every crawl
re-emits all documents, a failed index write never loses a previously seen record.
All documents of a run share one `indexed_at`; duplicate IDs in crawl output fail
the run before any write.

## Report

The JSON report is printed to stdout and, with `--report`, atomically written to a
file. It contains `status`, `mode`, `dry_run`, `alias`, `index`, document `counts`
per kind, `indexed_at`, timestamps, `duration_seconds`, `state_committed` and the
complete crawler report under `crawl`. Full builds add `previous_indices`;
incremental runs add `created`, `updated`, `unchanged`, `stale_count` and up to 50
`stale_ids`. `written` appears only when documents were actually written. Rejected
documents appear as `failure_count` plus up to 50 `failures` with ID, HTTP status
and OpenSearch error type/reason. The exit status is 0 on success and 1 otherwise.

## Limits

Bulk requests carry at most 500 documents or about 5 MiB. The connection targets
the configured URL directly; proxy environment variables are not used, and the
local Compose cluster runs without security. Production authentication, TLS and
topology remain deployment decisions. Concurrent indexers, scheduled runs and
automatic retention are out of scope.

## Verify

```bash
.venv/bin/pytest tests/test_indexer.py -q
TUIK_OPENSEARCH_URL=http://localhost:9200 .venv/bin/pytest -m opensearch -q
```

The opt-in test crawls the recorded fixtures into a UUID alias, searches it
through the Turkish/ASCII analyzers, runs an incremental update and a second full
build, verifies that a cluster-rejected document discards the build without moving
the alias, and deletes only its own indices. See the
[dated evidence](verification/issue-16-2026-10-27.md).
