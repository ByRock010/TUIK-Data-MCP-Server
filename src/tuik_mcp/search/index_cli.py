"""Crawl public TÜİK metadata and write it to the OpenSearch alias (#16).

--full refreshes every bulletin detail, loads a new versioned index and atomically
moves the alias. --incremental (default) reuses unchanged details and upserts new or
changed documents into the served index. Crawl state advances only after the index
write succeeds; --dry-run reads the cluster but writes neither index nor state.
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from tuik_mcp.clients.crawl import CrawlHttpClient, CrawlSource
from tuik_mcp.config import Settings
from tuik_mcp.models.common import now
from tuik_mcp.search.crawl import (
    CrawlState,
    MetadataCrawler,
    atomic_write,
    error_details,
    state_bytes,
)
from tuik_mcp.search.indexer import (
    IndexerError,
    OpenSearchStore,
    bounded,
    build_target,
    prepare,
    rebuild,
    served_index,
    update,
)


async def run(args, *, source=None, store=None) -> int:
    started = time.monotonic()
    report = {
        "status": "running",
        "mode": "full" if args.full else "incremental",
        "dry_run": args.dry_run,
        "started_at": now().isoformat(),
        "state_committed": False,
    }
    http, own_store = None, store is None
    try:
        settings = Settings.from_env()
        report["alias"] = settings.opensearch_index
        if store is None:
            if settings.opensearch_url is None:
                raise IndexerError("Set TUIK_OPENSEARCH_URL to the OpenSearch cluster")
            store = OpenSearchStore(str(settings.opensearch_url))
        # Fail before a crawl that can take an hour: reachable cluster, usable alias.
        await (build_target if args.full else served_index)(store, settings.opensearch_index)
        state = await asyncio.to_thread(CrawlState.load, args.state)
        if source is None:
            http = CrawlHttpClient(settings)
            source = CrawlSource(http)
        crawler = MetadataCrawler(source, state)
        try:
            documents = [document async for document in crawler.documents(full=args.full)]
        finally:
            report["crawl"] = crawler.report
        if crawler.next_state is None:
            raise IndexerError("The crawl did not complete; nothing was indexed")
        indexed_at = now()
        payloads, kinds = prepare(documents, indexed_at)
        report.update(indexed_at=indexed_at.isoformat(), counts=dict(kinds))
        write = rebuild if args.full else update
        report.update(await write(store, settings.opensearch_index, payloads, dry_run=args.dry_run))
        if not args.dry_run:
            # The index already holds every document, so a later failure loses nothing.
            payload = await asyncio.to_thread(state_bytes, crawler.next_state)
            await asyncio.to_thread(atomic_write, args.state, payload)
            report["state_committed"] = True
        report["status"] = "ok"
        return 0
    except Exception as exc:
        report.update(status="error", error=error_details(exc))
        if isinstance(exc, IndexerError) and exc.failures:
            report["failure_count"] = len(exc.failures)
            report["failures"] = bounded(exc.failures)
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if report["status"] == "running":
            report["status"] = "incomplete"
        report["finished_at"] = now().isoformat()
        report["duration_seconds"] = round(time.monotonic() - started, 3)
        if http is not None:
            await http.close()
        if own_store and store is not None:
            await store.close()
        payload = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode()
        if args.report:
            await asyncio.to_thread(atomic_write, args.report, payload)
        sys.stdout.write(payload.decode())


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tuik-mcp-index", description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--full", action="store_true", help="Refresh all details and rebuild the index"
    )
    modes.add_argument(
        "--incremental", action="store_true", help="Upsert new/changed documents (default)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Crawl and plan; write no index or state"
    )
    parser.add_argument("--state", type=Path, default=Path(".tuik-crawl-state.json"))
    parser.add_argument("--report", type=Path, help="Also write the JSON summary report here")
    args = parser.parse_args(argv)
    if args.report is not None and args.report.resolve() == args.state.resolve():
        parser.error("State and report paths must be distinct")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
