"""Derive validated metadata documents without importing an OpenSearch client."""

import asyncio
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from tuik_mcp.clients.base import SourceError
from tuik_mcp.clients.crawl import CrawlSource
from tuik_mcp.clients.tuik_portal import TuikPortal
from tuik_mcp.config import PORTAL
from tuik_mcp.models.common import now
from tuik_mcp.search.documents import IndexDocument
from tuik_mcp.utils.normalization import official_url, plain_text, resource_id

MAX_STATE_BYTES = 128 * 1024 * 1024
MAX_RECORDS = 20_000


def error_details(exc):
    details = {"type": type(exc).__name__, "message": str(exc)}
    if isinstance(exc, SourceError):
        details.update(code=exc.code, source_url=exc.source_url)
    return details


def portal_date(value):
    """Portal wall-clock publication times use Europe/Istanbul; preserve date precision."""
    if value is None:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo("Europe/Istanbul"))


def bulletin_id(url):
    value = resource_id(official_url(url))
    if not re.fullmatch(r"press:[1-9][0-9]{0,9}", value):
        raise ValueError("Expected a stable bulletin URL")
    return value


class PressSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bulletin: IndexDocument
    metadata: IndexDocument | None = None
    previous_ids: list[str] = Field(default_factory=list)
    listing_date: str | None = None


class CrawlState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = 1
    records: dict[str, PressSnapshot] = Field(default_factory=dict, max_length=MAX_RECORDS)

    @classmethod
    def load(cls, path: Path):
        if not path.exists():
            return cls()
        if path.stat().st_size > MAX_STATE_BYTES:
            raise ValueError("Crawl state exceeds its byte budget")
        state = cls.model_validate_json(path.read_bytes())
        if state.version != 1:
            raise ValueError("Unsupported crawl-state version; perform a fresh full run")
        for key, record in state.records.items():
            if key != record.bulletin.id or record.bulletin.kind != "bulletin":
                raise ValueError("State key/bulletin mismatch")
            if record.metadata and record.metadata.parent_id != key:
                raise ValueError("State metadata belongs to another bulletin")
            for previous in record.previous_ids:
                if not re.fullmatch(r"press:[1-9][0-9]{0,9}", previous):
                    raise ValueError("State contains an invalid series relation")
        return state


def theme_memberships(nodes):
    memberships = {}
    visited = 0

    def visit(node, path, depth):
        nonlocal visited
        visited += 1
        if visited > 50_000 or depth > 32:
            raise ValueError("Theme tree exceeds the crawl budget")
        # Source category IDs are numeric dotted paths; UI folders (.b/.i/...) are not categories.
        categories = (
            path + [(node.id, node.name)] if re.fullmatch(r"\d+(?:\.\d+)*", node.id) else path
        )
        if node.url:
            try:
                key = bulletin_id(node.url)
            except ValueError:
                pass
            else:
                memberships.setdefault(key, []).append(categories)
        for child in node.children:
            visit(child, categories, depth + 1)

    for root in nodes:
        visit(root, [], 0)
    return memberships


def category_names(flow):
    names, node = [], flow.category
    while node is not None:
        if len(names) >= 32:
            raise ValueError("Flow category chain exceeds the crawl budget")
        names.append(plain_text(node.name) or node.name)
        node = node.child
    return names


