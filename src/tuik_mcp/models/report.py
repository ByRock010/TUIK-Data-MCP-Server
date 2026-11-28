"""Evidence-bound report contract shared by every renderer (#91).

A `ReportSpec` is what a client asks for: evidence selections, sections and
optional narrative. The server resolves each evidence entry against a reviewed
observation recipe (#87) or a deterministic calculation (#89), checks every
number in the narrative against its bound evidence, and returns a `Report`.
Renderers (PowerPoint, PDF, Word) read the `Report` only: they never fetch,
recalculate or write prose.

All text is plain data. It is never parsed as HTML, Markdown or a template, and
control and formatting characters are refused.
"""

import unicodedata
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field, model_validator

from tuik_mcp.artifacts import ArtifactSummary

from .calculation import MeasureSpec, UnitSpec
from .common import Provenance

SPEC_VERSION = "tuik.report-spec/1"
SCHEMA_VERSION = "tuik.report/1"
REPORT_RECIPE = "report-recipe.json"

MAX_EVIDENCE = 40
MAX_SECTIONS = 12
MAX_FINDINGS = 8
MAX_CLAIMS = 8
MAX_TABLE_ROWS = 20
MAX_CHART_SERIES = 4
MAX_SERIES_POINTS = 240
MAX_TEXT = 600


def plain(value: str) -> str:
    """NFC text without control, format (bidi override) or unassigned characters."""
    value = unicodedata.normalize("NFC", value).strip()
    if any(unicodedata.category(c).startswith("C") for c in value):
        raise ValueError("Text may not contain control or formatting characters")
    return value


def text_field(max_length: int, min_length: int = 1):
    return Annotated[
        str, Field(min_length=min_length, max_length=max_length), AfterValidator(plain)
    ]


def no_digits(value: str | None) -> str | None:
    if value is not None and any(c.isdigit() for c in value):
        raise ValueError("A label may not contain digits; state numbers through claims")
    return value


Key = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")]
PeriodText = Annotated[str, Field(min_length=4, max_length=7)]
Filters = Annotated[dict[str, str], Field(max_length=12)]
Language = Literal["tr", "en"]
SectionKind = Literal["summary", "findings", "table", "chart", "methods", "limitations", "sources"]
# Generated from the evidence; a spec may place them but gives them no content.
GENERATED = ("methods", "limitations", "sources")


class EvidenceBase(BaseModel):
    key: Key
    resource_id: Annotated[str, Field(max_length=200)]
    filters: Filters = Field(default_factory=dict)
    snapshot_id: Annotated[str | None, Field(max_length=80)] = None
    # Shown instead of the published measure label. No digits: a number in a label
    # would escape the claim checks.
    label: Annotated[text_field(120) | None, AfterValidator(no_digits)] = None
    decimals: Annotated[int, Field(ge=0, le=10)] = 2


class ObservationEvidenceSpec(EvidenceBase):
    """One published value: the filters must leave one series, read at `period`."""

    kind: Literal["observation"]
    period: PeriodText


class CalculationEvidenceSpec(EvidenceBase):
    """One derived value from calculate_tuik_series: a result period or an extremum."""

    kind: Literal["calculation"]
    operation: Literal[
        "difference", "percent_change", "pp_difference", "moving_average", "extrema", "rebase"
    ]
    basis: Literal["previous_period", "same_period_previous_year", "periods"] | None = None
    periods: Annotated[list[PeriodText] | None, Field(min_length=2, max_length=2)] = None
    window: Annotated[int | None, Field(ge=2, le=60)] = None
    base_period: PeriodText | None = None
    # The result period; not with basis="periods" (its target) or extrema.
    period: PeriodText | None = None
    extremum: Literal["min", "max"] | None = None
    # The range an extremum is taken over.
    period_from: PeriodText | None = None
    period_to: PeriodText | None = None

    @model_validator(mode="after")
    def check_selection(self):
        if self.operation == "extrema":
            if self.extremum is None or self.period is not None:
                raise ValueError(f"{self.key}: extrema takes extremum (min or max), not period")
        else:
            if self.extremum is not None or self.period_from or self.period_to:
                raise ValueError(
                    f"{self.key}: extremum, period_from and period_to are for extrema only"
                )
            if (self.period is None) == (self.basis != "periods"):
                raise ValueError(
                    f"{self.key}: give period, except with basis='periods', whose target "
                    "is periods[1]"
                )
        return self


class SeriesEvidenceSpec(EvidenceBase):
    """Published values of one series over an explicit range, for charts."""

    kind: Literal["series"]
    period_from: PeriodText
    period_to: PeriodText


