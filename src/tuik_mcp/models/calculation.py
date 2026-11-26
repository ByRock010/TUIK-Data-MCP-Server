"""Versioned contract for derived values computed from normalized observations (#89).

A calculation never edits an observation. It names the operation, the exact
input observation IDs and values, the output unit and the rounding, so the same
result can be recomputed from the response or its saved bundle alone.
"""

from typing import Literal

from pydantic import BaseModel, Field

from tuik_mcp.artifacts import ArtifactSummary

from .common import Provenance
from .observation import ChangeBasis, Frequency, MeasureType, RangeCoverage, RecipeRef, UnitKind

SCHEMA_VERSION = "tuik.calculation/1"
# Divisions are rounded half-to-even to this many places before they are reported.
VALUE_PLACES = 10

Operation = Literal[
    "difference",
    "percent_change",
    "pp_difference",
    "moving_average",
    "extrema",
    "rebase",
]
Basis = Literal["previous_period", "same_period_previous_year", "periods"]
ResultStatus = Literal["valid", "missing", "undefined"]


class CalculationSpec(BaseModel):
    """Everything that determines the result, besides the input values."""

    operation: Operation
    # Reference period of difference, percent_change and pp_difference.
    basis: Basis | None = None
    # [reference, target] when basis is "periods".
    periods: list[str] | None = None
    # Trailing window length of moving_average, in periods.
    window: int | None = None
    # Common base period of rebase; the base value becomes 100.
    base_period: str | None = None
    decimals: int = Field(default=2, ge=0, le=10)
    filters: dict[str, str] = Field(default_factory=dict)
    # Output periods. Inputs before period_from may be read for lags and windows.
    period_from: str | None = None
    period_to: str | None = None


class UnitSpec(BaseModel):
    kind: UnitKind
    multiplier: int = 0
    currency: str | None = None
    base_period: str | None = None


class MeasureSpec(BaseModel):
    type: MeasureType
    basis: ChangeBasis | Literal["periods"] | None = None


class Rounding(BaseModel):
    value_places: int = VALUE_PLACES
    decimals: int
    mode: Literal["ROUND_HALF_EVEN"] = "ROUND_HALF_EVEN"


class CalculationInput(BaseModel):
    """One normalized observation a result reads; value is null unless status is valid."""

    id: str
    period: str
    status: str
    value: str | None = None


class CalculatedValue(BaseModel):
    period: str
    status: ResultStatus
    reason: str | None = None
    # Exact for differences; divisions use `rounding.value_places`.
    value: str | None = None
    # `value` rounded to `rounding.decimals` for display.
    rounded: str | None = None
    inputs: list[str] = Field(default_factory=list)


class Extremum(BaseModel):
    kind: Literal["min", "max"]
    value: str
    rounded: str
    # Every period that reaches the value: ties are all reported.
    periods: list[str]
    inputs: list[str]


class SeriesResult(BaseModel):
    # Varying dimensions that identify the series, by concept name.
    dimensions: dict[str, str] = Field(default_factory=dict)
    inputs: list[CalculationInput] = Field(default_factory=list)
    results: list[CalculatedValue] = Field(default_factory=list)
    extrema: list[Extremum] = Field(default_factory=list)


class CalculationCounts(BaseModel):
    series: int = Field(ge=0)
    results: int = Field(ge=0)
    valid: int = Field(ge=0)
    missing: int = Field(ge=0)
    undefined: int = Field(ge=0)


class CalculationResponse(Provenance):
    schema_version: str = SCHEMA_VERSION
    status: Literal["ok"] = "ok"
    message: str
    resource_id: str
    recipe: RecipeRef
    snapshot_id: str
    publication_date: str | None = None
    source_scope: Literal["whole_export", "reviewed_cells"]
    spec: CalculationSpec
    formula: str
    frequency: Frequency
    # Output periods actually evaluated, after defaults.
    period_from: str
    period_to: str
    input_unit: UnitSpec
    output_unit: UnitSpec
    output_measure: MeasureSpec
    rounding: Rounding
    counts: CalculationCounts
    # One valid input per series and period of the output range; extrema and
    # averages describe only the covered periods.
    coverage: RangeCoverage
    # False when the inline response leaves out inputs and results to fit the
    # output budget; the saved artifact then holds the complete calculation.
    inline_complete: bool = True
    series: list[SeriesResult] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    artifact: ArtifactSummary | None = None
