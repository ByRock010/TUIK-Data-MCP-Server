"""Bounded immutable query snapshots and process-local authenticated continuation."""

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timedelta

import pydantic_core

from tuik_mcp.cache import BytesTTLCache
from tuik_mcp.clients.base import SourceError
from tuik_mcp.config import PORTAL
from tuik_mcp.models.common import now
from tuik_mcp.models.dataset import DataResponse
from tuik_mcp.parsers.query import QueryError, QueryLimits, evaluate

QUERY_VERSION = 1


class QueryService:
    def __init__(self, files, settings):
        self.files = files
        self.limits = QueryLimits.from_settings(settings)
        self.ttl = settings.query_cursor_ttl_seconds
        self.cache = BytesTTLCache(settings.query_cache_bytes, max_entries=128)
        self.secret = secrets.token_bytes(32)
        self.lock = asyncio.Lock()

    def token(self, identity: str, offset: int) -> str:
        raw = json.dumps([QUERY_VERSION, identity, offset], separators=(",", ":")).encode()
        payload = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        signature = hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()
        return payload + "." + signature

    def decode(self, cursor: str):
        try:
            if not isinstance(cursor, str) or len(cursor) > 1024:
                raise ValueError()
            payload, signature = cursor.split(".")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", payload):
                raise ValueError()
            expected = hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError()
            data = json.loads(base64.b64decode(payload + "=" * (-len(payload) % 4), altchars=b"-_"))
            version, identity, offset = data
            if (
                version != QUERY_VERSION
                or not isinstance(identity, str)
                or not re.fullmatch(r"[0-9a-f]{32}", identity)
                or type(offset) is not int
                or offset < 1
            ):
                raise ValueError()
            return identity, offset
        except (ValueError, TypeError, binascii.Error, UnicodeError) as exc:
            raise SourceError(
                "invalid_cursor",
                "Invalid cursor or server session changed; restart without cursor.",
                PORTAL,
            ) from exc

    def page(self, state: dict, identity: str, offset: int, limit: int):
        body = state["response"]
        rows = body["rows"]
        if offset > len(rows):
            raise QueryError("invalid_cursor", "Cursor position is outside the retained result.")
        metadata = {key: value for key, value in body.items() if key != "rows"}

        def candidate(count):
            end = offset + count
            more = end < len(rows)
            return DataResponse(
                **metadata,
                rows=rows[offset:end],
                next_cursor=self.token(identity, end) if more else None,
                has_more=more,
                truncated=more,
                cursor_expires_at=state["expires_at"],
            )

        def fits(response):
            return len(pydantic_core.to_json(response.model_dump(mode="json"), indent=2)) <= (
                self.limits.max_output_bytes
            )

        low, high = 0, min(limit, len(rows) - offset)
        # The final page drops its cursor, so its size can be smaller than the
        # preceding candidate. Check it before binary-searching non-final pages.
        largest = candidate(high)
        if fits(largest):
            return largest
        while low < high:
            middle = (low + high + 1) // 2
            if fits(candidate(middle)):
                low = middle
            else:
                high = middle - 1
        result = candidate(low)
        if not fits(result) or offset < len(rows) and low == 0:
            raise QueryError(
                "query_limit_exceeded",
                "Metadata or a single row exceeds the output budget; project fewer columns.",
            )
        return result

    async def start(self, resource_id, cache_id, file, base, query, filters, sheet, limit):
        async with self.lock:
            try:
                csv = file.link.format == "csv"
                tables = await self.files.tables(
                    cache_id,
                    file,
                    scan_max_rows=self.limits.max_rows if csv else None,
                    scan_max_cells=self.limits.max_cells if csv else None,
                )
                if not tables:
                    raise SourceError(
                        "parse_failed", "Workbook contains no worksheets", file.link.source_url
                    )
                table = next((t for t in tables if t.sheet == sheet), None) if sheet else tables[0]
                if table is None:
                    raise ValueError(
                        f"Unknown sheet {sheet!r}; sheets: {[t.sheet for t in tables]}"
                    )
                columns, rows = await self.files.work(evaluate, table, query, filters, self.limits)
                labels = dict(zip(table.columns, table.columns_en, strict=False))
                response = dict(
                    **base,
                    format=file.link.format,
                    extraction=table.extraction,
                    sheet=table.sheet,
                    sheets=[] if csv else [t.sheet for t in tables],
                    table_title=table.title,
                    table_title_en=table.title_en,
                    unit=table.unit,
                    table_notes=table.notes,
                    source_note=table.source_note,
                    layout=None if csv else table.layout,
                    warnings=table.warnings,
                    columns=columns,
                    columns_en=[labels.get(name, "") for name in columns] if labels else [],
                    rows=rows,
                    total_rows=len(rows),
                    snapshot_id="sha256:" + file.digest,
                    status="ok",
                    message=(
                        "Complete local query; strings preserved. "
                        "Continuation uses this retained snapshot."
                    ),
                )
                # Override catalog provenance with the downloaded file's provenance.
                response.update(
                    source_url=file.link.source_url, retrieved_at=file.retrieved_at.isoformat()
                )
                ttl = min(self.ttl, self.files.cache.remaining_ttl("body:" + file.digest))
                state = {
                    "response": response,
                    "query": query.model_dump(mode="json"),
                    "filters": filters,
                    "digest": file.digest,
                    "version": QUERY_VERSION,
                    "expires_at": (now() + timedelta(seconds=ttl)).isoformat(),
                }
                payload = await self.files.work(pydantic_core.to_json, state)
                if len(payload) > self.limits.max_snapshot_bytes:
                    raise QueryError(
                        "query_limit_exceeded", "Query snapshot exceeds the retention budget."
                    )
                identity = secrets.token_hex(16)
                result = await self.files.work(self.page, state, identity, 0, limit)
                if result.has_more:
                    # The original body deadline also bounds state lifetime: a later
                    # re-download of identical bytes must not resurrect this cursor.
                    ttl = min(ttl, self.files.cache.remaining_ttl("body:" + file.digest))
                    if ttl <= 0:
                        raise QueryError(
                            "query_limit_exceeded", "File snapshot cannot be retained."
                        )
                    self.cache.set(identity, payload, ttl=ttl)
                    if (
                        self.cache.get(identity) is None
                        or self.files.cache.get("body:" + file.digest) is None
                    ):
                        raise QueryError(
                            "query_limit_exceeded",
                            "Snapshot cannot be retained; increase caches or narrow the query.",
                        )
                return result
            except QueryError as exc:
                raise SourceError(exc.code, str(exc), file.link.source_url) from exc

    async def resume(self, resource_id, cursor, query, filters, sheet, limit):
        identity, offset = self.decode(cursor)
        async with self.lock:
            payload = self.cache.get(identity)
            if payload is None:
                raise SourceError(
                    "cursor_expired", "Cursor expired or evicted; restart without cursor.", PORTAL
                )
            state = await self.files.work(json.loads, payload)
            response = state["response"]
            url = response["source_url"]
            if now() >= datetime.fromisoformat(state["expires_at"]):
                raise SourceError("cursor_expired", "Cursor expired; restart without cursor.", url)
            if self.files.cache.get("body:" + state["digest"]) is None:
                raise SourceError(
                    "cursor_expired",
                    "File snapshot expired or evicted; restart without cursor.",
                    url,
                )
            if state["version"] != QUERY_VERSION:
                raise SourceError(
                    "invalid_cursor", "Extraction version changed; restart without cursor.", url
                )
            if (
                resource_id != response["resource_id"]
                or (query is not None and query.model_dump(mode="json") != state["query"])
                or (filters is not None and filters != state["filters"])
                or (sheet is not None and sheet != response["sheet"])
            ):
                raise SourceError(
                    "cursor_mismatch",
                    "Cursor resource, sheet or query changed; restart without cursor.",
                    url,
                )
            try:
                return await self.files.work(self.page, state, identity, offset, limit)
            except QueryError as exc:
                raise SourceError(exc.code, str(exc), url) from exc
