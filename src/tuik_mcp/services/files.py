"""Bounded downloads and parsed snapshots shared by table/document tools."""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from pydantic import TypeAdapter

from tuik_mcp.cache import BytesTTLCache, MemoryTTLCache
from tuik_mcp.clients.base import SourceError
from tuik_mcp.clients.tuik_portal import TuikPortal
from tuik_mcp.models.common import now
from tuik_mcp.models.dataset import DownloadLink
from tuik_mcp.parsers.limits import ParseLimits
from tuik_mcp.parsers.models import ParsedDocument, ParsedTable, TableProfile
from tuik_mcp.parsers.profile import InspectLimits, profile_table
from tuik_mcp.services.parsing import parse_document_source, parse_source

TABLES = TypeAdapter(list[ParsedTable])
PROFILE = TypeAdapter(tuple[list[str], TableProfile])


async def finish_worker(function, *args, **kwargs):
    """Cancellation cannot kill a thread: retain the parse slot until it finishes."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()  # Consume failures from a worker whose caller was cancelled.
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
        self.inspect = InspectLimits.from_settings(portal.http.settings)
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

    async def tables(
        self,
        resource_id: str,
        file: DownloadedFile,
        limit: int | None = None,
        *,
        scan_max_rows: int | None = None,
        scan_max_cells: int | None = None,
    ) -> list[ParsedTable]:
        """Parsed tables; `limit` bounds retained CSV rows while totals stay exact."""
        key = (
            f"tables:{resource_id}:{file.digest}:{file.link.format}:{limit}:"
            f"{scan_max_rows}:{scan_max_cells}"
        )
        async with self._parse_lock:
            cached = self.cache.get(key)
            if cached is not None:
                return await finish_worker(TABLES.validate_json, cached)
            tables = await finish_worker(
                parse_source,
                file.body,
                file.link.format or "",
                file.link.source_url,
                limit=limit,
                budgets=self.limits,
                scan_max_rows=scan_max_rows,
                scan_max_cells=scan_max_cells,
            )
            payload = await finish_worker(TABLES.dump_json, tables)
            self.cache.set(key, payload)
            return tables

    async def work(self, function, *args, **kwargs):
        """Query evaluation/serialization shares the parser's cancellation-safe slot."""
        async with self._parse_lock:
            return await finish_worker(function, *args, **kwargs)

    async def document(self, resource_id: str, file: DownloadedFile, page: int) -> ParsedDocument:
        key = f"pdf:{resource_id}:{file.digest}:{page}"
        async with self._parse_lock:
            cached = self.cache.get(key)
            if cached is not None:
                return await finish_worker(ParsedDocument.model_validate_json, cached)
            document = await finish_worker(
                parse_document_source,
                file.body,
                file.link.source_url,
                page=page,
                budgets=self.limits,
            )
            payload = await finish_worker(lambda: document.model_dump_json().encode())
            self.cache.set(key, payload)
            return document

    async def profile(
        self,
        resource_id: str,
        file: DownloadedFile,
        *,
        sheet: str | None,
        columns: list[str] | None,
        max_values: int,
    ) -> tuple[list[str], TableProfile]:
        """Sheet names and the selected table's profile for one retained snapshot."""
        key = "profile:" + json.dumps(
            [resource_id, file.digest, file.link.format, sheet, columns, max_values],
            ensure_ascii=False,
        )
        cached = self.cache.get(key)
        if cached is not None:
            return PROFILE.validate_json(cached)
        csv = file.link.format == "csv"
        tables = await self.tables(resource_id, file, self.inspect.max_rows if csv else None)
        if not tables:
            raise SourceError(
                "parse_failed", "Workbook contains no worksheets", file.link.source_url
            )
        sheets = [] if csv else [table.sheet for table in tables]
        table = next((t for t in tables if t.sheet == sheet), None) if sheet else tables[0]
        if table is None:
            raise ValueError(f"Workbook has no sheet named {sheet!r}; sheets: {sheets}")
        async with self._parse_lock:
            profile = await finish_worker(
                profile_table, table, max_values=max_values, limits=self.inspect, columns=columns
            )
        result = (sheets, profile)
        self.cache.set(key, PROFILE.dump_json(result))
        return result
