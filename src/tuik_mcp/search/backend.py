"""Interchangeable search backends behind one protocol (#17).

`OpenSearchBackend` queries the indexed metadata alias over REST. `LiteralBackend`
is the pre-index dataset matching and covers dataflows only; `PortalBackend` is the
live portal search. Callers choose the fallback; a backend never falls back itself.
"""

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from tuik_mcp.models.dataset import TuikResource
from tuik_mcp.models.search import SearchResponse
from tuik_mcp.utils.normalization import turkish_casefold

from .indexer import IndexerError, OpenSearchStore

Kind = Literal["dataset", "bulletin", "metadata"]
# Exact (synonym-aware) matching on every field; typo tolerance only as a weaker clause.
EXACT_FIELDS = ["title^4", "title.ascii", "description^2", "body", "theme"]
FUZZY_FIELDS = ["title.ascii", "description", "body"]
HIGHLIGHT_FIELDS = ["title", "title.ascii", "description", "body"]
# Highlighting analyzes at most this many characters of a long bulletin body.
HIGHLIGHT_MAX_OFFSET = 100_000
MAX_OFFSET = 1000


class SearchFilters(BaseModel):
    kinds: list[Kind] = Field(default_factory=list, max_length=3)
    # Exact stored theme names; any of them matches.
    themes: list[str] | None = Field(default=None, max_length=50)
    downloadable: bool | None = None
    published_after: date | None = None
    published_before: date | None = None


class SearchHit(BaseModel):
    id: str
    kind: str
    title: str
    score: float | None = None
    source_url: str
    theme: list[str] = Field(default_factory=list)
    publication_date: str | None = None
    updated_at: str | None = None
    downloadable: bool | None = None
    formats: list[str] = Field(default_factory=list)
    highlights: dict[str, list[str]] = Field(default_factory=dict)
    retrieved_at: str | None = None
    indexed_at: str | None = None


class SearchHits(BaseModel):
    backend: Literal["opensearch", "literal", "portal"]
    hits: list[SearchHit] = Field(default_factory=list)
    total: int | None = None
    limit: int
    offset: int = 0
    warnings: list[str] = Field(default_factory=list)


class BackendUnavailable(Exception):
    """The backend cannot answer now; the caller may fall back."""


class SearchBackend(Protocol):
    async def search(
        self, query: str, filters: SearchFilters, limit: int, offset: int = 0
    ) -> SearchHits: ...


def check_page(limit: int, offset: int) -> None:
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be an integer between 1 and 50")
    if type(offset) is not int or not 0 <= offset <= MAX_OFFSET:
        raise ValueError(f"offset must be an integer between 0 and {MAX_OFFSET}")


def build_query(query: str, filters: SearchFilters, limit: int, offset: int = 0) -> dict:
    """The OpenSearch request body; analyzers come from the index mapping."""
    check_page(limit, offset)
    clauses = []
    if filters.kinds:
        clauses.append({"terms": {"kind": filters.kinds}})
    if filters.themes is not None:
        clauses.append({"terms": {"theme": filters.themes}})
    if filters.downloadable is not None:
        clauses.append({"term": {"downloadable": filters.downloadable}})
    if filters.published_after or filters.published_before:
        bounds = {}
        if filters.published_after:
            bounds["gte"] = filters.published_after.isoformat()
        if filters.published_before:
            bounds["lte"] = filters.published_before.isoformat()
        clauses.append({"range": {"publication_date": bounds}})
    return {
        "query": {
            "bool": {
                "should": [
                    {
                        "multi_match": {
                            "query": query,
                            "type": "best_fields",
                            "fields": EXACT_FIELDS,
                        }
                    },
                    {
                        "multi_match": {
                            "query": query,
                            "type": "best_fields",
                            "fields": FUZZY_FIELDS,
                            "fuzziness": "AUTO",
                            # A different first letter is a different word: "tufe" ≠ "ufe".
                            "prefix_length": 1,
                            "max_expansions": 50,
                            "boost": 0.5,
                        }
                    },
                ],
                "minimum_should_match": 1,
                "filter": clauses,
            }
        },
        "from": offset,
        "size": limit,
        "track_total_hits": True,
        "_source": {"excludes": ["body", "description"]},
        "highlight": {
            "fields": {field: {} for field in HIGHLIGHT_FIELDS},
            "max_analyzer_offset": HIGHLIGHT_MAX_OFFSET,
        },
    }