EvidenceSpec = Annotated[
    ObservationEvidenceSpec | CalculationEvidenceSpec | SeriesEvidenceSpec,
    Field(discriminator="kind"),
]


class ClaimBinding(BaseModel):
    """Binds one number in a finding's text to the evidence it states.

    `quote` is the exact text of the number with its sign, unit marker and scale
    word, such as `%32,87`, `1,2 yüzde puan`, `-0.6 percentage points` or
    `3.214 bin`.
    """

    evidence: Key
    quote: text_field(80)


class FindingSpec(BaseModel):
    # The evidence this finding is about, in order.
    evidence: Annotated[list[Key], Field(min_length=1, max_length=4)]
    # Optional host narrative. Without it the server writes a deterministic sentence.
    text: text_field(MAX_TEXT) | None = None
    claims: Annotated[list[ClaimBinding], Field(max_length=MAX_CLAIMS)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def check_claims(self):
        if self.claims and self.text is None:
            raise ValueError("claims bind numbers in text; give text or drop claims")
        for claim in self.claims:
            if claim.evidence not in self.evidence:
                raise ValueError(
                    f"claim evidence {claim.evidence!r} is not in the finding's evidence"
                )
        return self


class TableSpec(BaseModel):
    rows: Annotated[list[Key], Field(min_length=1, max_length=MAX_TABLE_ROWS)]


class ChartSpec(BaseModel):
    """A chart of series evidence. #90 adds formats and styles to the same data."""

    type: Literal["line", "bar"] = "line"
    series: Annotated[list[Key], Field(min_length=1, max_length=MAX_CHART_SERIES)]


class SectionSpec(BaseModel):
    kind: SectionKind
    title: text_field(120) | None = None
    findings: Annotated[list[FindingSpec], Field(max_length=MAX_FINDINGS)] = Field(
        default_factory=list
    )
    table: TableSpec | None = None
    chart: ChartSpec | None = None

    @model_validator(mode="after")
    def check_layout(self):
        wanted = {
            "summary": "findings",
            "findings": "findings",
            "table": "table",
            "chart": "chart",
        }.get(self.kind)
        given = {
            name
            for name, present in (
                ("findings", bool(self.findings)),
                ("table", self.table is not None),
                ("chart", self.chart is not None),
            )
            if present
        }
        if wanted is None and given:
            raise ValueError(
                f"A {self.kind} section is generated from the evidence; give no content"
            )
        if wanted is not None and given != {wanted}:
            raise ValueError(f"A {self.kind} section takes {wanted} and nothing else")
        return self


class ReportSpec(BaseModel):
    schema_version: Literal["tuik.report-spec/1"] = SPEC_VERSION
    title: text_field(200)
    subtitle: text_field(200) | None = None
    audience: text_field(200) | None = None
    language: Language = "tr"
    # Data cutoff: no evidence released or observed after this date.
    source_cutoff: date | None = None
    # reject: any unverified narrative refuses the report. mark: it is kept, visibly
    # labelled as unverified draft content, and the report status is draft.
    on_unverified: Literal["reject", "mark"] = "reject"
    evidence: Annotated[list[EvidenceSpec], Field(min_length=1, max_length=MAX_EVIDENCE)]
    sections: Annotated[list[SectionSpec], Field(min_length=1, max_length=MAX_SECTIONS)]

    @model_validator(mode="after")
    def check_references(self):
        kinds: dict[str, str] = {}
        for item in self.evidence:
            if item.key in kinds:
                raise ValueError(f"Duplicate evidence key {item.key!r}")
            kinds[item.key] = item.kind
        placed = [s.kind for s in self.sections if s.kind in GENERATED]
        if len(placed) != len(set(placed)):
            raise ValueError("methods, limitations and sources may each appear once")

        def need(key: str, allowed: set[str], where: str) -> None:
            if key not in kinds:
                raise ValueError(f"{where} refers to unknown evidence {key!r}")
            if kinds[key] not in allowed:
                raise ValueError(
                    f"{where} needs {' or '.join(sorted(allowed))} evidence; "
                    f"{key!r} is {kinds[key]}"
                )

        for number, section in enumerate(self.sections, 1):
            where = f"section {number} ({section.kind})"
            for finding in section.findings:
                for key in finding.evidence:
                    need(key, {"observation", "calculation"}, where)
            if section.table is not None:
                for key in section.table.rows:
                    need(key, {"observation", "calculation"}, where)
            if section.chart is not None:
                for key in section.chart.series:
                    need(key, {"series"}, where)
        return self


# Resolved report -----------------------------------------------------------


class Adjustment(BaseModel):
    seasonal: str
    calendar: str


class EvidenceSource(BaseModel):
    resource_id: str
    recipe_id: str
    recipe_version: int
    snapshot_id: str
    source_url: str
    source_scope: Literal["whole_export", "reviewed_cells"]
    # Release date of the source as the portal states it; null means unknown and is
    # never replaced by the retrieval time.
    publication_date: str | None = None
    retrieved_at: datetime


class Point(BaseModel):
    period: str
    # An observation status, or missing when the source has no record for the period.
    status: str
    value: str | None = None
    id: str | None = None


class Evidence(BaseModel):
    key: str
    kind: Literal["observation", "calculation", "series"]
    label: str
    # Observation ID, or `{calculation_id}:{period|min|max}`; null for a series.
    id: str | None = None
    status: Literal["valid", "missing", "undefined"]
    reason: str | None = None
    # Exact decimal string, and its display rounding to `decimals`.
    value: str | None = None
    rounded: str | None = None
    # Localized value with its unit, as templates and tables print it.
    display: str | None = None
    decimals: int
    frequency: str
    period: str | None = None
    # Reference period of a change; range of an extremum or a series.
    reference_period: str | None = None
    period_from: str | None = None
    period_to: str | None = None
    # Every tied period of an extremum, in order.
    periods: list[str] = Field(default_factory=list)
    tie_count: int | None = None
    unit: UnitSpec
    unit_label: str | None = None
    measure: MeasureSpec
    measure_label: str | None = None
    adjustment: Adjustment
    derived: bool
    formula: str | None = None
    calculation_id: str | None = None
    # Observation IDs the value was read or computed from.
    inputs: list[str] = Field(default_factory=list)
    # The caller's filters and the published values that identify the series.
    dimensions: dict[str, str] = Field(default_factory=dict)
    points: list[Point] = Field(default_factory=list)
    # One valid value for every period the evidence covers.
    coverage_complete: bool = True
    source: EvidenceSource
    notes: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class Claim(BaseModel):
    evidence: str
    quote: str
    status: Literal["verified", "rejected"]
    problems: list[str] = Field(default_factory=list)


class Finding(BaseModel):
    # template: written by the server from the evidence. host: narrative the client
    # supplied; it passed or failed the same claim checks.
    origin: Literal["template", "host"]
    status: Literal["verified", "unverified"]
    text: str
    evidence: list[str]
    claims: list[Claim] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)


