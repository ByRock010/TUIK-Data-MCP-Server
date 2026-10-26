"""JSON-only crawl access; the regular seven MCP tools keep their adapters."""

import re
import time

import httpx
from pydantic import BaseModel, Field

from tuik_mcp.clients.base import HttpClient, SourceError
from tuik_mcp.clients.tuik_portal import Flow, Press, SearchPage, envelope, parse
from tuik_mcp.config import PORTAL, Settings


class CrawlHttpClient(HttpClient):
    def __init__(self, settings: Settings, transport=None):
        settings = settings.model_copy(
            update={"min_interval_seconds": max(2.0, settings.min_interval_seconds)}
        )
        super().__init__(settings, transport)
        self._client.event_hooks["response"].append(self._cool_down)

    async def _cool_down(self, response: httpx.Response):
        if response.status_code == 429:
            try:
                wait = min(300.0, max(30.0, float(response.headers.get("retry-after", 0))))
            except ValueError:
                wait = 30.0
            # Shared with the inherited pacing lock, including bounded retries.
            self._next_request = max(self._next_request, time.monotonic() + wait)


class Category(BaseModel):
    name: str = Field(min_length=1)
    child: "Category | None" = None


class CrawlFlow(Flow):
    category: Category | None = None
    period: str | None = None


class ThemeNode(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    url: str = ""
    children: list["ThemeNode"] = Field(default_factory=list)


class PreviousPress(BaseModel):
    title: str
    url: str


class CrawlPress(Press):
    previousPresses: list[PreviousPress] = Field(default_factory=list)


class CrawlSource:
    def __init__(self, http: CrawlHttpClient):
        self.http = http

    async def flows(self) -> list[CrawlFlow]:
        url = PORTAL + "/api/tr/dataflows"
        data = await self.http.json("GET", url)
        if not isinstance(data, list):
            raise SourceError("malformed_response", "Expected a dataflow catalog", url)
        return [parse(CrawlFlow, item, url) for item in data]

    async def themes(self) -> list[ThemeNode]:
        url = PORTAL + "/api/tr/data/statistical-themes"
        data = envelope(await self.http.json("GET", url), url)
        if not isinstance(data, list):
            raise SourceError("malformed_response", "Expected a theme tree", url)
        return [parse(ThemeNode, item, url) for item in data]

    async def listing(self, page: int) -> SearchPage:
        if type(page) is not int or not 1 <= page <= 1000:
            raise ValueError("Listing page must be an integer between 1 and 1000")
        url = PORTAL + "/api/tr/data/search"
        data = await self.http.json(
            "POST",
            url,
            json={
                "text": "",
                "page": page,
                "typeIds": [1],
                "categoryIds": [],
                "subCategoryIds": [],
                "years": [],
                "levels": [],
                "archive": False,
                "autoFilter": False,
            },
        )
        return parse(SearchPage, envelope(data, url), url)

    async def press(self, number: str) -> CrawlPress:
        if not re.fullmatch(r"[1-9][0-9]{0,9}", number):
            raise ValueError("Invalid bulletin number")
        url = f"{PORTAL}/api/tr/press/{number}"
        data = envelope(await self.http.json("GET", url), url)
        press = parse(CrawlPress, data, url)
        if str(press.id) != number:
            raise SourceError("malformed_response", "Bulletin identifier mismatch", url)
        return press
