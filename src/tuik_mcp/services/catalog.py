import asyncio
from datetime import datetime
from urllib.parse import urlencode

from tuik_mcp.cache import Cache, MemoryTTLCache
from tuik_mcp.clients.base import HttpClient, SourceError
from tuik_mcp.clients.classification import ClassificationClient
from tuik_mcp.clients.tuik_portal import TuikPortal, normalize_result
from tuik_mcp.config import CLASSIFICATION, PORTAL
from tuik_mcp.models.common import SearchInput, now
from tuik_mcp.models.dataset import (
    DataResponse,
    DatasetSearchInput,
    DatasetSearchResponse,
    DownloadLink,
    TuikResource,
)
from tuik_mcp.models.search import SearchResponse, ThemesResponse
from tuik_mcp.utils.normalization import turkish_casefold, validate_id


class CatalogService:
    def __init__(self, http: HttpClient, cache: Cache | None = None):
        self.portal = TuikPortal(http)
        self.classifications = ClassificationClient(http)
        self.cache = cache if cache is not None else MemoryTTLCache()
        self._dataset_lock = asyncio.Lock()

    def remember(self, result):
        self.cache.set("record:" + result.id, result, 3600)

    async def _datasets(self) -> tuple[datetime, list[TuikResource]]:
        # Share one catalog fetch across discovery/detail and concurrent tool calls.
        async with self._dataset_lock:
            cached = self.cache.get("datasets")
            if cached is not None:
                return cached
            flows = await self.portal.flows()
            retrieved_at = now()
            resources = [self.portal.flow_resource(flow) for flow in flows]
            for resource in resources:
                resource.retrieved_at = retrieved_at
            result = (retrieved_at, resources)
            self.cache.set("datasets", result, 3600)
            return result

    async def search_datasets(
        self,
        query: str,
        limit: int = 10,
        downloadable_only: bool = True,
        theme: str | None = None,
    ) -> DatasetSearchResponse:
        args = DatasetSearchInput(
            query=query,
            limit=limit,
            downloadable_only=downloadable_only,
            theme=theme,
        )
        retrieved_at, datasets = await self._datasets()
        normalized_query = turkish_casefold(args.query)
        terms = normalized_query.split()
        normalized_theme = turkish_casefold(args.theme) if args.theme is not None else None
        matches = []
        for dataset in datasets:
            if args.downloadable_only and "csv" not in dataset.available_formats:
                continue
            if (
                normalized_theme is not None
                and turkish_casefold(dataset.theme or "") != normalized_theme
            ):
                continue
            text = turkish_casefold(
                " ".join(
                    [
                        dataset.title,
                        dataset.description or "",
                        dataset.theme or "",
                    ]
                )
            )
            if all(term in text for term in terms):
                matches.append(dataset)

        def rank(dataset: TuikResource):
            title = turkish_casefold(dataset.title)
            return (
                title != normalized_query,
                normalized_query not in title,
                -sum(term in title for term in terms),
                title,
                dataset.id,
            )

        matches.sort(key=rank)
        return DatasetSearchResponse(
            query=args.query,
            downloadable_only=args.downloadable_only,
            theme=args.theme,
            results=matches[: args.limit],
            total=len(matches),
            truncated=len(matches) > args.limit,
            source_url=f"{PORTAL}/api/tr/dataflows",
            retrieved_at=retrieved_at,
        )

    async def search(self, query: str, limit: int = 10) -> SearchResponse:
        args = SearchInput(query=query, limit=limit)
        key = f"search:{turkish_casefold(args.query)}:{args.limit}"
        cached = self.cache.get(key)
        if cached is not None:
            for result in cached.results:
                self.remember(result)
            return cached
        response = SearchResponse(
            query=args.query, source_url=f"{PORTAL}/tr/search?" + urlencode({"q": args.query})
        )
        seen = set()
        # Portal pages contain ten results; bound each tool call to five pages.
        for page in range(1, 6):
            data = await self.portal.search_page(args.query, page)
            response.total = data.total
            for item in data.data:
                try:
                    result = normalize_result(item)
                except ValueError:
                    response.warnings.append(
                        "An upstream result with an unapproved source URL was omitted."
                    )
                    continue
                if result.id not in seen:
                    response.results.append(result)
                    seen.add(result.id)
                    self.remember(result)
                if len(response.results) == args.limit:
                    break
            if len(response.results) >= args.limit or not data.data or page * 10 >= data.total:
                break
        self.cache.set(key, response, 600)
        return response

    async def search_classifications(self, query: str, limit: int = 10) -> SearchResponse:
        args = SearchInput(query=query, limit=limit)
        key = f"classification-search:{turkish_casefold(args.query)}:{args.limit}"
        cached = self.cache.get(key)
        if cached is not None:
            for item in cached.results:
                self.remember(item)
            return cached
        records = await self.classifications.search(args.query)
        response = SearchResponse(
            query=args.query,
            total=len(records),
            results=records[: args.limit],
            source_url=f"{CLASSIFICATION}/Classifications/ClassificationsTurDetay?"
            + urlencode({"tur": 0, "searchname": args.query}),
        )
        for item in response.results:
            self.remember(item)
        self.cache.set(key, response, 600)
        return response

    async def themes(self) -> ThemesResponse:
        result = self.cache.get("themes")
        if result is None:
            result = ThemesResponse(
                source_url=f"{PORTAL}/tr/statistical-themes", themes=await self.portal.themes()
            )
            self.cache.set("themes", result, 21600)
        return result

    async def resource(self, resource_id: str) -> TuikResource:
        validate_id(resource_id)
        cached = self.cache.get("metadata:" + resource_id)
        if cached is not None:
            return cached
        if resource_id.startswith("press:"):
            result = await self.portal.press(resource_id)
        elif resource_id.startswith("flow:"):
            _, datasets = await self._datasets()
            result = next((dataset for dataset in datasets if dataset.id == resource_id), None)
            if result is None:
                raise SourceError(
                    "not_found",
                    "Dataflow is absent from the current public catalog",
                    f"{PORTAL}/api/tr/dataflows",
                )
        else:
            record = self.cache.get("record:" + resource_id)
            if record is None:
                raise SourceError(
                    "resource_expired",
                    "Search again to obtain this resource in the current server session",
                    CLASSIFICATION if resource_id.startswith("classification:") else PORTAL,
                )
            result = TuikResource(**record.model_dump())
            if self.portal.is_download(result.source_url):
                result.download_links = [
                    DownloadLink(title=result.title, source_url=result.source_url)
                ]
            result.notes = (
                "Metadata available from the official search record; further fields "
                "are not exposed by a supported detail endpoint."
            )
        self.cache.set("metadata:" + resource_id, result, 3600)
        return result

    async def data(
        self, resource_id: str, filters: dict[str, str] | None = None, limit: int = 100
    ) -> DataResponse:
        validate_id(resource_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("Data limit must be an integer between 1 and 1000")
        filters = filters or {}
        if len(filters) > 20 or any(
            not isinstance(k, str) or not isinstance(v, str) or len(k) > 200 or len(v) > 200
            for k, v in filters.items()
        ):
            raise ValueError(
                "Filters support at most 20 exact string column/value pairs (200 characters each)"
            )
        resource = await self.resource(resource_id)
        base = dict(
            resource_id=resource_id,
            source_url=resource.source_url,
            notes=resource.notes,
            download_links=resource.download_links,
        )
        csv_link = next((link for link in resource.download_links if link.format == "csv"), None)
        if filters and csv_link is None:
            return DataResponse(
                **base,
                status="unsupported_filters",
                message=(
                    "Filters are supported only for public CSV exports; no filters were applied."
                ),
            )
        if not resource.download_links:
            return DataResponse(
                **base,
                status="not_structured",
                message=(
                    "This resource has no supported public structured export. "
                    "Open the official source page."
                ),
            )
        link = csv_link or resource.download_links[0]
        cached = self.cache.get("download:" + link.source_url)
        if cached is None:
            body, verified = await self.portal.download(link)
            # Avoid retaining large response bodies in the in-memory cache.
            if len(body) <= 256 * 1024:
                self.cache.set("download:" + link.source_url, (body, verified), 600)
        else:
            body, verified = cached
        base["download_links"] = [
            verified if item.source_url == link.source_url else item
            for item in resource.download_links
        ]
        if verified.format != "csv":
            return DataResponse(
                **base,
                status="download_available",
                message=(
                    "Official download verified. Spreadsheet/PDF ingestion is outside "
                    "this MVP; limit does not truncate the file."
                ),
            )
        columns, rows, total = await asyncio.to_thread(
            self.portal.csv_rows, body, verified.source_url, limit, filters
        )
        base["source_url"] = verified.source_url
        return DataResponse(
            **base,
            status="ok",
            columns=columns,
            rows=rows,
            total_rows=total,
            truncated=total > limit,
            message=(
                "CSV strings are preserved as published. Exact filters are applied "
                "locally after bounded download."
            ),
        )