class TableRow(BaseModel):
    evidence: str
    label: str
    period: str | None = None
    value: str | None = None
    status: str
    id: str | None = None


class Table(BaseModel):
    columns: list[str]
    rows: list[TableRow]


class ChartLine(BaseModel):
    evidence: str
    label: str
    points: list[Point]


class Chart(BaseModel):
    type: Literal["line", "bar"]
    unit: UnitSpec
    unit_label: str | None = None
    frequency: str
    series: list[ChartLine]
    # Deterministic text description for screen readers and image fallbacks.
    alt_text: str


class Section(BaseModel):
    kind: SectionKind
    title: str
    findings: list[Finding] = Field(default_factory=list)
    table: Table | None = None
    chart: Chart | None = None
    # Lines of a generated methods, limitations or sources section.
    items: list[str] = Field(default_factory=list)


class Timeline(BaseModel):
    """Four distinct dates; none stands in for another."""

    # Latest release date among the sources; unknown ones are listed, not guessed.
    latest_release: str | None = None
    release_unknown: list[str] = Field(default_factory=list)
    latest_observation: str | None = None
    retrieved_from: datetime
    retrieved_to: datetime
    data_cutoff: date | None = None


class ReportCounts(BaseModel):
    evidence: int = Field(ge=0)
    findings: int = Field(ge=0)
    verified: int = Field(ge=0)
    unverified: int = Field(ge=0)


class Report(Provenance):
    schema_version: str = SCHEMA_VERSION
    # ok: every finding verified. draft: unverified findings kept and marked.
    # rejected: unverified findings with on_unverified=reject; nothing was saved.
    status: Literal["ok", "draft", "rejected"]
    message: str
    # Deterministic: the same spec over the same snapshots gives the same ID.
    report_id: str
    title: str
    subtitle: str | None = None
    audience: str | None = None
    language: Language
    timeline: Timeline
    counts: ReportCounts
    evidence: list[Evidence] = Field(default_factory=list)
    sections: list[Section] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    # Unverified numbers, dates or comparisons in the title, subtitle, audience or
    # section titles; these fields state no claims.
    problems: list[str] = Field(default_factory=list)
    # False when the inline response leaves out evidence and sections to fit the
    # output budget; report.json holds them.
    inline_complete: bool = True
    spec: ReportSpec | None = None
    artifact: ArtifactSummary | None = None
