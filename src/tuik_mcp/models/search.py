from typing import Literal

from pydantic import Field

from .common import Provenance

ResourceType = Literal[
    "dataset", "table", "bulletin", "metadata", "publication", "classification", "unknown"
]


class TuikSearchResult(Provenance):
    id: str
    title: str
    description: str | None = None
    resource_type: ResourceType
    theme: str | None = None
    publication_date: str | None = None
    source: str = "TÜİK"


class SearchResponse(Provenance):
    query: str
    results: list[TuikSearchResult] = Field(default_factory=list)
    total: int | None = None
    warnings: list[str] = Field(default_factory=list)


class Theme(Provenance):
    id: str
    name: str


class ThemesResponse(Provenance):
    themes: list[Theme]
