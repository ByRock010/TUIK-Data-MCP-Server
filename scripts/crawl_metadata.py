"""Crawl public metadata to validated JSONL or report a bounded live dry run (#15).

No files or OpenSearch indexes are downloaded/written. All listing pages are visited
unless an explicit dry-run page budget is supplied; incremental runs replay cached
documents while avoiding unchanged detail requests.
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import httpx

from tuik_mcp.clients.crawl import CrawlHttpClient, CrawlSource
from tuik_mcp.config import Settings
from tuik_mcp.search.crawl import (
    CrawlState,
    MetadataCrawler,
    atomic_write,
    error_details,
    state_bytes,
)


async def run(args):
    report = {"status": "running", "state_committed": False, "dry_run": args.dry_run}
    http, crawler = None, None
    temporary, file = None, None
    try:
        state = await asyncio.to_thread(CrawlState.load, args.state)
        transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0") if args.ipv4 else None
        http = CrawlHttpClient(Settings.from_env(), transport)
        crawler = MetadataCrawler(CrawlSource(http), state)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            file = tempfile.NamedTemporaryFile(dir=args.output.parent, delete=False)
            temporary = Path(file.name)
        async for document in crawler.documents(
            full=args.full, max_bulletins=args.max_bulletins, page_limit=args.max_pages
        ):
            if file:
                file.write((document.model_dump_json() + "\n").encode())
        if file:
            file.flush()
            os.fsync(file.fileno())
            file.close()
            temporary.replace(args.output)
        if not args.dry_run:
            if crawler.next_state is None:
                raise ValueError("Partial crawls cannot advance incremental state")
            payload = await asyncio.to_thread(state_bytes, crawler.next_state)
            await asyncio.to_thread(atomic_write, args.state, payload)
        report.update(crawler.report, state_committed=not args.dry_run)
        return 0
    except Exception as exc:
        if crawler is not None:
            report.update(crawler.report)
        report.update(status="error", error=error_details(exc))
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if report["status"] == "running":
            if crawler is not None:
                report.update(crawler.report)
            report["status"] = "incomplete"
        if file and not file.closed:
            file.close()
        if temporary:
            temporary.unlink(missing_ok=True)
        if http is not None:
            await http.close()
        await asyncio.to_thread(
            atomic_write,
            args.report,
            (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode(),
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--full", action="store_true", help="Refresh all bulletin details")
    modes.add_argument(
        "--incremental", action="store_true", help="Reuse unchanged details (default)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not advance crawl state")
    parser.add_argument("--max-bulletins", type=int, help="Limit detail scope, dry-run only")
    parser.add_argument("--max-pages", type=int, help="Limit listing scope, dry-run only")
    parser.add_argument("--state", type=Path, default=Path(".tuik-crawl-state.json"))
    parser.add_argument("--output", type=Path, help="Atomically write validated metadata JSONL")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--ipv4", action="store_true", help="Use IPv4 transport if needed locally")
    args = parser.parse_args(argv)
    if (args.max_bulletins is not None or args.max_pages is not None) and not args.dry_run:
        parser.error("Scope limits are allowed only with --dry-run")
    if not args.output and not args.dry_run:
        parser.error("A non-dry run requires --output before state can be committed")
    paths = [p.resolve() for p in (args.state, args.output, args.report) if p is not None]
    if len(set(paths)) != len(paths):
        parser.error("State, output and report paths must be distinct")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
