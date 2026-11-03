"""File content documents for passage-level search (#22).

By default the newest bulletin of each series contributes its XLS/XLSX tables and
PDF pages; files named with `--content-file` are indexed on demand and kept on later
runs. Files resolve, download and parse through the MCP server's CatalogService, so
the allowlist, file verification, download throttle and parse budgets are the ones
the tools use. A content state reuses the documents of files indexed before: file
IDs embed their bulletin, so a cached file never needs downloading again.
"""

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict, Field

from tuik_mcp.clients.base import SourceError
from tuik_mcp.config import PORTAL
from tuik_mcp.parsers.errors import ParseError
from tuik_mcp.parsers.models import ParsedTable
from tuik_mcp.parsers.pdf import chunk_pages, parse_pdf
from tuik_mcp.services.catalog import CatalogService

from .crawl import MAX_STATE_BYTES, error_details
from .documents import IndexDocument, chunk_id, file_id, table_id

CONTENT_FORMATS = {"xls", "xlsx", "pdf"}
MAX_FILES = 5000
# Failed files are retried after every never-tried file, at most this many times.
MAX_ATTEMPTS = 3
MAX_REPORTED = 50
COLUMN_MARKER = re.compile(r"\s*\[column \d+\]$")
PLACEHOLDER = re.compile(r"column_\d+")


@dataclass(frozen=True)
class ContentLimits:
    max_downloads: int = 25
    max_pdf_pages: int = 300
    chunk_chars: int = 1500
    max_documents_per_file: int = 2000


class BulletinFiles(BaseModel):
    model_config = ConfigDict(extra="forbid")
    published: str | None = None
    file_ids: list[str] = Field(default_factory=list)


class ContentFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    on_demand: bool = False
    documents: list[IndexDocument] = Field(default_factory=list)


class FailedFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempts: int = Field(ge=1)
    code: str | None = None
    # For a bulletin file list: the publication it failed for; a new one starts over.
    published: str | None = None


class ContentState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = 1
    bulletins: dict[str, BulletinFiles] = Field(default_factory=dict, max_length=MAX_FILES)
    files: dict[str, ContentFile] = Field(default_factory=dict, max_length=MAX_FILES)
    # Listed files that could not be indexed yet, so retries never starve new files.
    failures: dict[str, FailedFile] = Field(default_factory=dict, max_length=MAX_FILES)
    # Bulletins whose file list could not be fetched yet, scheduled the same way.
    list_failures: dict[str, FailedFile] = Field(default_factory=dict, max_length=MAX_FILES)

    @classmethod
    def load(cls, path: Path) -> "ContentState":
        if not path.exists():
            return cls()
        if path.stat().st_size > MAX_STATE_BYTES:
            raise ValueError("Content state exceeds its byte budget")
        state = cls.model_validate_json(path.read_bytes())
        if state.version != 1:
            raise ValueError("Unsupported content-state version; delete it to start over")
        for key, entry in state.files.items():
            file_id(key)
            if any(document.parent_id != key for document in entry.documents):
                raise ValueError("Content state documents belong to another file")
        for key in state.failures:
            file_id(key)
        for key in state.list_failures:
            if not re.fullmatch(r"press:[1-9][0-9]{0,9}", key):
                raise ValueError("Content state list failures must name bulletins")
        return state


def published(document: IndexDocument) -> str | None:
    value = document.publication_date
    return value.isoformat() if isinstance(value, date | datetime) else None


def latest_per_series(documents: list[IndexDocument]) -> list[IndexDocument]:
    """The newest bulletin of each series, newest first; undated bulletins last."""

    def recency(document):
        return (published(document) or "")[:10], int(document.id.split(":")[1])

    latest: dict[str, IndexDocument] = {}
    for document in documents:
        if document.kind != "bulletin":
            continue
        key = document.series_id or document.id
        if key not in latest or recency(document) > recency(latest[key]):
            latest[key] = document
    return sorted(latest.values(), key=recency, reverse=True)


def header_text(table: ParsedTable) -> str:
    """Titles, headers, units and notes: what a table is about, not its values."""
    columns = [COLUMN_MARKER.sub("", column) for column in table.columns + table.columns_en]
    parts = [column for column in columns if column and not PLACEHOLDER.fullmatch(column)]
    parts += [table.unit or "", *table.notes, table.source_note or ""]
    return "\n".join(dict.fromkeys(part for part in parts if part))


