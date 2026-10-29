"""Write validated metadata documents to OpenSearch behind a versioned alias (#16).

Uses the cluster REST API through httpx; the optional opensearch-py client is not
imported. One indexer writes to an alias at a time.
"""

import json
import re
from collections import Counter
from collections.abc import Iterable
from datetime import datetime

import httpx

from .documents import IndexDocument
from .schema import SCHEMA_VERSION, index_definition, physical_index_name

BULK_MAX_DOCUMENTS = 500
BULK_MAX_BYTES = 5 * 1024 * 1024
MGET_BATCH = 500
MAX_REPORTED_IDS = 50


class IndexerError(Exception):
    """A cluster operation failed or changing the index would be unsafe."""

    def __init__(self, message: str, failures: list[dict] | None = None):
        super().__init__(message)
        self.failures = failures or []


class OpenSearchStore:
    """The REST operations the indexer needs; IDs travel in bodies, never in paths."""

    def __init__(self, url: str, *, transport=None, timeout: float = 60):
        # A local development cluster: proxy environment variables do not apply.
        self._client = httpx.AsyncClient(
            base_url=url.rstrip("/") + "/", timeout=timeout, trust_env=False, transport=transport
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, *, ok=(200,), **kwargs) -> httpx.Response:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise IndexerError(f"OpenSearch {method} {path} failed: {exc}") from exc
        if response.status_code not in ok:
            raise IndexerError(
                f"OpenSearch {method} {path} returned HTTP {response.status_code}: "
                + response.text[:500]
            )
        return response

    async def exists(self, name: str) -> bool:
        response = await self._request("HEAD", name, ok=(200, 404))
        return response.status_code == 200

    async def alias_targets(self, alias: str) -> list[str]:
        response = await self._request("GET", f"_alias/{alias}", ok=(200, 404))
        return sorted(response.json()) if response.status_code == 200 else []

    async def versions(self, alias: str) -> dict[str, int]:
        response = await self._request(
            "GET",
            f"_cat/indices/{alias}-v*",
            params={"format": "json", "h": "index", "expand_wildcards": "all"},
        )
        pattern = re.compile(re.escape(alias) + r"-v([1-9][0-9]*)")
        versions = {}
        for row in response.json():
            if match := pattern.fullmatch(row["index"]):
                versions[row["index"]] = int(match[1])
        return versions

    async def schema_version(self, index: str) -> int | None:
        body = (await self._request("GET", f"{index}/_mapping")).json()
        return body[index]["mappings"].get("_meta", {}).get("schema_version")

    async def create(self, name: str) -> None:
        await self._request("PUT", name, json=index_definition())

    async def delete(self, name: str) -> None:
        await self._request("DELETE", name, ok=(200, 404))

    async def refresh(self, index: str) -> None:
        await self._request("POST", f"{index}/_refresh")

    async def count(self, index: str) -> int:
        return (await self._request("GET", f"{index}/_count")).json()["count"]

    async def swap(self, alias: str, index: str, previous: list[str]) -> None:
        # One request: readers see either the previous indices or the new one.
        actions = [
            {"remove": {"index": old, "alias": alias, "must_exist": True}} for old in previous
        ]
        actions.append({"add": {"index": index, "alias": alias}})
        await self._request("POST", "_aliases", json={"actions": actions})

    async def sources(self, index: str, ids: list[str]) -> dict[str, dict]:
        body = (await self._request("POST", f"{index}/_mget", json={"ids": ids})).json()
        return {doc["_id"]: doc["_source"] for doc in body["docs"] if doc.get("found")}

    async def ids(self, index: str) -> set[str]:
        found, after = set(), None
        while True:
            query = {"size": 1000, "_source": False, "sort": [{"id": "asc"}]}
            if after is not None:
                query["search_after"] = after
            hits = (await self._request("POST", f"{index}/_search", json=query)).json()["hits"][
                "hits"
            ]
            if not hits:
                return found
            found.update(hit["_id"] for hit in hits)
            after = hits[-1]["sort"]

    async def bulk(self, index: str, payloads: list[dict]) -> list[dict]:
        """Index documents by ID; returns per-document failures."""
        failures, lines, size = [], [], 0
        for payload in payloads:
            action = json.dumps({"index": {"_index": index, "_id": payload["id"]}})
            source = json.dumps(payload, ensure_ascii=False)
            entry = (action + "\n" + source + "\n").encode()
            if lines and (len(lines) >= BULK_MAX_DOCUMENTS or size + len(entry) > BULK_MAX_BYTES):
                failures += await self._bulk_request(lines)
                lines, size = [], 0
            lines.append(entry)
            size += len(entry)
        if lines:
            failures += await self._bulk_request(lines)
        return failures

    async def _bulk_request(self, lines: list[bytes]) -> list[dict]:
        response = await self._request(
            "POST",
            "_bulk",
            content=b"".join(lines),
            headers={"content-type": "application/x-ndjson"},
        )
        failures = []
        for item in response.json()["items"]:
            result = item["index"]
            if result.get("error") or result.get("status", 500) >= 300:
                error = result.get("error") or {}
                failures.append(
                    {
                        "id": result.get("_id"),
                        "status": result.get("status"),
                        "type": error.get("type"),
                        "reason": (error.get("reason") or "")[:500],
                    }
                )
        return failures


