"""Chart requests, portable plotting data and the structured tool response."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tuik_mcp.artifacts import ArtifactSummary

from .common import Provenance
from .dataset import DownloadLink, RelatedDataset

ChartKind = Literal["line", "bar", "horizontal_bar", "ranked_bar", "scatter", "heatmap"]
ChartFormat = Literal["png", "svg", "pdf"]
ChartTheme = Literal["publication", "monochrome"]
ChartOrder = Literal["chronological", "source", "value_descending"]
PointState = Literal["ok", "missing", "invalid"]
MAX_POINTS = 5000
MAX_SERIES = 8
MAX_BAR_POINTS = 60
MAX_HORIZONTAL_POINTS = 24
MAX_HEATMAP_POINTS = 24
PlotNumber = Annotated[float, Field(ge=-1e100, le=1e100, allow_inf_nan=False)]
PointLabel = Annotated[str, Field(max_length=600)]


class ChartOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    formats: list[ChartFormat] = Field(default_factory=lambda: ["png"], min_length=1, max_length=3)
    theme: ChartTheme = "publication"
    series_units: list[str] | None = Field(default=None, min_length=1, max_length=MAX_SERIES)
    x_unit: str | None = Field(default=None, min_length=1, max_length=80)
    source_note: str | None = Field(default=None, min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_options(self):
        if len(set(self.formats)) != len(self.formats):
            raise ValueError("Chart formats must be distinct")
        if self.series_units is not None and any(
            not 1 <= len(unit) <= 80 for unit in self.series_units
        ):
            raise ValueError("Series units must have 1–80 characters")
        return self


class ChartSpecSeries(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    name: str
    column: str
    values: list[PlotNumber | None] = Field(min_length=1, max_length=MAX_POINTS)
    states: list[PointState | None] = Field(min_length=1, max_length=MAX_POINTS)


class ChartSpec(BaseModel):
    """A versioned plotting input. Null values are gaps, with an optional row status."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    schema_version: Literal["tuik.chart-spec/1"] = "tuik.chart-spec/1"
    chart: ChartKind
    theme: ChartTheme
    title: str = Field(min_length=1, max_length=600)
    x_columns: list[str] = Field(min_length=1, max_length=3)
    x_labels: list[PointLabel] = Field(min_length=1, max_length=MAX_POINTS)
    x_values: list[PlotNumber] | None = Field(default=None, min_length=1, max_length=MAX_POINTS)
    x_unit: str | None = None
    x_order: ChartOrder
    break_before: list[int] = Field(default_factory=list, max_length=MAX_POINTS - 1)
    series: list[ChartSpecSeries] = Field(min_length=1, max_length=MAX_SERIES)
    unit: str | None
    unit_source: Literal["source_metadata", "declared", "unknown"]
    resource_id: str
    source_url: str
    retrieved_at: datetime
    snapshot_id: str
    source_note: str | None = Field(max_length=300)
    alt_text: str

    @model_validator(mode="after")
    def validate_geometry(self):
        points = len(self.x_labels)
        if any(
            len(line.values) != points
            or len(line.states) != points
            or any(
                (value is not None) != (state == "ok")
                for value, state in zip(line.values, line.states, strict=True)
            )
            for line in self.series
        ):
            raise ValueError("All series must have one value and state per x label")
        if (self.x_values is not None) != (self.chart == "scatter"):
            raise ValueError("Exactly scatter charts require numeric x values")
        if self.x_values is not None and len(self.x_values) != points:
            raise ValueError("Scatter charts need one numeric x value per point")
        if self.x_unit is not None and self.chart != "scatter":
            raise ValueError("Only scatter charts have a numeric x unit")
        if self.break_before and (
            self.chart != "line"
            or self.break_before != sorted(set(self.break_before))
            or not 0 < self.break_before[0] <= self.break_before[-1] < points
        ):
            raise ValueError("Line breaks must be unique, increasing, in-range x positions")
        if self.chart == "ranked_bar":
            if len(self.series) != 1 or self.x_order != "value_descending":
                raise ValueError("Ranked bars require one descending series")
            rank = [
                value if value is not None else float("-inf") for value in self.series[0].values
            ]
            if any(rank[i] > rank[i - 1] for i in range(1, points)):
                raise ValueError("Ranked bars put the highest values first and gaps last")
        if self.chart in ("ranked_bar", "heatmap") and len(set(self.x_labels)) != points:
            raise ValueError("Ranked bars and heatmaps require unique categories")
        cap = {
            "bar": MAX_BAR_POINTS,
            "horizontal_bar": MAX_HORIZONTAL_POINTS,
            "ranked_bar": MAX_HORIZONTAL_POINTS,
            "heatmap": MAX_HEATMAP_POINTS,
        }.get(self.chart, MAX_POINTS)
        if points > cap:
            raise ValueError(f"{self.chart} supports at most {cap} x points")
        if len(self.series) > 1 and self.unit is None:
            raise ValueError("A common unit is required for multiple chart series")
        return self


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
    chart: ChartKind = "line"
    title: str | None = None
    unit: str | None = None
    format: str | None = None
    sheet: str | None = None
    sheets: list[str] = Field(default_factory=list)
    snapshot_id: str | None = None
    x: list[str] = Field(default_factory=list)
    # "chronological" when every x label is a recognised date/period (oldest first).
    x_order: ChartOrder | None = None
    x_points: int = 0
    matched_rows: int = 0
    series: list[ChartSeries] = Field(default_factory=list)
    image: Literal["image/png"] | None = None
    width_px: int | None = None
    height_px: int | None = None
    alt_text: str | None = None
    warnings: list[str] = Field(default_factory=list)
    # With save=true: the immutable bundle holding this chart, its data and source (#88).
    artifact: ArtifactSummary | None = None
    download_links: list[DownloadLink] = Field(default_factory=list)
    related_datasets: list[RelatedDataset] = Field(default_factory=list)