class OpenSearchBackend:
    def __init__(self, store: OpenSearchStore, alias: str):
        self.store, self.alias = store, alias

    async def search(
        self, query: str, filters: SearchFilters, limit: int, offset: int = 0
    ) -> SearchHits:
        body = build_query(query, filters, limit, offset)
        try:
            response = await self.store.query(self.alias, body)
            # HTTP 200 does not mean complete: never report a partial search as no matches.
            if response.get("timed_out"):
                raise BackendUnavailable("the search timed out")
            shards = response.get("_shards") or {}
            if shards.get("failed"):
                raise BackendUnavailable(
                    f"{shards['failed']} of {shards.get('total')} shards failed"
                )
            hits = response["hits"]
            if hits["total"].get("relation", "eq") != "eq":
                raise BackendUnavailable("the search returned only a lower-bound total")
            return SearchHits(
                backend="opensearch",
                hits=[self.hit(item) for item in hits["hits"]],
                total=hits["total"]["value"],
                limit=limit,
                offset=offset,
            )
        except (IndexerError, KeyError, TypeError, ValueError) as exc:
            raise BackendUnavailable(str(exc)) from exc

    @staticmethod
    def hit(item: dict) -> SearchHit:
        source = item["_source"]
        return SearchHit(
            id=source["id"],
            kind=source["kind"],
            title=source["title"],
            score=item.get("_score"),
            source_url=source["source_url"],
            theme=source.get("theme") or [],
            publication_date=source.get("publication_date"),
            updated_at=source.get("updated_at"),
            downloadable=source.get("downloadable"),
            formats=source.get("formats") or [],
            highlights=item.get("highlight") or {},
            retrieved_at=source.get("retrieved_at"),
            indexed_at=source.get("indexed_at"),
        )

    async def close(self) -> None:
        await self.store.close()


class LiteralBackend:
    """Every query word must occur in a dataflow's title, description or theme."""

    def __init__(self, datasets: Callable[[], Awaitable[tuple[object, list[TuikResource]]]]):
        self.datasets = datasets

    async def search(
        self, query: str, filters: SearchFilters, limit: int, offset: int = 0
    ) -> SearchHits:
        check_page(limit, offset)
        if filters.kinds and "dataset" not in filters.kinds:
            raise ValueError("Literal search covers datasets only")
        warnings = []
        _, datasets = await self.datasets()
        normalized = turkish_casefold(query)
        terms = normalized.split()
        themes = (
            {turkish_casefold(theme) for theme in filters.themes}
            if filters.themes is not None
            else None
        )
        matches = []
        if filters.published_after or filters.published_before:
            # Consistent with the index: dataflows carry no publication date.
            warnings.append("Datasets have no publication date; the date range matched none.")
        else:
            for dataset in datasets:
                downloadable = "csv" in dataset.available_formats
                if filters.downloadable is not None and downloadable != filters.downloadable:
                    continue
                if themes is not None and turkish_casefold(dataset.theme or "") not in themes:
                    continue
                text = turkish_casefold(
                    " ".join([dataset.title, dataset.description or "", dataset.theme or ""])
                )
                if all(term in text for term in terms):
                    matches.append(dataset)

        def rank(dataset: TuikResource):
            title = turkish_casefold(dataset.title)
            return (
                title != normalized,
                normalized not in title,
                -sum(term in title for term in terms),
                title,
                dataset.id,
            )

        matches.sort(key=rank)
        return SearchHits(
            backend="literal",
            hits=[
                SearchHit(
                    id=dataset.id,
                    kind="dataset",
                    title=dataset.title,
                    source_url=dataset.source_url,
                    theme=[dataset.theme] if dataset.theme else [],
                    updated_at=dataset.updated_at,
                    downloadable="csv" in dataset.available_formats,
                    formats=dataset.available_formats,
                    retrieved_at=dataset.retrieved_at.isoformat(),
                )
                for dataset in matches[offset : offset + limit]
            ],
            total=len(matches),
            limit=limit,
            offset=offset,
            warnings=warnings,
        )


class PortalBackend:
    """Live portal search: up to 50 relevance-ranked results, kind filter only."""

    def __init__(self, search: Callable[[str, int], Awaitable[SearchResponse]]):
        self.portal_search = search

    async def search(
        self, query: str, filters: SearchFilters, limit: int, offset: int = 0
    ) -> SearchHits:
        check_page(limit, offset)
        if offset + limit > 50:
            raise ValueError("Portal search returns at most 50 results; reduce offset or limit")
        warnings = []
        ignored = [
            name
            for name, value in (
                ("theme", filters.themes),
                ("downloadable", filters.downloadable),
                ("published_after", filters.published_after),
                ("published_before", filters.published_before),
            )
            if value is not None
        ]
        if ignored:
            warnings.append(f"Portal search cannot apply these filters: {', '.join(ignored)}.")
        response = await self.portal_search(query, 50 if filters.kinds else offset + limit)
        # Portal resource types share the index's kind names.
        types = set(filters.kinds) or None
        results = [r for r in response.results if types is None or r.resource_type in types]
        return SearchHits(
            backend="portal",
            hits=[
                SearchHit(
                    id=result.id,
                    kind=result.resource_type,
                    title=result.title,
                    source_url=result.source_url,
                    theme=[result.theme] if result.theme else [],
                    publication_date=result.publication_date,
                    retrieved_at=result.retrieved_at.isoformat(),
                )
                for result in results[offset : offset + limit]
            ],
            total=response.total if types is None else None,
            limit=limit,
            offset=offset,
            warnings=warnings + response.warnings,
        )
