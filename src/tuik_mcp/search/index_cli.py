"""Crawl public TÜİK metadata and write it to the OpenSearch alias (#16).

--full refreshes every bulletin detail, loads a new versioned index and atomically
moves the alias. --incremental (default) reuses unchanged details and upserts new or
changed documents into the served index. Crawl state advances only after the index
write succeeds; --dry-run reads the cluster but writes neither index nor state.

--content also indexes file content (#22): the tables and PDF pages of the newest
bulletin in each series, at most --max-content-files downloads per run. --content-file
adds a file on demand and keeps it on later runs. Content already indexed is reused
from --content-state on every run, so a full rebuild does not drop it.
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
from tuik_mcp.search.content import ContentIndexer, ContentLimits, ContentState
from tuik_mcp.search.crawl import (
    CrawlState,
    MetadataCrawler,
    atomic_write,
    error_details,
    state_bytes,
)
from tuik_mcp.search.documents import file_id
from tuik_mcp.search.embeddings import Embedder
from tuik_mcp.search.indexer import (
    IndexerError,
    OpenSearchStore,
    bounded,
    build_target,
    check_vectors,
    prepare,
    rebuild,
    served_index,
    update,
)
from tuik_mcp.services.catalog import CatalogService


async def run(args, *, source=None, store=None, content_service=None, embedder=None) -> int:
    started = time.monotonic()
    report = {
        "status": "running",
        "mode": "full" if args.full else "incremental",
        "dry_run": args.dry_run,
        "started_at": now().isoformat(),
        "state_committed": False,
    }
    http, own_store = None, store is None
    content_http, own_service = None, content_service is None
    try:
        settings = Settings.from_env()
        report["alias"] = settings.opensearch_index
        if store is None:
            if settings.opensearch_url is None:
                raise IndexerError("Set TUIK_OPENSEARCH_URL to the OpenSearch cluster")
            store = OpenSearchStore(str(settings.opensearch_url))
        if getattr(args, "embeddings", False) and embedder is None:
            embedder = Embedder(cache_dir=settings.embedding_cache_dir)
        report["embedding_model"] = embedder.model if embedder is not None else None
        # Fail before a crawl that can take an hour: reachable cluster, usable alias.
        if args.full:
            await build_target(store, settings.opensearch_index)
        else:
            index = await served_index(store, settings.opensearch_index)
            await check_vectors(store, index, embedder)
        state = await asyncio.to_thread(CrawlState.load, args.state)
        if source is None:
            http = CrawlHttpClient(settings)
            source = CrawlSource(
                http, listing_auto_filter=getattr(args, "listing_auto_filter", False)
            )
        crawler = MetadataCrawler(source, state)
        try:
            documents = [
                document
                async for document in crawler.documents(
                    full=args.full,
                    listing_attempts=getattr(args, "listing_attempts", 1),
                    alternate_listing_mode=getattr(args, "alternate_listing_mode", False),
                )
            ]
        finally:
            report["crawl"] = crawler.report
        if crawler.next_state is None:
            raise IndexerError("The crawl did not complete; nothing was indexed")
        wanted = args.content or bool(args.content_file)
        content = None
        if wanted or args.content_state.exists():
            content_state = await asyncio.to_thread(ContentState.load, args.content_state)
            if content_service is None:
                content_http = CrawlHttpClient(settings)
                content_service = CatalogService(content_http)
            content = ContentIndexer(
                content_service, content_state, ContentLimits(max_downloads=args.max_content_files)
            )
            try:
                # Without --content only cached content is kept: nothing is downloaded.
                documents += await content.documents(
                    documents, on_demand=args.content_file, download=wanted
                )
            finally:
                report["content"] = content.report
        indexed_at = now()
        payloads, kinds = prepare(documents, indexed_at)
        report.update(indexed_at=indexed_at.isoformat(), counts=dict(kinds))
        write = rebuild if args.full else update
        report.update(
            await write(
                store,
                settings.opensearch_index,
                payloads,
                dry_run=args.dry_run,
                embedder=embedder,
            )
        )
        if not args.dry_run:
            # The index already holds every document, so a later failure loses nothing.
            payload = await asyncio.to_thread(state_bytes, crawler.next_state)
            await asyncio.to_thread(atomic_write, args.state, payload)
            if content is not None:
                payload = await asyncio.to_thread(state_bytes, content.next_state)
                await asyncio.to_thread(atomic_write, args.content_state, payload)
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
        if own_service and content_service is not None:
            await content_service.close()
        if content_http is not None:
            await content_http.close()
        if own_store and store is not None:
            await store.close()
        payload = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode()
        if args.report:
            await asyncio.to_thread(atomic_write, args.report, payload)
        sys.stdout.write(payload.decode())


def content_file(value: str) -> str:
    try:
        return file_id(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def content_budget(value: str) -> int:
    number = int(value)
    if not 0 <= number <= 1000:
        raise argparse.ArgumentTypeError("must be between 0 and 1000")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tuik-mcp-index", description=__doc__)
    parser.add_argument(
        "--alternate-listing-mode",
        action="store_true",
        help="Alternate autoFilter modes between complete listing attempts",
    )
    parser.add_argument(
        "--listing-auto-filter",
        action="store_true",
        help="Use the portal's autoFilter listing mode; completeness checks still apply",
    )
    parser.add_argument(
        "--listing-attempts",
        type=int,
        choices=range(1, 6),
        default=1,
        help="Bounded full-listing retries for overlapping pages (default: 1)",
    )
    parser.add_argument(
        "--embeddings",
        action="store_true",
        help="Add multilingual-e5-small vectors to metadata documents for hybrid search "
        "(needs the semantic extra; downloads the model once). Incremental updates of an "
        "index built with vectors require it too.",
    )
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
    parser.add_argument(
        "--content",
        action="store_true",
        help="Index tables and PDF pages of the newest bulletin in each series (#22)",
    )
    parser.add_argument(
        "--content-file",
        action="append",
        default=[],
        type=content_file,
        metavar="ID",
        help="Index this file on demand (press:N:file:..., publication:..., report:..., "
        "table:...); kept on later runs. Repeatable.",
    )
    parser.add_argument(
        "--max-content-files",
        type=content_budget,
        default=25,
        help="Files downloaded per run; others wait for the next run (default 25)",
    )
    parser.add_argument("--content-state", type=Path, default=Path(".tuik-content-state.json"))
    args = parser.parse_args(argv)
    paths = [args.state, args.content_state] + ([args.report] if args.report else [])
    if len({path.resolve() for path in paths}) != len(paths):
        parser.error("State, content state and report paths must be distinct")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
