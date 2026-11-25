"""Versioned normalized observation contract (#87).

Each statistical field records where it came from: `source` (a published cell or
column value), `recipe` (a reviewed family mapping in tuik_mcp.observations), `user`
(the caller's explicit selection) or `unknown`. Original strings stay beside every
derived value, so a caller can always check the published text.
"""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from .common import Provenance

SCHEMA_VERSION = "tuik.observation/1"

FieldSource = Literal["source", "recipe", "user", "unknown"]
Frequency = Literal["year", "half", "quarter", "month"]
GeographyLevel = Literal["national", "nuts1", "nuts2", "province", "district", "unknown"]
UnitKind = Literal[
    "index",
    "percent",
    "percentage_points",
    "persons",
    "currency",
    "terajoule",
    "unknown",
]
MeasureType = Literal["level", "index", "rate", "ratio", "change", "share", "unknown"]
ChangeBasis = Literal[
    "previous_period",
    "same_period_previous_year",
    "previous_december",
    "twelve_month_average",
]
Adjustment = Literal["adjusted", "not_adjusted", "not_applicable", "unknown"]
ObservationStatus = Literal["valid", "missing", "confidential", "invalid", "duplicate", "unmapped"]


class CsvLocator(BaseModel):
    format: Literal["csv"] = "csv"
    # One-based CSV record, counting the header as record 1 (#86 audit convention).
    record: int = Field(ge=2)
    column: str


class SpreadsheetLocator(BaseModel):
    """Reserved for workbook lineage; no workbook recipe is verified yet (#86 follow-up 1)."""

    format: Literal["xls", "xlsx"]
    sheet: str
    cell: str
    header_path: list[str] = Field(default_factory=list)
    merged_from: list[str] = Field(default_factory=list)


class Period(BaseModel):
    value: str
    frequency: Frequency
    frequency_label: str
    source: FieldSource


class Geography(BaseModel):
    label: str
    code: str | None = None
    level: GeographyLevel
    # The label is published; the code and level come from `source`.
    source: FieldSource


class Unit(BaseModel):
    kind: UnitKind
    # Published unit text, when the source states one.
    label: str | None = None
    # Values are published in 10**multiplier of the unit (3 = thousands).
    multiplier: int = 0
    currency: str | None = None
    base_period: str | None = None
    source: FieldSource


class Measure(BaseModel):
    type: MeasureType
    label: str
    basis: ChangeBasis | None = None
    source: FieldSource


class Adjustments(BaseModel):
    seasonal: Adjustment
    calendar: Adjustment
    label: str | None = None
    source: FieldSource


class Observation(BaseModel):
    id: str
    status: ObservationStatus
    reason: str | None = None
    # The published text, unchanged.
    value_text: str
    # Exact decimal of a valid observation, otherwise null. Serialized as a string.
    value: Decimal | None = None
    locator: CsvLocator | SpreadsheetLocator
    period: Period | None = None
    geography: Geography | None = None
    unit: Unit | None = None
    measure: Measure | None = None
    adjustment: Adjustments
    # Dimensions that vary within the file, keyed by concept name.
    dimensions: dict[str, str] = Field(default_factory=dict)
    # Published observation and confidentiality status text.
    source_status: dict[str, str] = Field(default_factory=dict)


class RecipeRef(BaseModel):
    id: str
    version: int
    domain: str
    title: str
    evidence: str


class ObservationCounts(BaseModel):
    source_records: int = Field(ge=0)
    selected: int = Field(ge=0)
    valid: int = Field(ge=0)
    missing: int = Field(ge=0)
    confidential: int = Field(ge=0)
    invalid: int = Field(ge=0)
    duplicate: int = Field(ge=0)
    unmapped: int = Field(ge=0)


class SeriesGap(BaseModel):
    """One selected series that lacks a valid value for some periods of the range."""

    # Varying dimensions that identify the series, by concept name.
    dimensions: dict[str, str]
    # Periods in the range with no source record for this series (first ones only).
    absent: list[str]
    absent_count: int = Field(ge=0)
    # Periods with a record but no value: missing, confidential, duplicate, invalid
    # or unmapped (first ones only).
    without_value: list[str]
    without_value_count: int = Field(ge=0)


class RangeCoverage(BaseModel):
    """Requested period coverage, separate from source-scan completeness."""

    frequency: Frequency
    # Inclusive range checked. An open bound uses the selection's own first or
    # last period; `bounds` says which side the caller requested.
    period_from: str
    period_to: str
    bounds: Literal["requested", "from_requested", "to_requested"]
    periods: int = Field(ge=0)
    series: int = Field(ge=0)
    # series × periods: one valid value expected for each.
    expected: int = Field(ge=0)
    valid: int = Field(ge=0)
    # True only when every selected series has a valid value for every period.
    complete: bool
    gaps: list[SeriesGap] = Field(default_factory=list)
    gaps_truncated: bool = False


class RecipeSummary(BaseModel):
    id: str
    version: int
    domain: str
    title: str
    resource_ids: list[str]
    evidence: str
    measures: list[str]
    example: dict[str, str]


class UnavailableFamily(BaseModel):
    domain: str
    # Flow IDs without version: every version is held.
    flows: list[str]
    reason: str


class ObservationResponse(Provenance):
    schema_version: str = SCHEMA_VERSION
    resource_id: str | None = None
    status: Literal["ok", "catalog", "unsupported", "unavailable", "schema_changed"]
    message: str
    recipe: RecipeRef | None = None
    snapshot_id: str | None = None
    # True only when every source record was read and classified. It says nothing
    # about whether a requested period range is covered; see `coverage`.
    complete: bool = False
    counts: ObservationCounts | None = None
    selection: dict[str, str] = Field(default_factory=dict)
    # Set when a period range was requested.
    coverage: RangeCoverage | None = None
    # Values shared by every record, by concept name; metadata, not a dimension.
    constant_dimensions: dict[str, str] = Field(default_factory=dict)
    # Varying dimension concepts and their values, in source order of first appearance.
    dimension_values: dict[str, list[str]] = Field(default_factory=dict)
    dimension_values_truncated: bool = False
    notes: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    offset: int = Field(default=0, ge=0)
    has_more: bool = False
    # Opaque continuation bound to this snapshot, resource and selection.
    next_cursor: str | None = None
    recipes: list[RecipeSummary] = Field(default_factory=list)
    unavailable: list[UnavailableFamily] = Field(default_factory=list)