def press_snapshot(press, listing_date, warnings):
    key, retrieved = f"press:{press.id}", now()
    previous = []
    for item in press.previousPresses:
        try:
            value = bulletin_id(item.url)
            if value != key:
                previous.append(value)
        except ValueError:
            warnings.append(f"{key}: an invalid previousPresses URL was omitted")
    related, paths, formats, has_download = {}, [], [], False
    for entry in press.tables + press.statisticalTables + press.reports:
        dataset = TuikPortal.related_dataset(entry)
        if dataset is not None:
            related[dataset.id] = dataset
            paths.extend("dataflow:" + part for part in dataset.category_path)
        value = entry.get("url")
        if not isinstance(value, str):
            continue
        try:
            url = official_url(value)
        except ValueError:
            continue
        fmt = entry.get("type")
        has_download = has_download or TuikPortal.is_download(url)
        if (
            TuikPortal.is_download(url)
            and isinstance(fmt, str)
            and re.fullmatch(r"[a-z0-9]{1,20}", fmt)
        ):
            formats.append(fmt)
    common = dict(
        title=plain_text(press.title) or press.title,
        period=press.period,
        publication_date=portal_date(press.date),
        retrieved_at=retrieved,
    )
    if press.period:
        common["title"] += ", " + press.period
    bulletin = IndexDocument(
        **common,
        id=key,
        kind="bulletin",
        source_url=f"{PORTAL}/tr/press/{press.id}",
        body=plain_text(press.content),
        formats=formats,
        downloadable=has_download,
        related_ids=list(related),
        category_path=paths,
    )
    sections = []
    try:
        for metadata in press.metadatas:
            for group in metadata.get("data", []):
                for item in group.get("detail", []):
                    content = plain_text(item.get("content"))
                    if content:
                        sections.append((plain_text(item.get("title")) or "") + "\n" + content)
    except (TypeError, AttributeError) as exc:
        raise SourceError(
            "malformed_response", "Invalid bulletin metadata", f"{PORTAL}/api/tr/press/{press.id}"
        ) from exc
    document = None
    if sections:
        document = IndexDocument(
            **common,
            id=key + ":metadata",
            kind="metadata",
            parent_id=key,
            source_url=bulletin.source_url + "/metadata",
            body="\n\n".join(sections),
            related_ids=[key],
        )
        bulletin.related_ids.append(document.id)
    return PressSnapshot(
        bulletin=bulletin,
        metadata=document,
        previous_ids=list(dict.fromkeys(previous)),
        listing_date=listing_date,
    )


def groups(records):
    parents = {}

    def find(key):
        parents.setdefault(key, key)
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    for key, record in records.items():
        for previous in record.previous_ids:
            a, b = find(key), find(previous)
            parents[max(a, b)] = min(a, b)
        find(key)
    components = {}
    for key in parents:
        components.setdefault(find(key), []).append(key)
    return {key: members for members in components.values() for key in members}


