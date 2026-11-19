"""Interchangeable search backends behind one protocol (#17).

`OpenSearchBackend` queries the indexed metadata alias over REST, lexically or, when
the index holds vectors from the configured model, as a hybrid lexical + k-NN query
fused by weighted reciprocal rank (#21, ADR 0005). `LiteralBackend`
is the pre-index dataset matching and covers dataflows only; `PortalBackend` is the
live portal search. Callers choose the fallback; a backend never falls back itself.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import date
from typing import Literal, Protocol

from pydantic import BaseModel, Field, model_validator

from tuik_mcp.models.dataset import TuikResource
from tuik_mcp.models.search import (
    SearchFacet,
    SearchFacets,
    SearchResponse,
    SeriesGroup,
    SeriesRelease,
)
from tuik_mcp.utils.normalization import turkish_casefold

from .documents import CONTENT_KINDS
from .embeddings import Embedder, EmbeddingUnavailable
from .indexer import IndexerError, OpenSearchStore

Kind = Literal["dataset", "bulletin", "metadata", "table", "document_chunk"]
# Exact (synonym-aware) matching on every field; typo tolerance only as a weaker clause.
EXACT_FIELDS = ["title^4", "title.ascii", "description^2", "body", "theme"]
FUZZY_FIELDS = ["title.ascii", "description", "body"]
HIGHLIGHT_FIELDS = ["title", "title.ascii", "description", "body"]
# Highlighting analyzes at most this many characters of a long bulletin body.
HIGHLIGHT_MAX_OFFSET = 100_000
MAX_OFFSET = 1000
# Hybrid fusion (#21): each subquery contributes its top HYBRID_DEPTH documents, and a
# document scores sum(weight / (HYBRID_RANK_CONSTANT + rank)) over the lists it is in.
# Weights are lexical, semantic. HYBRID_DEPTH bounds offset + limit for hybrid ranking.
# Equal weights had the best reviewed nDCG@10/MRR of the two pre-registered settings
# (docs/verification/issue-21-hybrid-search-2026-11-05.md).
HYBRID_DEPTH = 50
HYBRID_RANK_CONSTANT = 60
HYBRID_WEIGHTS = (0.5, 0.5)
# k-NN always returns its nearest neighbours, and e5 similarities do not separate an
# unrelated query from a real one (gibberish and "issizlik orani" both score ~0.92),
# so no score threshold is used. Instead, the hybrid request counts the documents the
# lexical query matches; with none, semantic neighbours alone are not returned.
LEXICAL_MATCHES_AGG = "lexical_matches"
NO_LEXICAL_MATCH = (
    "No indexed document contains the query's words or a close spelling; semantic "
    "neighbours alone are not returned. Try other Turkish terms or live=true."
)
# Kinds whose filtered searches were neither measured nor vector-indexed stay lexical:
# datasets ranked lower in hybrid top tens, and file content has no vectors.
LEXICAL_ONLY_KINDS = frozenset(("dataset", *CONTENT_KINDS))
# Bare curated acronyms have lexical synonyms but unreliable E5 neighbours (#79).
# These are the equivalence-rule aliases in synonyms.txt, with Turkish/ASCII forms.
LEXICAL_ABBREVIATIONS = frozenset(
    ("tufe", "ufe", "yi-ufe", "yi ufe", "gsyh", "hia", "adnks", "nace")
)
ABBREVIATION_RANKING = (
    "Known statistical abbreviation: results use synonym-aware lexical ranking "
    "because semantic neighbours can be unrelated."
)
# How long a served index's vector model is trusted before the alias is rechecked.
META_TTL_SECONDS = 60
# Series grouping (#78): releases of one bulletin series (a bulletin and its metadata
# share `series_id`) are folded into their best-ranked document. Grouping regroups the
# first GROUP_WINDOW documents of the normal ranking; hybrid fusion then also uses this
# depth, so one large series (up to 94 documents) cannot exhaust the window. OpenSearch's
# native collapse is not used: with hybrid queries it collapses each subquery before
# fusion and splits a series whose best lexical and semantic members differ.
GROUP_WINDOW = 200
# Grouped pages cover offset + limit <= MAX_GROUPED groups.
MAX_GROUPED = 50
SERIES_KINDS = frozenset(("bulletin", "metadata"))
SERIES_AGG = "series"


class SearchFilters(BaseModel):
    # Empty means the metadata kinds; file content (#22) is searched only when named.
    kinds: list[Kind] = Field(default_factory=list, max_length=5)
    # Exact stored theme names; any of them matches.
    themes: list[str] | None = Field(default=None, max_length=50)
    # Exact series IDs (#78), e.g. to list every release of one bulletin series.
    series_ids: list[str] | None = Field(default=None, max_length=10)
    downloadable: bool | None = None
    published_after: date | None = None
    published_before: date | None = None

    @model_validator(mode="after")
    def ordered_dates(self):
        if self.published_after and self.published_before:
            if self.published_after > self.published_before:
                raise ValueError("published_after must not be later than published_before")
        return self


class SearchHit(BaseModel):
    id: str
    kind: str
    title: str
    description: str | None = None
    score: float | None = None
    source_url: str
    # File content: the file ID to open and where in it the match is.
    parent_id: str | None = None
    page: int | None = None
    sheet: str | None = None
    theme: list[str] = Field(default_factory=list)
    publication_date: str | None = None
    updated_at: str | None = None
    downloadable: bool | None = None
    formats: list[str] = Field(default_factory=list)
    highlights: dict[str, list[str]] = Field(default_factory=dict)
    retrieved_at: str | None = None
    indexed_at: str | None = None
    # Bulletins and their metadata: the series key; grouped results also carry `series`.
    series_id: str | None = None
    series: SeriesGroup | None = None


class SearchHits(BaseModel):
    backend: Literal["opensearch", "literal", "portal"]
    # How an OpenSearch result list was ranked; None for the other backends.
    ranking: Literal["lexical", "hybrid"] | None = None
    hits: list[SearchHit] = Field(default_factory=list)
    total: int | None = None
    limit: int
    offset: int = 0
    warnings: list[str] = Field(default_factory=list)
    facets: SearchFacets | None = None
    indexed_at: str | None = None


class BackendUnavailable(Exception):
    """The backend cannot answer now; the caller may fall back."""


class SearchBackend(Protocol):
    async def search(
        self, query: str, filters: SearchFilters, limit: int, offset: int = 0, group: bool = False
    ) -> SearchHits: ...


def check_page(limit: int, offset: int, max_limit: int = 50) -> None:
    if type(limit) is not int or not 1 <= limit <= max_limit:
        raise ValueError(f"limit must be an integer between 1 and {max_limit}")
    if type(offset) is not int or not 0 <= offset <= MAX_OFFSET:
        raise ValueError(f"offset must be an integer between 0 and {MAX_OFFSET}")


def match_clauses(query: str) -> list[dict]:
    """Exact (synonym-aware) matching, plus typo tolerance as a weaker clause."""
    return [
        {"multi_match": {"query": query, "type": "best_fields", "fields": EXACT_FIELDS}},
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
    ]


def highlight_settings() -> dict:
    return {
        "fields": {field: {} for field in HIGHLIGHT_FIELDS},
        "max_analyzer_offset": HIGHLIGHT_MAX_OFFSET,
    }


def build_query(query: str, filters: SearchFilters, limit: int, offset: int = 0) -> dict:
    """The OpenSearch request body; analyzers come from the index mapping.

    `limit` may reach GROUP_WINDOW: grouped searches fetch a window of documents.
    """
    check_page(limit, offset, GROUP_WINDOW)
    clauses = []
    if filters.kinds:
        clauses.append({"terms": {"kind": filters.kinds}})
    else:
        clauses.append({"bool": {"must_not": {"terms": {"kind": list(CONTENT_KINDS)}}}})
    if filters.themes is not None:
        clauses.append({"terms": {"theme": filters.themes}})
    if filters.series_ids is not None:
        clauses.append({"terms": {"series_id": filters.series_ids}})
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
                "should": match_clauses(query),
                "minimum_should_match": 1,
                "filter": clauses,
            }
        },
        "from": offset,
        "size": limit,
        "track_total_hits": True,
        "aggs": {
            "kind": {"terms": {"field": "kind", "size": 5}},
            "theme": {"terms": {"field": "theme", "size": 50}},
            "indexed_at": {"max": {"field": "indexed_at"}},
        },
        "_source": {"excludes": ["body", "description", "embedding"]},
        "highlight": highlight_settings(),
    }


def build_highlight_query(query: str, ids: list[str]) -> dict:
    """The lexical query's highlights for documents another request already ranked.

    Inside a hybrid request the highlighter cannot read the query's terms and re-runs
    the query per document: about 20 ms per hit on the 3,440-document index, against
    about 1 ms here. Documents without a lexical match come back without highlights.
    """
    return {
        "query": {"bool": {"filter": [{"ids": {"values": ids}}], "should": match_clauses(query)}},
        "size": len(ids),
        "track_total_hits": False,
        "_source": False,
        "highlight": highlight_settings(),
    }


def build_followup_query(query: str, ids: list[str], series_ids: list[str]) -> dict:
    """Highlights for grouped representatives plus their series' size and newest release.

    The series aggregation is global: it counts bulletin releases regardless of the
    query and filters, so `latest` is the series' newest indexed release.
    """
    body = build_highlight_query(query, ids)
    if series_ids:
        body["aggs"] = {
            SERIES_AGG: {
                "global": {},
                "aggs": {
                    "releases": {
                        "filter": {
                            "bool": {
                                "filter": [
                                    {"terms": {"series_id": series_ids}},
                                    {"term": {"kind": "bulletin"}},
                                ]
                            }
                        },
                        "aggs": {
                            "by_series": {
                                "terms": {"field": "series_id", "size": len(series_ids)},
                                "aggs": {
                                    "latest": {
                                        "top_hits": {
                                            "size": 1,
                                            "sort": [
                                                {
                                                    "publication_date": {
                                                        "order": "desc",
                                                        "missing": "_last",
                                                    }
                                                },
                                                {"id": "asc"},
                                            ],
                                            "_source": [
                                                "id",
                                                "title",
                                                "publication_date",
                                                "source_url",
                                            ],
                                        }
                                    }
                                },
                            }
                        },
                    }
                },
            }
        }
    return body


def build_hybrid_query(
    query: str,
    filters: SearchFilters,
    limit: int,
    offset: int,
    vector: list[float],
    *,
    weights: tuple[float, float] = HYBRID_WEIGHTS,
    depth: int = HYBRID_DEPTH,
) -> dict:
    """The lexical query plus k-NN over the same filters, fused by weighted RRF.

    The fusion pipeline travels in the request, so no cluster-side pipeline object
    has to be created or kept in sync with this code. `depth` is how many documents
    each subquery contributes; grouped searches (#78) use GROUP_WINDOW.
    """
    if offset + limit > depth:
        raise ValueError(f"Hybrid ranking covers the first {depth} results")
    body = deepcopy(build_query(query, filters, limit, offset))
    # Highlights come from build_highlight_query: they are slow inside a hybrid request.
    del body["highlight"]
    lexical = body["query"]
    semantic = {
        "knn": {
            "embedding": {
                "vector": vector,
                "k": depth,
                "filter": {"bool": {"filter": lexical["bool"]["filter"]}},
            }
        }
    }
    body["query"] = {"hybrid": {"queries": [lexical, semantic], "pagination_depth": depth}}
    # Every lexical match is in the hybrid result set, so this counts all of them.
    body["aggs"][LEXICAL_MATCHES_AGG] = {"filter": lexical}
    body["search_pipeline"] = {
        "phase_results_processors": [
            {
                "score-ranker-processor": {
                    "combination": {
                        "technique": "rrf",
                        "rank_constant": HYBRID_RANK_CONSTANT,
                        "parameters": {"weights": list(weights)},
                    }
                }
            }
        ]
    }
    return body


def semantic_scope(filters: SearchFilters) -> bool:
    """Unfiltered metadata searches (the evaluated setting) and bulletin/metadata kinds."""
    return not set(filters.kinds) & LEXICAL_ONLY_KINDS


def groupable(filters: SearchFilters) -> bool:
    """Grouping applies when bulletins or metadata can match and no series is named."""
    return filters.series_ids is None and (
        not filters.kinds or bool(SERIES_KINDS & set(filters.kinds))
    )


def series_key(source: dict) -> str:
    """The grouping key: a bulletin's or its metadata's series, else the document itself."""
    if source.get("kind") in SERIES_KINDS and source.get("series_id"):
        return source["series_id"]
    return source["id"]


def regroup(items: list[dict]) -> list[tuple[dict, int]]:
    """Keep each group's first (best-ranked) document and count the ones folded into it."""
    groups: dict[str, list] = {}
    for item in items:
        key = series_key(item["_source"])
        if key in groups:
            groups[key][1] += 1
        else:
            groups[key] = [item, 0]
    return [(item, folded) for item, folded in groups.values()]


def is_statistical_abbreviation(query: str) -> bool:
    """Recognize whole curated aliases, keeping contextual queries on the hybrid path."""
    folded = turkish_casefold(query).translate(str.maketrans("çğıöşü", "cgiosu"))
    return " ".join(folded.split()) in LEXICAL_ABBREVIATIONS


class OpenSearchBackend:
    def __init__(self, store: OpenSearchStore, alias: str, embedder: Embedder | None = None):
        self.store, self.alias, self.embedder = store, alias, embedder
        self._model: tuple[float, str | None] | None = None

    async def vector_model(self) -> str | None:
        """The model whose vectors the served index holds; None if lexical-only."""
        if self._model is not None and time.monotonic() - self._model[0] < META_TTL_SECONDS:
            return self._model[1]
        meta = await self.store.meta(self.alias)
        models = {value.get("embedding_model") for value in meta.values()}
        model = models.pop() if len(models) == 1 else None
        self._model = (time.monotonic(), model)
        return model

    async def plan(
        self,
        query: str,
        filters: SearchFilters,
        limit: int,
        offset: int,
        *,
        depth: int = HYBRID_DEPTH,
    ) -> tuple[dict, list[str]]:
        """The request body and any reason hybrid ranking was not used."""
        lexical = build_query(query, filters, limit, offset)
        if is_statistical_abbreviation(query):
            # Match the frozen lexical evaluation, including tied scores and later pages.
            lexical["sort"] = [{"_score": "desc"}, {"id": "asc"}]
            warnings = (
                [ABBREVIATION_RANKING]
                if self.embedder is not None and semantic_scope(filters)
                else []
            )
            return lexical, warnings
        if self.embedder is None or not semantic_scope(filters):
            return lexical, []
        model = await self.vector_model()
        if model is None:
            return lexical, []
        if model != self.embedder.model:
            return lexical, [
                f"The index holds {model} vectors, not {self.embedder.model}; "
                "results use lexical ranking."
            ]
        if offset + limit > depth:
            return lexical, [
                f"Semantic ranking covers the first {depth} results; "
                "this page uses lexical ranking."
            ]
        try:
            vector = await asyncio.to_thread(self.embedder.query, query)
        except EmbeddingUnavailable as exc:
            return lexical, [
                f"Semantic ranking is unavailable ({exc}); results use lexical ranking."
            ]
        return build_hybrid_query(query, filters, limit, offset, vector, depth=depth), []

    async def search(
        self,
        query: str,
        filters: SearchFilters,
        limit: int,
        offset: int = 0,
        group: bool = False,
    ) -> SearchHits:
        """Rank documents; with `group`, fold each bulletin series into its best release."""
        check_page(limit, offset)
        notes = []
        grouped = group and groupable(filters)
        if grouped and offset + limit > MAX_GROUPED:
            grouped = False
            notes.append(
                f"Series grouping covers the first {MAX_GROUPED} results; "
                "this page lists releases separately."
            )
        # A grouped page is cut from the first GROUP_WINDOW documents of the ranking.
        fetch_limit, fetch_offset = (GROUP_WINDOW, 0) if grouped else (limit, offset)
        depth = GROUP_WINDOW if grouped else HYBRID_DEPTH
        try:
            body, warnings = await self.plan(query, filters, fetch_limit, fetch_offset, depth=depth)
        except (IndexerError, KeyError, TypeError, AttributeError) as exc:
            raise BackendUnavailable(str(exc)) from exc
        warnings = notes + warnings
        page = (limit, offset, grouped)
        if grouped:
            # Only the page's representatives are highlighted, in the follow-up request.
            body.pop("highlight", None)
        ranking = "hybrid" if "hybrid" in body["query"] else "lexical"
        if ranking == "hybrid":
            try:
                return await self.execute(query, body, page, ranking, warnings)
            except BackendUnavailable as exc:
                # A cluster without the neural-search plugin still answers lexically.
                warnings.append(f"Hybrid search failed ({exc}); results use lexical ranking.")
                body, ranking = build_query(query, filters, fetch_limit, fetch_offset), "lexical"
                if grouped:
                    body.pop("highlight", None)
        return await self.execute(query, body, page, ranking, warnings)

    async def add_highlights(
        self, query: str, items: list[dict], series_ids: list[str] | None = None
    ) -> tuple[list[str], dict | None]:
        """Fill each hit's highlight from a lexical request; hits stand without it.

        With `series_ids`, the same request also returns each series' release count
        and newest release (None when the request failed).
        """
        try:
            ids = [item["_source"]["id"] for item in items]
            body = (
                build_followup_query(query, ids, series_ids)
                if series_ids is not None
                else build_highlight_query(query, ids)
            )
            response = await self.store.query(self.alias, body)
            found = {hit["_id"]: hit.get("highlight") or {} for hit in response["hits"]["hits"]}
            series = None
            if series_ids:
                buckets = response["aggregations"][SERIES_AGG]["releases"]["by_series"]["buckets"]
                series = {}
                for bucket in buckets:
                    latest = bucket["latest"]["hits"]["hits"]
                    series[bucket["key"]] = (
                        bucket["doc_count"],
                        SeriesRelease(**latest[0]["_source"]) if latest else None,
                    )
            elif series_ids is not None:
                series = {}
        except (IndexerError, KeyError, TypeError, AttributeError, ValueError) as exc:
            message = "Highlights are unavailable"
            if series_ids:
                message = "Highlights and series details are unavailable"
            return [f"{message} ({exc})."], None
        for item in items:
            item["highlight"] = found.get(item["_source"]["id"], {})
        return [], series

    async def execute(
        self,
        query: str,
        body: dict,
        page: tuple[int, int, bool],
        ranking: str,
        warnings: list[str],
    ) -> SearchHits:
        limit, offset, grouped = page
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
            aggregations = response.get("aggregations") or {}
            if ranking == "hybrid" and aggregations[LEXICAL_MATCHES_AGG]["doc_count"] == 0:
                return SearchHits(
                    backend="opensearch",
                    ranking=ranking,
                    warnings=[*warnings, NO_LEXICAL_MATCH],
                    total=0,
                    limit=limit,
                    offset=offset,
                    facets=SearchFacets(
                        **{name: SearchFacet(counts={}) for name in ("kind", "theme")}
                    ),
                    indexed_at=aggregations.get("indexed_at", {}).get("value_as_string"),
                )
            warnings = list(warnings)
            items, folded, series = hits["hits"], {}, None
            if grouped:
                groups = regroup(items)
                if len(groups) < offset + limit and hits["total"]["value"] > len(items):
                    warnings.append(
                        f"Series grouping covers the first {len(items)} ranked documents; "
                        f"they hold {len(groups)} distinct results."
                    )
                groups = groups[offset : offset + limit]
                items = [item for item, _ in groups]
                folded = {item["_source"]["id"]: count for item, count in groups}
            if (ranking == "hybrid" or grouped) and items:
                series_ids = (
                    sorted(
                        {
                            series_key(item["_source"])
                            for item in items
                            if series_key(item["_source"]) != item["_source"]["id"]
                        }
                    )
                    if grouped
                    else None
                )
                notes, series = await self.add_highlights(query, items, series_ids)
                warnings += notes
            return SearchHits(
                backend="opensearch",
                ranking=ranking,
                warnings=warnings,
                hits=[
                    self.hit(item, folded.get(item["_source"]["id"]), series, grouped)
                    for item in items
                ],
                total=hits["total"]["value"],
                limit=limit,
                offset=offset,
                facets=self.facets(response.get("aggregations")),
                indexed_at=(response.get("aggregations") or {})
                .get("indexed_at", {})
                .get("value_as_string"),
            )
        except (IndexerError, KeyError, TypeError, ValueError, AttributeError) as exc:
            raise BackendUnavailable(str(exc)) from exc

    @staticmethod
    def facets(aggregations: dict | None) -> SearchFacets | None:
        if aggregations is None:
            return None
        return SearchFacets(
            **{
                name: SearchFacet(
                    counts={b["key"]: b["doc_count"] for b in aggregations[name]["buckets"]},
                    truncated=aggregations[name]["sum_other_doc_count"] > 0,
                    count_error_upper_bound=aggregations[name]["doc_count_error_upper_bound"],
                )
                for name in ("kind", "theme")
            }
        )

    @staticmethod
    def hit(
        item: dict,
        folded: int | None = None,
        series: dict | None = None,
        grouped: bool = False,
    ) -> SearchHit:
        source = item["_source"]
        series_id = source.get("series_id") if source["kind"] in SERIES_KINDS else None
        group = None
        if grouped and series_id:
            releases, latest = (series or {}).get(series_id, (None, None))
            if series is not None and releases is None:
                releases = 0  # The series has no bulletin release in the index.
            group = SeriesGroup(collapsed=folded or 0, releases=releases, latest=latest)
        return SearchHit(
            series_id=series_id,
            series=group,
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
            parent_id=source.get("parent_id") if source["kind"] in CONTENT_KINDS else None,
            page=source.get("page"),
            sheet=source.get("sheet"),
        )

    async def close(self) -> None:
        await self.store.close()


class LiteralBackend:
    """Every query word must occur in a dataflow's title, description or theme."""

    def __init__(self, datasets: Callable[[], Awaitable[tuple[object, list[TuikResource]]]]):
        self.datasets = datasets

    async def search(
        self, query: str, filters: SearchFilters, limit: int, offset: int = 0, group: bool = False
    ) -> SearchHits:
        # `group` is accepted for the protocol; these results carry no series.
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
        if filters.series_ids is not None:
            # Consistent with the index: dataflows belong to no bulletin series.
            warnings.append(
                "Datasets belong to no bulletin series; the series filter matched none."
            )
        elif filters.published_after or filters.published_before:
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
        self, query: str, filters: SearchFilters, limit: int, offset: int = 0, group: bool = False
    ) -> SearchHits:
        # `group` is accepted for the protocol; these results carry no series.
        check_page(limit, offset)
        if offset + limit > 50:
            raise ValueError("Portal search returns at most 50 results; reduce offset or limit")
        warnings = []
        ignored = [
            name
            for name, value in (
                ("theme", filters.themes),
                ("series", filters.series_ids),
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
                    description=result.description,
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
