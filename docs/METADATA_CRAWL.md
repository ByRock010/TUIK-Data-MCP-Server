# JSON metadata crawler (#15)

The crawler produces #14's validated `IndexDocument`s independently of OpenSearch.
It fetches the public dataflow catalog, statistical-theme tree, non-archive bulletin
listing pages and necessary detail JSON. It downloads no spreadsheets/PDFs/CSV
exports. Source text stays data; MCP tool behavior is unchanged.

## Run from a checkout

```bash
# Bounded preview; never advances state.
.venv/bin/python scripts/crawl_metadata.py --dry-run --full \
  --max-pages 2 --max-bulletins 3 --report /tmp/crawl-preview.json

# Full source refresh: validated JSONL, then a state checkpoint.
.venv/bin/python scripts/crawl_metadata.py --full \
  --output /tmp/tuik-metadata.jsonl --report /tmp/crawl-full.json

# Lists every page; reuses unchanged details.
.venv/bin/python scripts/crawl_metadata.py --incremental \
  --output /tmp/tuik-metadata.jsonl --report /tmp/crawl-incremental.json
```

Default state: `.tuik-crawl-state.json` (gitignored); override with `--state`.
State/output/report paths must differ. `--output` is optional only for dry runs.
Scope limits are dry-run-only and explicitly set `partial`; they never authorize a
checkpoint. Optional `--ipv4` is a local transport diagnostic, not a requirement.
Its custom transport does not inherit environment proxy configuration; use the
default transport when an environment proxy is required.

## Pacing and source freshness

Request starts are at least **two seconds** apart; higher configured intervals
are retained. A 429 establishes at least a 30-second shared cooldown before another
request/retry, honoring numeric `Retry-After` values up to five minutes. Existing
timeouts, attempts, decoded-body bounds, allowlist and redirect checks are reused.
Issue #15's old 0.5-second estimate is superseded by the rate-limit measurements in
`TUIK_SOURCES.md`. A full ~1,500-bulletin refresh can take an hour or more.

The listing is not chronological. Incremental runs still enumerate every page,
never stopping at a known ID/date. The first page establishes observed page size;
totals/end-of-list determine termination. Repeated pages, exhausted limits and a
final listing-row-count or unique-ID-count mismatch fail before checkpointing.
Duplicate IDs across pages are reported, but cannot establish complete coverage:
the same number of source rows may hide an unseen bulletin. A complete enumeration
must match the upstream total in both raw rows and unique IDs; otherwise it raises
`incomplete_listing` without producing replacement output or advancing state.
Partial previews may report overlaps but are never eligible for checkpoints.
The source does not
provide an atomic listing snapshot; changes during enumeration may require retry.

Dataflows are fetched each run. Existing bulletin details are reused only when
the listing date is unchanged. New IDs and changed dates fetch details. Revisions
without listing-date changes require periodic `--full` refreshes; full refresh is
also required after normalizer changes. No universal revision detection is claimed.

## Checkpoints and downstream delivery

State retains normalized bulletin/metadata documents and previous-release links.
Every run emits all documents in its scope, including reused records. Incremental
mode optimizes source fetching, not delivery: a failed index write cannot lose a
document merely because it was previously seen. Reused `retrieved_at` stays intact;
catalog retrieval and document generation have separate timestamps.

The async generator exposes `next_state` only after successful complete consumption.
An indexer must persist it **after** downstream writes/alias activation succeed.
The script atomically replaces the JSONL output before committing state. A source
or serialization failure preserves previous output/state. Interrupted iteration,
partial previews and dry runs cannot advance state. State/report writes use a
temporary file, flush/fsync and replacement. One writer per state file is supported;
concurrent writers/locking are outside this increment.

Only complete enumeration can reconcile absent IDs. `missing_from_listing` reports
them with an explicit warning. The [indexer](INDEXING.md) (#16) never deletes them
incrementally; a full rebuild drops them.
A partial listing
must not be used to remove documents from an existing index.

## Classification, relations and dates

Confirmed classification comes from the official theme tree's explicit bulletin
URLs, or a join of related dataflow IDs against fresh catalog category chains.
Tree classification can propagate through explicit `previousPresses` links; no
title-keyword inference is used. Numeric category nodes are retained; UI folders
such as “Haber Bültenleri” are excluded. Multiple assignments remain arrays.

The tree and databrowser use different ID systems: `category_path` namespaces
them as `theme-tree:8.55` versus `dataflow:8_1_4`. It does not invent equivalence.
Unknown categories stay empty. A theme-tree outage is explicit in the report and
uses only confirmed linked-flow categories. Each subsequent run retries taxonomy
and reclassifies reused details; an outage cannot cache an unconfirmed assignment
permanently. The report includes coverage numerator/denominator.

`previousPresses` forms connected release groups. `series_id` uses the lowest
numeric ID in the currently known component, including referenced older releases.
It is internal grouping, may change as more links/components become known, and is
not a public resource ID or permanent series identity. `press:` IDs stay unchanged.
Links outside the selected listing scope are not fetched recursively.

Nonempty metadata becomes a `press:<id>:metadata` document with parent/reciprocal
bulletin links. Confirmed dataflows and bulletins also receive reciprocal links.
Formats/downloadability are advertised availability, never download verification.
Naive portal publication/update wall times are explicitly localized using
`Europe/Istanbul`; ISO calendar dates retain their precision.

## Budgets and verification

Limits: inherited 10 MiB decoded HTTP bodies, 1,000 listing pages, 20,000 flow/bulletin
records, 50,000 theme nodes/depth 32, category-chain depth 32, and a 128 MiB serialized
state payload, plus shared document field limits. These bound inputs/stored payloads,
not total process memory. State serialization/loading runs off the event loop.

Reports include scope, fetched/reused counts, counts per kind, theme coverage,
missing IDs, timestamps and structured errors. Failed sources never become empty
catalogs, fixture fallbacks or invented successful counts.

```bash
.venv/bin/pytest tests/test_metadata_crawl.py -q
.venv/bin/pytest -m live -q
```

Recorded fixtures test source access separately from controlled pagination,
incremental replay, changed dates, outages, interruption, output/state preservation,
count reconciliation and 429 cooldown. Live acceptance must retain actual scope
and errors. See [dated evidence](verification/issue-15-2026-10-26.md).
Bulk indexing and alias swap are the [indexer](INDEXING.md) (#16); indexed search
remains #17–#18.
