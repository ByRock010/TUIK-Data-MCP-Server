"""Bounded downloads and parsed snapshots shared by table/document tools."""

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime

from pydantic import TypeAdapter

from tuik_mcp.cache import BytesTTLCache, MemoryTTLCache
from tuik_mcp.clients.tuik_portal import TuikPortal
from tuik_mcp.models.common import now
from tuik_mcp.models.dataset import DownloadLink
from tuik_mcp.parsers.limits import ParseLimits
from tuik_mcp.parsers.models import ParsedTable
from tuik_mcp.services.parsing import parse_source

TABLES = TypeAdapter(list[ParsedTable])


async def finish_worker(function, *args, **kwargs):
    """Cancellation cannot kill a thread: retain the parse slot until it finishes."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except Exception:
            pass
        raise


@dataclass(frozen=True)
class DownloadedFile:
    body: bytes
    link: DownloadLink
    retrieved_at: datetime
    digest: str


class FileStore:
    def __init__(self, portal: TuikPortal):
        self.portal = portal
        self.limits = ParseLimits.from_settings(portal.http.settings)
        self.cache = BytesTTLCache(portal.http.settings.parse_cache_bytes)
        self.metadata = MemoryTTLCache(max_entries=256)
        self._download_lock = asyncio.Lock()
        self._parse_lock = asyncio.Lock()

    async def download(self, resource_id: str, link: DownloadLink) -> DownloadedFile:
        # Stable IDs identify a one-hour snapshot, independent of rotating download URLs.
        async with self._download_lock:
            metadata = self.metadata.get(resource_id)
            if metadata is not None:
                verified, retrieved_at, digest = metadata
                body = self.cache.get("body:" + digest)
                if body is not None:
                    return DownloadedFile(body, verified, retrieved_at, digest)
            body, verified = await self.portal.download(link)
            retrieved_at = now()
            digest = hashlib.sha256(body).hexdigest()
            self.cache.set("body:" + digest, body)
            self.metadata.set(resource_id, (verified, retrieved_at, digest), 3600)
            return DownloadedFile(body, verified, retrieved_at, digest)

    async def tables(self, resource_id: str, file: DownloadedFile) -> list[ParsedTable]:
        key = f"tables:{resource_id}:{file.digest}:{file.link.format}"
        async with self._parse_lock:
            cached = self.cache.get(key)
            if cached is not None:
                return await finish_worker(TABLES.validate_json, cached)
            tables = await finish_worker(
                parse_source,
                file.body,
                file.link.format or "",
                file.link.source_url,
                budgets=self.limits,
            )
            payload = await finish_worker(TABLES.dump_json, tables)
            self.cache.set(key, payload)
            return tables
