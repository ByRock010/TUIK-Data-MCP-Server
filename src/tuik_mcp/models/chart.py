"""Chart requests and the structured summary returned next to the PNG (#72)."""

from typing import Literal

from pydantic import BaseModel, Field

from .common import Provenance
from .dataset import DownloadLink, RelatedDataset


class ChartPoint(BaseModel):
    x: str
    value: float


class ChartSeries(BaseModel):
    name: str
    column: str
    points: int
    missing: int = 0
    invalid: int = 0
    minimum: ChartPoint | None = None
    maximum: ChartPoint | None = None
    first: ChartPoint | None = None
    last: ChartPoint | None = None


class ChartResponse(Provenance):
    resource_id: str
    status: Literal["ok", "not_structured", "download_available", "related_datasets"]
    message: str | None = None
    chart: Literal["line", "bar"] = "line"
    title: str | None = None
    unit: str | None = None
    format: str | None = None
    sheet: str | None = None
    sheets: list[str] = Field(default_factory=list)
    snapshot_id: str | None = None
    x: list[str] = Field(default_factory=list)
    # "chronological" when every x label is a recognised date/period (oldest first).
    x_order: Literal["chronological", "source"] | None = None
    x_points: int = 0
    matched_rows: int = 0
    series: list[ChartSeries] = Field(default_factory=list)
    image: Literal["image/png"] | None = None
    width_px: int | None = None
    height_px: int | None = None
    warnings: list[str] = Field(default_factory=list)
    download_links: list[DownloadLink] = Field(default_factory=list)
    related_datasets: list[RelatedDataset] = Field(default_factory=list)
