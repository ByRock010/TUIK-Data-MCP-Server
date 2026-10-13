from typing import Literal

from pydantic import BaseModel, Field

from .common import Provenance
from .search import TuikSearchResult


class DownloadLink(BaseModel):
    title: str
    source_url: str
    format: str | None = None
    verified: bool = False
    size_bytes: int | None = None


class MetadataSection(BaseModel):
    title: str
    content: str


class TuikResource(TuikSearchResult):
    updated_at: str | None = None
    available_formats: list[str] = Field(default_factory=list)
    download_links: list[DownloadLink] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    metadata: list[MetadataSection] = Field(default_factory=list)
    notes: str | None = None


class DataResponse(Provenance):
    resource_id: str
    status: Literal["ok", "not_structured", "download_available", "unsupported_filters"]
    message: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, str]] = Field(default_factory=list)
    unit: str | None = None
    notes: str | None = None
    download_links: list[DownloadLink] = Field(default_factory=list)
    total_rows: int | None = None
    truncated: bool = False
