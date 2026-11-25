"""Versioned statistical observations from reviewed TÜİK source selections."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import Provenance

Frequency = Literal["month", "quarter", "year"]
Origin = Literal["source_declared", "recipe_defined", "user_selected", "unknown"]
Adjustment = Literal["unadjusted", "seasonal", "calendar", "seasonal_calendar"]
HASH = r"^[a-f0-9]{64}$"
PERIOD = r"^[0-9]{4}(?:-(?:0[1-9]|1[0-2]|Q[1-4]))?$"


class ObservationSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    series: str = Field(min_length=1, max_length=60)
    start: str = Field(pattern=PERIOD)
    end: str = Field(pattern=PERIOD)
    snapshot_sha256: str | None = Field(default=None, pattern=HASH)


class Geography(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    label: str
    level: Literal["national", "province", "nuts1", "nuts2", "nuts3"]
    system: Literal["ISO 3166-1", "İBBS"]
    vintage: str | None = None


class Measure(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["level", "index", "rate"]
    unit: Literal[
        "person", "percent", "percentage_point", "index", "ratio", "terajoule", "currency"
    ]
    multiplier: Literal[1, 1000, 1000000]
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    base_year: int | None = Field(default=None, ge=1900, le=2100)

    @model_validator(mode="after")
    def validate_currency(self):
        if (self.unit == "currency") != (self.currency is not None):
            raise ValueError("Currency code is required exactly for currency units")
        return self


class Snapshot(Provenance):
    resource_id: str
    sha256: str = Field(pattern=HASH)
    retrieved_at: datetime = Field()
    publication_date: str | None = None


class CsvLocator(BaseModel):
    kind: Literal["csv"] = "csv"
    record: int = Field(ge=2)
    column: int = Field(ge=1)
    headers: list[str]


class HeaderCell(BaseModel):
    cell: str
    label: str
    merged_from: str | None = None


class WorkbookLocator(BaseModel):
    kind: Literal["xls", "xlsx"]
    sheet: str
    cell: str
    headers: list[HeaderCell]


class Observation(BaseModel):
    id: str = Field(pattern=r"^observation:[a-f0-9]{64}$")
    locator: Annotated[CsvLocator | WorkbookLocator, Field(discriminator="kind")]
    period: str = Field(pattern=PERIOD)
    original_value: str
    numeric_value: Decimal | None = Field(default=None, allow_inf_nan=False)
    status: Literal["observed", "missing", "suppressed", "invalid"]
    source_attributes: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_numeric_status(self):
        if (self.status == "observed") != (self.numeric_value is not None):
            raise ValueError("Exactly observed values carry a validated number")
        return self


class ObservationResult(BaseModel):
    schema_version: Literal[1] = 1
    mapping_id: str
    mapping_version: Literal[1] = 1
    selection: ObservationSelection
    snapshot: Snapshot
    status: Literal["complete", "incomplete"]
    frequency: Frequency
    geography: Geography
    measure: Measure
    adjustment: Adjustment | None
    origins: dict[str, Origin]
    dimensions: dict[str, str]
    observations: list[Observation]
    absent_periods: list[str]
    valid_count: int = Field(ge=0)
    expected_count: int = Field(ge=1)
    notes: list[str] = Field(default_factory=list)


class SeriesDescription(BaseModel):
    id: str
    frequency: Frequency
    geography: Geography
    measure: Measure
    adjustment: Adjustment | None
    example_period: str
    selectors: dict[str, str]


class MappingDescription(BaseModel):
    id: str
    version: Literal[1] = 1
    domain: str
    resource_id: str
    format: Literal["csv", "xls", "xlsx"]
    required_snapshot_sha256: str | None
    series: list[SeriesDescription]


class MappingDiscovery(BaseModel):
    schema_version: Literal[1] = 1
    mappings: list[MappingDescription]
    unavailable_domains: dict[str, str]