class MetadataCrawler:
    def __init__(self, source: CrawlSource, state: CrawlState | None = None):
        self.source, self.state = source, state or CrawlState()
        self.next_state = None
        self.report = {}

    async def documents(self, *, full=False, max_bulletins=None, page_limit=None):
        if max_bulletins is not None and (type(max_bulletins) is not int or max_bulletins < 1):
            raise ValueError("max_bulletins must be a positive integer")
        if page_limit is not None and (type(page_limit) is not int or not 1 <= page_limit <= 1000):
            raise ValueError("page_limit must be between 1 and 1000")
        self.next_state = None
        report = self.report = {
            "started_at": now().isoformat(),
            "mode": "full" if full else "incremental",
            "status": "running",
            "warnings": [],
            "pages": 0,
            "upstream_total": None,
            "fetched_bulletins": 0,
            "reused_bulletins": 0,
            "counts": {},
            "partial": True,
        }
        try:
            flows = await self.source.flows()
            flows_retrieved_at = now()
            if len(flows) > MAX_RECORDS or len({f.id for f in flows}) != len(flows):
                raise ValueError("Duplicate dataflow IDs or catalog budget exceeded")
            try:
                memberships = theme_memberships(await self.source.themes())
                report["theme_tree"] = "ok"
                report["theme_tree_retrieved_at"] = now().isoformat()
            except SourceError as exc:
                memberships = {}
                report["theme_tree"] = exc.code
                report["warnings"].append(
                    f"Theme tree unavailable ({exc.code}); "
                    "only confirmed linked-flow categories are used."
                )
            listing, signatures, complete, page_size, listing_rows = {}, set(), False, None, 0
            for page in range(1, 1001):
                batch = await self.source.listing(page)
                report["pages"], report["upstream_total"] = page, batch.total
                signature = tuple(item.url for item in batch.data)
                if signature and signature in signatures:
                    raise ValueError("Upstream repeated a listing page; enumeration is incomplete")
                signatures.add(signature)
                listing_rows += len(batch.data)
                if page_size is None and batch.data:
                    page_size = len(batch.data)
                for item in batch.data:
                    if item.type != 1:
                        raise ValueError("Bulletin listing returned a different resource kind")
                    listing[bulletin_id(item.url)] = item.date
                    if len(listing) > MAX_RECORDS:
                        raise ValueError("Bulletin listing exceeds the crawl budget")
                if not batch.data or page * page_size >= batch.total:
                    complete = True
                    break
                if page_limit is not None and page >= page_limit:
                    break
            if not complete and page_limit is None:
                raise ValueError("Bulletin listing exceeded 1000 pages")
            report["listed_bulletins"] = len(listing)
            report["listing_rows"] = listing_rows
            report["duplicate_listing_rows"] = listing_rows - len(listing)
            if complete and (
                listing_rows != report["upstream_total"] or len(listing) != report["upstream_total"]
            ):
                raise SourceError(
                    "incomplete_listing",
                    "Listing row or unique bulletin count differs from the upstream total; "
                    "retry full enumeration before committing state",
                    PORTAL + "/api/tr/data/search",
                )
            selected = list(listing)[:max_bulletins]
            limited = not complete or len(selected) < len(listing)
            report["partial"] = limited
            records = {}
            for key in selected:
                cached = self.state.records.get(key)
                if not full and cached is not None and cached.listing_date == listing[key]:
                    snapshot = cached.model_copy(deep=True)
                    report["reused_bulletins"] += 1
                else:
                    press = await self.source.press(key.split(":")[1])
                    snapshot = press_snapshot(press, listing[key], report["warnings"])
                    report["fetched_bulletins"] += 1
                records[key] = snapshot
            components = groups(records)
            by_flow = {"flow:" + flow.id: flow for flow in flows}
            reverse = {}
            for key, snapshot in records.items():
                themes, subthemes, paths = [], [], []
                for member in components[key]:
                    for path in memberships.get(member, []):
                        if path:
                            themes.append(path[0][1])
                            subthemes.extend(name for _, name in path[1:])
                            paths.extend("theme-tree:" + node_id for node_id, _ in path)
                for related in snapshot.bulletin.related_ids:
                    flow = by_flow.get(related)
                    if flow:
                        names = category_names(flow)
                        themes.extend(names[:1])
                        subthemes.extend(names[1:])
                        reverse.setdefault(related, []).append(key)
                # Refresh classification; unconfirmed cached values cannot become permanent.
                paths.extend(
                    p for p in snapshot.bulletin.category_path if p.startswith("dataflow:")
                )
                series = "series:" + min(
                    components[key], key=lambda value: int(value.split(":")[1])
                )
                for attribute in ("bulletin", "metadata"):
                    doc = getattr(snapshot, attribute)
                    if doc:
                        values = doc.model_dump() | {
                            "theme": list(dict.fromkeys(themes)),
                            "subtheme": list(dict.fromkeys(subthemes)),
                            "category_path": list(dict.fromkeys(paths)),
                            "series_id": series,
                            "indexed_at": now(),
                        }
                        setattr(snapshot, attribute, IndexDocument.model_validate(values))
            all_documents = []
            for flow in flows:
                resource = TuikPortal.flow_resource(flow)
                names = category_names(flow)
                all_documents.append(
                    IndexDocument(
                        id=resource.id,
                        kind="dataset",
                        title=resource.title,
                        description=resource.description,
                        body=plain_text("\n".join(flow.footnotes)),
                        theme=names[:1],
                        subtheme=names[1:],
                        period=flow.period,
                        updated_at=portal_date(flow.updatedAt),
                        related_ids=reverse.get(resource.id, []),
                        formats=resource.available_formats,
                        downloadable=flow.downloadable,
                        source_url=resource.source_url,
                        retrieved_at=flows_retrieved_at,
                    )
                )
            for snapshot in records.values():
                all_documents.append(snapshot.bulletin)
                if snapshot.metadata:
                    all_documents.append(snapshot.metadata)
            known = sum(bool(record.bulletin.theme) for record in records.values())
            report["theme_coverage"] = {
                "bulletins_with_theme": known,
                "bulletins": len(records),
                "ratio": known / len(records) if records else None,
            }
            report["missing_from_listing"] = (
                sorted(set(self.state.records) - set(listing)) if complete else []
            )
            if report["missing_from_listing"]:
                report["warnings"].append(
                    f"{len(report['missing_from_listing'])} previously cached bulletin(s) "
                    "are absent from the complete listing; inspect before applying index removals."
                )
            candidate = CrawlState(records=records) if not limited else None
            if candidate is not None:
                await asyncio.to_thread(state_bytes, candidate)
            counts = Counter()
            for document in all_documents:
                counts[document.kind] += 1
                yield document
            report["counts"] = dict(counts)
            # A consumer commits only after all documents have been accepted downstream.
            self.next_state = candidate
            report["status"] = "ok"
        except Exception as exc:
            report["status"] = "error"
            report["error"] = error_details(exc)
            raise
        finally:
            if report["status"] == "running":
                report["status"] = "incomplete"
            report["finished_at"] = now().isoformat()


def state_bytes(state):
    payload = state.model_dump_json().encode()
    if len(payload) > MAX_STATE_BYTES:
        raise ValueError("Crawl state exceeds its byte budget")
    return payload
