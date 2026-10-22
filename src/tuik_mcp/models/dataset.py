from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .common import Provenance, SearchInput
from .search import TuikSearchResult


class DownloadLink(BaseModel):
    title: str
    source_url: str
    format: str | None = None
    verified: bool = False
    size_bytes: int | None = None
    # Reusable ID of a bulletin file; the download URL itself changes over time.
    resource_id: str | None = None


class MetadataSection(BaseModel):
    title: str
    content: str


class RelatedDataset(Provenance):
    id: str
    title: str
    available_formats: list[str] = Field(default_factory=list)
    category_path: list[str] = Field(default_factory=list)
    downloadable: bool = False


class TuikResource(TuikSearchResult):
    updated_at: str | None = None
    available_formats: list[str] = Field(default_factory=list)
    download_links: list[DownloadLink] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    metadata: list[MetadataSection] = Field(default_factory=list)
    related_datasets: list[RelatedDataset] = Field(default_factory=list)
    notes: str | None = None


class DatasetSearchInput(SearchInput):
    downloadable_only: bool = Field(default=True, strict=True)
    theme: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("theme")
    @classmethod
    def clean_theme(cls, value: str | None) -> str | None:
        return cls.clean_query(value) if value is not None else None


class DatasetSearchResponse(Provenance):
    query: str
    downloadable_only: bool
    theme: str | None = None
    results: list[TuikResource]
    total: int
    truncated: bool


class DataResponse(Provenance):
    resource_id: str
    status: Literal[
        "ok", "not_structured", "download_available", "unsupported_filters", "related_datasets"
    ]
    message: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, str]] = Field(default_factory=list)
    unit: str | None = None
    notes: str | None = None
    download_links: list[DownloadLink] = Field(default_factory=list)
    total_rows: int | None = None
    related_datasets: list[RelatedDataset] = Field(default_factory=list)
    truncated: bool = False
    format: str | None = None
    extraction: str | None = None
    sheet: str | None = None
    sheets: list[str] = Field(default_factory=list)
    table_title: str | None = None
    table_title_en: str | None = None
    columns_en: list[str] = Field(default_factory=list)
    table_notes: list[str] = Field(default_factory=list)
    source_note: str | None = None
    layout: Literal["table", "raw"] | None = None
    warnings: list[str] = Field(default_factory=list)
