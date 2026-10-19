"""Refresh only the reviewed official fixture sources (network required).

This is not a crawler. Golden expectations must be reviewed separately after
refreshing; normal offline tests never invoke this script.
"""

import argparse
import asyncio
import hashlib
import json
import re
import sys
from pathlib import Path

from tuik_mcp.clients.base import HttpClient
from tuik_mcp.config import Settings
from tuik_mcp.models.common import now
from tuik_mcp.services.catalog import CatalogService

FILES = Path(__file__).resolve().parents[1] / "tests/fixtures/files"
MAX_CORPUS_BYTES = 3_000_000


def write_manifest(output: Path, entries: list[dict]) -> None:
    path = output / "manifest.json"
    temporary = output / "manifest.json.tmp"
    temporary.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


async def fetch(sources: list[dict], output: Path, *, resume: bool = False) -> list[dict]:
    manifest, total = [], 0
    keys = [source["key"] for source in sources]
    if len(set(keys)) != len(keys) or any(not re.fullmatch(r"[a-z0-9-]+", key) for key in keys):
        raise ValueError("Fixture keys must be unique safe basenames")
    previous = {}
    if resume and (output / "manifest.json").exists():
        previous = {
            item["key"]: item for item in json.loads((output / "manifest.json").read_text())
        }
    settings = Settings.from_env().model_copy(update={"download_interval_seconds": 6.0})
    http = HttpClient(settings)
    service = CatalogService(http)
    try:
        output.mkdir(parents=True, exist_ok=True)
        for source in sources:
            saved = previous.get(source["key"])
            if (
                saved
                and saved["title"] == source.get("file_title", source.get("title"))
                and saved.get("resource_id") == source.get("resource_id")
                and saved.get("search_query") == source.get("query")
                and saved.get("format") in {"xls", "xlsx", "pdf"}
                and saved["file"] == f"{source['key']}.{saved['format']}"
            ):
                path = output / saved["file"]
                body = path.read_bytes() if path.is_file() else None
                if body is not None and hashlib.sha256(body).hexdigest() == saved["sha256"]:
                    total += len(body)
                    if total >= MAX_CORPUS_BYTES:
                        raise ValueError("Fixture corpus must stay below 3 MB")
                    manifest.append({**saved, "size_bytes": len(body)})
                    continue
            if "resource_id" in source:
                resource = await service.resource(source["resource_id"])
                links = [
                    link for link in resource.download_links if link.title == source["file_title"]
                ]
            else:
                results = await service.search(source["query"], limit=50)
                matches = [item for item in results.results if item.title == source["title"]]
                if len(matches) != 1:
                    raise ValueError(f"Ambiguous or missing publication: {source['key']}")
                resource = await service.resource(matches[0].id)
                links = resource.download_links
            if len(links) != 1:
                raise ValueError(f"Ambiguous or missing download: {source['key']}")
            body, verified = await service.portal.download(links[0])
            # download() uses the shared production file-format detector.
            fmt = verified.format
            if fmt not in {"xls", "xlsx", "pdf"}:
                raise ValueError(f"Unexpected fixture format: {fmt}")
            total += len(body)
            if total >= MAX_CORPUS_BYTES:
                raise ValueError("Fixture corpus must stay below 3 MB; select smaller sources")
            filename = f"{source['key']}.{fmt}"
            (output / filename).write_bytes(body)
            manifest.append(
                {
                    "key": source["key"],
                    "file": filename,
                    "golden": f"{source['key']}.golden.json",
                    "title": links[0].title,
                    "format": fmt,
                    "resource_id": source.get("resource_id"),
                    "search_query": source.get("query"),
                    "source_page": resource.source_url,
                    "source_url": verified.source_url,
                    "retrieved_at": now().isoformat(),
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "size_bytes": len(body),
                    "publisher": "Türkiye İstatistik Kurumu (TÜİK)",
                }
            )
            # Retain unprocessed resume entries; verify their identity/hash when reached.
            updated = {item["key"]: item for item in manifest}
            write_manifest(
                output,
                [
                    updated.get(key, previous.get(key))
                    for key in keys
                    if key in updated or key in previous
                ],
            )
            print(f"{filename}: {len(body)} bytes ({total} total)", file=sys.stderr, flush=True)
        write_manifest(output, manifest)
        return manifest
    finally:
        await http.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=FILES / "sources.json")
    parser.add_argument("--output", type=Path, default=FILES)
    parser.add_argument("--resume", action="store_true", help="Keep matching verified local files")
    args = parser.parse_args()
    asyncio.run(
        fetch(json.loads(args.sources.read_text(encoding="utf-8")), args.output, resume=args.resume)
    )


if __name__ == "__main__":
    main()
