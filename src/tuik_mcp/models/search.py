from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .common import Provenance, SearchInput

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
    score: float | None = None
    highlights: dict[str, list[str]] = Field(default_factory=dict)
    indexed_at: str | None = None
    themes: list[str] = Field(default_factory=list)
    downloadable: bool | None = None
    formats: list[str] = Field(default_factory=list)


class SearchResponse(Provenance):
    query: str
    results: list[TuikSearchResult] = Field(default_factory=list)
    total: int | None = None
    warnings: list[str] = Field(default_factory=list)


class SearchFacet(BaseModel):
    counts: dict[str, int]
    truncated: bool = False
    count_error_upper_bound: int = 0


class SearchFacets(BaseModel):
    kind: SearchFacet
    theme: SearchFacet


class IndexedSearchResponse(SearchResponse):
    source: Literal["index", "portal", "catalog"]
    backend: Literal["opensearch", "portal", "literal"]
    limit: int
    truncated: bool | None = None
    facets: SearchFacets | None = None
    indexed_at: str | None = None


class IndexedSearchInput(SearchInput):
    kind: Literal["dataset", "bulletin", "metadata"] | None = None
    theme: str | None = Field(default=None, min_length=1, max_length=200)
    published_after: date | None = None
    published_before: date | None = None
    downloadable_only: bool = Field(default=False, strict=True)
    live: bool = Field(default=False, strict=True)

    @field_validator("theme")
    @classmethod
    def clean_theme(cls, value):
        return SearchInput(query=value).query if value is not None else None

    @field_validator("published_after", "published_before", mode="before")
    @classmethod
    def iso_date(cls, value):
        if value is not None and not (type(value) is date or isinstance(value, str)):
            raise ValueError("Publication bounds must be ISO dates (YYYY-MM-DD)")
        if isinstance(value, str):
            # Reject timestamps, compact dates and week dates accepted by some parsers.
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValueError("Publication bounds must be ISO dates (YYYY-MM-DD)")
        return value

    @model_validator(mode="after")
    def ordered_dates(self):
        if self.published_after and self.published_before:
            if self.published_after > self.published_before:
                raise ValueError("published_after must not be later than published_before")
        return self


class Theme(Provenance):
    id: str
    name: str


class ThemesResponse(Provenance):
    themes: list[Theme]