def prepare(documents: Iterable[IndexDocument], indexed_at: datetime) -> tuple[list[dict], Counter]:
    """Serialize one snapshot with a shared indexed_at; duplicate IDs are broken input."""
    payloads, kinds, seen = [], Counter(), set()
    for document in documents:
        if document.id in seen:
            raise IndexerError(f"Duplicate document ID in crawl output: {document.id}")
        seen.add(document.id)
        kinds[document.kind] += 1
        payloads.append(
            document.model_copy(update={"indexed_at": indexed_at}).model_dump(mode="json")
        )
    if not payloads:
        raise IndexerError("The crawl produced no documents; nothing was indexed")
    return payloads, kinds


def bounded(items: list) -> list:
    return items[:MAX_REPORTED_IDS]


async def build_target(store: OpenSearchStore, alias: str) -> tuple[list[str], str]:
    """The indices the alias serves now and the next physical index name."""
    previous = await store.alias_targets(alias)
    if not previous and await store.exists(alias):
        raise IndexerError(f"'{alias}' is a concrete index, not an alias; remove or rename it")
    versions = await store.versions(alias)
    return previous, physical_index_name(alias, max(versions.values(), default=0) + 1)


async def served_index(store: OpenSearchStore, alias: str) -> str:
    """The single index an incremental update may write, with the current schema."""
    targets = await store.alias_targets(alias)
    if not targets:
        raise IndexerError(f"Alias '{alias}' does not exist; run a full build first")
    if len(targets) > 1:
        raise IndexerError(f"Alias '{alias}' serves several indices: {targets}")
    (index,) = targets
    version = await store.schema_version(index)
    if version != SCHEMA_VERSION:
        raise IndexerError(
            f"{index} has schema version {version}, this indexer writes {SCHEMA_VERSION}; "
            "run a full build"
        )
    return index


async def rebuild(
    store: OpenSearchStore, alias: str, payloads: list[dict], *, dry_run: bool = False
) -> dict:
    """Load a new physical index, verify it, then atomically point the alias at it."""
    previous, index = await build_target(store, alias)
    result = {"index": index, "previous_indices": previous, "documents": len(payloads)}
    if dry_run:
        return result
    await store.create(index)
    try:
        failures = await store.bulk(index, payloads)
        if failures:
            raise IndexerError(
                f"{len(failures)} document(s) were rejected; the alias was not changed", failures
            )
        await store.refresh(index)
        if (count := await store.count(index)) != len(payloads):
            raise IndexerError(f"{index} holds {count} documents, expected {len(payloads)}")
        await store.swap(alias, index, previous)
    except BaseException:
        # Remove the unused build, but never an index the alias already serves.
        try:
            if index not in await store.alias_targets(alias):
                await store.delete(index)
        except Exception:
            pass  # Keep the original failure; an orphan build is harmless.
        raise
    result["written"] = len(payloads)
    return result


def comparable(payload: dict) -> dict:
    return {key: value for key, value in payload.items() if key != "indexed_at"}


async def update(
    store: OpenSearchStore, alias: str, payloads: list[dict], *, dry_run: bool = False
) -> dict:
    """Upsert new or changed documents into the index the alias serves."""
    index = await served_index(store, alias)
    existing = {}
    for start in range(0, len(payloads), MGET_BATCH):
        batch = [payload["id"] for payload in payloads[start : start + MGET_BATCH]]
        existing |= await store.sources(index, batch)
    created, changed, unchanged = [], [], 0
    for payload in payloads:
        old = existing.get(payload["id"])
        if old is None:
            created.append(payload)
        elif comparable(old) != comparable(payload):
            changed.append(payload)
        else:
            unchanged += 1
    # Absent IDs are reported, not deleted: a full build is the removal path.
    stale = sorted(await store.ids(index) - {payload["id"] for payload in payloads})
    result = {
        "index": index,
        "documents": len(payloads),
        "created": len(created),
        "updated": len(changed),
        "unchanged": unchanged,
        "stale_count": len(stale),
        "stale_ids": bounded(stale),
    }
    if dry_run:
        return result
    failures = await store.bulk(index, created + changed)
    await store.refresh(index)
    if failures:
        raise IndexerError(f"{len(failures)} document(s) were rejected", failures)
    result["written"] = len(created) + len(changed)
    return result