class ContentIndexer:
    def __init__(
        self,
        service: CatalogService,
        state: ContentState | None = None,
        limits: ContentLimits | None = None,
    ):
        self.service, self.state = service, state or ContentState()
        self.limits = limits or ContentLimits()
        self.next_state: ContentState | None = None
        self.report: dict = {}
        self.remaining = 0

    async def documents(
        self, metadata: list[IndexDocument], *, on_demand: list[str] = (), download: bool = True
    ) -> list[IndexDocument]:
        """Content documents for this snapshot; `download=False` reuses cached files only.

        Indexed files are reused and never dropped for lack of budget. The budget goes
        first to explicit on-demand refreshes, then to listed files never tried, then
        to earlier failures (fewest attempts first, at most MAX_ATTEMPTS each).
        """
        on_demand = list(dict.fromkeys(file_id(value) for value in on_demand))
        bulletins = {d.id: d for d in metadata if d.kind == "bulletin"}
        latest = latest_per_series(metadata)
        report = self.report = {
            "series": len(latest),
            "file_lists_fetched": 0,
            "downloaded": 0,
            "reused": 0,
            "pending_bulletins": 0,
            "pending_files": 0,
            "pending_refreshes": 0,
            "given_up": 0,
            "lists_given_up": 0,
            "failures": [],
            "page_limited": [],
            "on_demand": on_demand,
        }
        self.remaining = self.limits.max_downloads if download else 0
        lists_left = self.remaining
        state = ContentState()
        results: list[IndexDocument] = []

        def keep(fid: str, entry: ContentFile):
            state.files[fid] = entry
            results.extend(entry.documents)

        async def attempt(fid: str, parent: IndexDocument | None):
            """(documents, None) on success, even with no documents; (None, code) on failure."""
            self.remaining -= 1
            try:
                documents = await self.extract(fid, parent)
            except (SourceError, ParseError, ValueError) as exc:
                details = error_details(exc)
                report["failures"].append({"id": fid} | details)
                return None, details.get("code")
            report["downloaded"] += 1
            return documents, None

        # Explicit requests refresh first; a deferred or failed refresh keeps the cache.
        for fid in on_demand:
            cached = self.state.files.get(fid)
            documents = None
            if self.remaining > 0:
                documents, _ = await attempt(fid, self.parent(fid, bulletins))
            elif cached is not None:
                report["pending_refreshes"] += 1
            else:
                report["pending_files"] += 1
            if documents is not None:
                keep(fid, ContentFile(on_demand=True, documents=documents))
            elif cached is not None:
                keep(fid, ContentFile(on_demand=True, documents=cached.documents))
        for fid, cached in self.state.files.items():
            if cached.on_demand and fid not in state.files and fid not in on_demand:
                report["reused"] += 1
                keep(fid, cached)

        # File lists to (re)fetch: never-tried first, then earlier failures by attempts,
        # each retried at most MAX_ATTEMPTS times per publication, so lists that keep
        # failing cannot starve the bulletins after them.
        lists = {b.id: self.state.bulletins.get(b.id) for b in latest}
        fresh: set[str] = set()
        stale = [b for b in latest if lists[b.id] is None or lists[b.id].published != published(b)]

        def list_attempts(bulletin: IndexDocument) -> int:
            failed = self.state.list_failures.get(bulletin.id)
            if failed is None or failed.published != published(bulletin):
                return 0
            return failed.attempts

        stale.sort(key=list_attempts)
        for bulletin in stale:
            attempts = list_attempts(bulletin)
            if attempts >= MAX_ATTEMPTS:
                report["lists_given_up"] += 1
                state.list_failures[bulletin.id] = self.state.list_failures[bulletin.id]
                continue
            if lists_left <= 0:
                report["pending_bulletins"] += 1
                if attempts:
                    state.list_failures[bulletin.id] = self.state.list_failures[bulletin.id]
                continue
            lists_left -= 1
            try:
                resource = await self.service.resource(bulletin.id)
            except SourceError as exc:
                report["failures"].append({"id": bulletin.id} | error_details(exc))
                state.list_failures[bulletin.id] = FailedFile(
                    attempts=attempts + 1, code=exc.code, published=published(bulletin)
                )
                continue
            report["file_lists_fetched"] += 1
            fresh.add(bulletin.id)
            lists[bulletin.id] = BulletinFiles(
                published=published(bulletin),
                file_ids=[
                    link.resource_id
                    for link in resource.download_links
                    if link.format in CONTENT_FORMATS and link.resource_id
                ],
            )

        candidates: list[tuple[str, IndexDocument]] = []
        for bulletin in latest:
            listed = lists[bulletin.id]
            if listed is None:
                continue
            # An outdated list keeps its files indexed, and is retried, until one is fetched.
            state.bulletins[bulletin.id] = listed
            for fid in listed.file_ids:
                if fid in state.files:
                    continue
                if fid in self.state.files:
                    report["reused"] += 1
                    keep(fid, ContentFile(documents=self.state.files[fid].documents))
                else:
                    if bulletin.id not in fresh and fid in self.state.failures:
                        state.failures[fid] = self.state.failures[fid]
                    candidates.append((fid, bulletin))
        # Never-tried files first, then earlier failures by attempts: failures cannot
        # starve later files, and each failure is retried a bounded number of times.
        candidates.sort(key=lambda item: self.attempts(state, item[0]))
        for fid, bulletin in candidates:
            failed = state.failures.get(fid)
            if failed is not None and failed.attempts >= MAX_ATTEMPTS:
                report["given_up"] += 1
            elif self.remaining > 0:
                documents, code = await attempt(fid, bulletin)
                if documents is not None:
                    state.failures.pop(fid, None)
                    keep(fid, ContentFile(documents=documents))
                else:
                    state.failures[fid] = FailedFile(
                        attempts=self.attempts(state, fid) + 1, code=code
                    )
            else:
                report["pending_files"] += 1
        report["files"] = len(state.files)
        report["documents"] = dict(Counter(document.kind for document in results))
        report["failures"] = report["failures"][:MAX_REPORTED]
        report["page_limited"] = report["page_limited"][:MAX_REPORTED]
        self.next_state = state
        return results

    @staticmethod
    def attempts(state: ContentState, fid: str) -> int:
        failed = state.failures.get(fid)
        return failed.attempts if failed is not None else 0

    @staticmethod
    def parent(fid: str, bulletins: dict[str, IndexDocument]) -> IndexDocument | None:
        if not fid.startswith("press:"):
            return None
        return bulletins.get(":".join(fid.split(":")[:2]))

    async def extract(self, fid: str, parent: IndexDocument | None) -> list[IndexDocument]:
        resource = await self.service.resource(fid)
        if not resource.download_links:
            raise SourceError("not_document", "The file has no download", resource.source_url)
        link = resource.download_links[0]
        file = await self.service.files.download(fid, link)
        fmt = file.link.format
        name = link.title or resource.title
        common = dict(
            parent_id=fid,
            formats=[fmt] if fmt else [],
            downloadable=True,
            retrieved_at=file.retrieved_at,
            source_url=(
                parent.source_url
                if parent is not None
                else resource.source_url
                if fid.startswith("press:")
                else f"{PORTAL}/tr/search?" + urlencode({"q": name})
            ),
        )
        context = []
        if parent is not None:
            common.update(
                theme=parent.theme,
                subtheme=parent.subtheme,
                series_id=parent.series_id,
                period=parent.period,
                publication_date=parent.publication_date,
                related_ids=[parent.id],
            )
            context.append(parent.title)
        if fmt in {"xls", "xlsx"}:
            documents = []
            tables = await self.service.files.tables(fid, file)
            for ordinal, table in enumerate(tables, 1):
                title = table.title or name
                documents.append(
                    IndexDocument(
                        **common,
                        id=table_id(fid, ordinal),
                        kind="table",
                        title=title,
                        title_en=table.title_en,
                        description=" — ".join(
                            dict.fromkeys(filter(None, [*context, name if name != title else None]))
                        )
                        or None,
                        body=header_text(table) or None,
                        sheet=table.sheet,
                    )
                )
        elif fmt == "pdf":
            documents = []
            per_page: Counter[int] = Counter()
            step = self.service.files.limits.max_pdf_pages_per_call
            start, last = 1, self.limits.max_pdf_pages
            while start <= last:
                parsed = await self.service.files.work(
                    parse_pdf,
                    file.body,
                    page=start,
                    pages=min(step, last - start + 1),
                    budgets=self.service.files.limits,
                )
                if parsed.page_count > self.limits.max_pdf_pages and start == 1:
                    self.report["page_limited"].append(fid)
                last = min(last, parsed.page_count)
                for chunk in chunk_pages(parsed.pages, self.limits.chunk_chars):
                    per_page[chunk.page] += 1
                    documents.append(
                        IndexDocument(
                            **common,
                            id=chunk_id(fid, chunk.page, per_page[chunk.page]),
                            kind="document_chunk",
                            title=name,
                            description=" — ".join(filter(None, [*context, chunk.heading])) or None,
                            body=chunk.text,
                            page=chunk.page,
                        )
                    )
                start += step
        else:
            raise SourceError(
                "unsupported_download",
                f"No content extraction for format {fmt!r}",
                file.link.source_url,
            )
        if len(documents) > self.limits.max_documents_per_file:
            raise ValueError(
                f"{fid} produced {len(documents)} documents; the limit is "
                f"{self.limits.max_documents_per_file}"
            )
        return documents
