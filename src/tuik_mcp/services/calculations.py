"""Auditable calculations on one complete normalized snapshot (#89)."""

import csv
import io
import json
from dataclasses import dataclass

from tuik_mcp.artifacts import ArtifactInput, PendingFile
from tuik_mcp.calculations import FORMULAS, compute, formula, text
from tuik_mcp.clients.base import SourceError
from tuik_mcp.config import PORTAL
from tuik_mcp.models.calculation import (
    CalculationCounts,
    CalculationInput,
    CalculationResponse,
    CalculationSpec,
    MeasureSpec,
    Rounding,
    UnitSpec,
)
from tuik_mcp.models.observation import Observation
from tuik_mcp.observations import BY_RESOURCE, SchemaChanged, unavailable
from tuik_mcp.observations.workbooks import BY_WORKBOOK, WorkbookRecipe
from tuik_mcp.services.observations import (
    MAX_LISTED_VALUES,
    MAX_RANGE_PERIODS,
    ObservationService,
    ordinal,
    period_frequency,
    period_label,
    validate_selection,
)
from tuik_mcp.utils.normalization import validate_id
from tuik_mcp.utils.rendering import rendered

MAX_SERIES = 20
MAX_WINDOW = 60
# Tied periods listed per extremum in the inline view of a saved calculation.
MAX_INLINE_TIES = 10
LAGGED = {"difference", "percent_change", "pp_difference"}
# Quantities that add: a difference keeps their unit.
ADDITIVE = {"persons", "currency", "terajoule"}
DERIVED = (
    "Derived values computed by tuik-mcp from the listed observations; TÜİK did not "
    "publish them. Cite the formula and the input observation IDs with any result."
)
SOURCE_TYPES = {
    "csv": "text/csv",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def checked_spec(
    operation, filters, basis, periods, window, base_period, period_from, period_to, decimals
) -> CalculationSpec:
    validate_selection(filters, period_from, period_to, 0, 1)
    if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 10:
        raise ValueError("decimals must be an integer between 0 and 10")
    given = {
        "basis": basis is not None,
        "periods": periods is not None,
        "window": window is not None,
        "base_period": base_period is not None,
    }
    needed = {
        "difference": {"basis"},
        "percent_change": {"basis"},
        "pp_difference": {"basis"},
        "moving_average": {"window"},
        "extrema": set(),
        "rebase": {"base_period"},
    }
    if operation not in needed:
        raise ValueError(f"operation must be one of {sorted(needed)}")
    allowed = needed[operation] | ({"periods"} if basis == "periods" else set())
    missing = sorted(name for name in needed[operation] if not given[name])
    extra = sorted(name for name, present in given.items() if present and name not in allowed)
    if missing:
        raise ValueError(f"{operation} needs {', '.join(missing)}")
    if extra:
        raise ValueError(f"{operation} does not take {', '.join(extra)}")
    if basis is not None and basis not in (
        "previous_period",
        "same_period_previous_year",
        "periods",
    ):
        raise ValueError("basis must be previous_period, same_period_previous_year or periods")
    if basis == "periods":
        if (
            not isinstance(periods, list)
            or len(periods) != 2
            or not all(isinstance(p, str) for p in periods)
        ):
            raise ValueError("basis=periods needs periods=[reference, target]")
        if period_from is not None or period_to is not None:
            raise ValueError("basis=periods compares two periods; omit period_from/period_to")
        frequency = period_frequency(periods[0], "periods[0]")
        if period_frequency(periods[1], "periods[1]") != frequency:
            raise ValueError("Both periods must have the same frequency")
        if ordinal(periods[0], frequency) >= ordinal(periods[1], frequency):
            raise ValueError("The reference period must come before the target period")
    if window is not None and (
        isinstance(window, bool) or not isinstance(window, int) or not 2 <= window <= MAX_WINDOW
    ):
        raise ValueError(f"window must be an integer between 2 and {MAX_WINDOW}")
    if base_period is not None:
        if not isinstance(base_period, str) or len(base_period) > 7:
            raise ValueError("base_period must be a period such as 2020 or 2020-01")
        period_frequency(base_period, "base_period")
    bounds = [p for p in (period_from, period_to) if p is not None]
    if len(bounds) == 2:
        frequency = period_frequency(period_from, "period_from")
        if period_frequency(period_to, "period_to") != frequency:
            raise ValueError("period_from and period_to must have the same frequency")
        if ordinal(period_from, frequency) > ordinal(period_to, frequency):
            raise ValueError("period_from is after period_to")
    return CalculationSpec(
        operation=operation,
        basis=basis,
        periods=periods,
        window=window,
        base_period=base_period,
        decimals=decimals,
        filters=filters or {},
        period_from=period_from,
        period_to=period_to,
    )


def single(series_name: str, field: str, values: set) -> object:
    if len(values) != 1:
        shown = sorted(map(str, values))[:MAX_LISTED_VALUES]
        raise ValueError(
            f"{series_name} mixes {field} values {shown}; filter the selection to one so "
            "incompatible observations are never combined"
        )
    return next(iter(values))


@dataclass
class Prepared:
    frequency: str
    period_from: str
    period_to: str
    bounds: str
    candidates: list[int]
    series: list[tuple[dict[str, str], list[CalculationInput]]]
    unit: UnitSpec
    measure: tuple
    undated: int


def check_operation(spec: CalculationSpec, unit: UnitSpec, measure_type: str) -> None:
    operation = spec.operation
    if unit.kind == "unknown" or measure_type == "unknown":
        raise ValueError("The series has no reviewed unit or measure; nothing is calculated")
    if operation == "difference" and (unit.kind not in ADDITIVE or measure_type != "level"):
        hint = {
            "percent": "use pp_difference for percent values",
            "index": "use percent_change for index values",
        }.get(unit.kind, "differences need a level in persons, currency or terajoules")
        raise ValueError(f"difference is not defined for {measure_type} in {unit.kind}; {hint}")
    if operation == "pp_difference" and unit.kind != "percent":
        raise ValueError(
            f"pp_difference needs percent values, not {unit.kind}; use difference or percent_change"
        )
    if operation == "percent_change" and (
        unit.kind not in ADDITIVE | {"index"} or measure_type not in {"level", "index"}
    ):
        raise ValueError(
            f"percent_change needs a level or index, not {measure_type} in {unit.kind}; "
            "use pp_difference for rates and shares"
        )
    if operation == "moving_average" and measure_type in {"change", "unknown"}:
        raise ValueError(
            "moving_average is not defined for change rates: the mean of period changes "
            "is not the change over the window"
        )
    if operation == "rebase" and (
        unit.kind not in ADDITIVE | {"index"} or measure_type not in {"level", "index"}
    ):
        raise ValueError(f"rebase needs a level or index, not {measure_type} in {unit.kind}")


def prepare(spec: CalculationSpec, table) -> Prepared:
    candidates, _, _, _ = ObservationService.select(table, spec.filters, None, None)
    observations: list[Observation] = table.observations
    dated = [i for i in candidates if observations[i].period is not None]
    if not dated:
        raise ValueError("The selection has no dated observations")
    frequencies = {observations[i].period.frequency for i in dated}
    if len(frequencies) > 1:
        labels = sorted({observations[i].period.frequency_label for i in dated})
        raise ValueError(
            f"The selection mixes frequencies {labels}; filter Gözlem Sıklığı to one. "
            "Periods are never resampled."
        )
    frequency = next(iter(frequencies))
    for name, value in (
        ("period_from", spec.period_from),
        ("period_to", spec.period_to),
        ("base_period", spec.base_period),
        *((f"periods[{n}]", p) for n, p in enumerate(spec.periods or [])),
    ):
        if value is not None and period_frequency(value, name) != frequency:
            raise ValueError(f"{name} {value!r} is not a {frequency} period like the series")

    identifying = [
        concept
        for concept in table.varying
        if concept not in table.series_exclude
        and len({table.facets[i][concept] for i in dated}) > 1
    ]
    groups: dict[tuple[str, ...], list[int]] = {}
    for i in dated:
        groups.setdefault(tuple(table.facets[i][c] for c in identifying), []).append(i)
    if len(groups) > 1 and spec.operation != "rebase":
        options = {
            concept: sorted({table.facets[i][concept] for i in dated})[:10]
            for concept in identifying
        }
        raise ValueError(
            f"The selection holds {len(groups)} series; add filters so one series remains: "
            f"{options}"
        )
    if len(groups) > MAX_SERIES:
        raise ValueError(f"rebase takes at most {MAX_SERIES} series; narrow the filters")

    units, measures, adjustments = set(), set(), set()
    series = []
    for key, indexes in groups.items():
        dimensions = dict(zip(identifying, key, strict=True))
        name = f"Series {dimensions}" if dimensions else "The series"
        valid = [observations[i] for i in indexes if observations[i].status == "valid"]
        if not valid:
            raise ValueError(f"{name} has no valid observations")
        unit = single(
            name,
            "unit",
            {(o.unit.kind, o.unit.multiplier, o.unit.currency, o.unit.base_period) for o in valid},
        )
        measure = single(name, "measure", {(o.measure.type, o.measure.basis) for o in valid})
        adjustment = single(
            name, "adjustment", {(o.adjustment.seasonal, o.adjustment.calendar) for o in valid}
        )
        units.add(unit)
        measures.add(measure)
        adjustments.add(adjustment)
        series.append(
            (
                dimensions,
                [
                    CalculationInput(
                        id=o.id,
                        period=o.period.value,
                        status=o.status,
                        value=text(o.value) if o.status == "valid" else None,
                    )
                    for o in (observations[i] for i in indexes)
                ],
            )
        )
    # Rebased series may start from different base years; nothing else may differ.
    kind, multiplier, currency = single("The selected series", "unit", {u[:3] for u in units})
    measure = single("The selected series", "measure", measures)
    single("The selected series", "adjustment", adjustments)
    bases = {u[3] for u in units}
    unit = UnitSpec(
        kind=kind,
        multiplier=multiplier,
        currency=currency,
        base_period=next(iter(bases)) if len(bases) == 1 else None,
    )
    check_operation(spec, unit, measure[0])

    numbers = [ordinal(observations[i].period.value, frequency) for i in dated]
    if spec.basis == "periods":
        start = end = ordinal(spec.periods[1], frequency)
    else:
        # A requested bound is kept even when the series lies wholly on its other
        # side: the range then holds that one period, reported as missing, the way
        # get_tuik_observations reports coverage. It never reverses into an empty
        # range that would look complete.
        start = ordinal(spec.period_from, frequency) if spec.period_from else None
        end = ordinal(spec.period_to, frequency) if spec.period_to else None
        if start is None and end is None:
            start, end = min(numbers), max(numbers)
        elif start is None:
            start = min((n for n in numbers if n <= end), default=end)
        elif end is None:
            end = max((n for n in numbers if n >= start), default=start)
    if end - start + 1 > MAX_RANGE_PERIODS:
        raise ValueError(f"The period range spans more than {MAX_RANGE_PERIODS} periods")
    if spec.basis == "periods":
        bounds = "requested"
    elif spec.period_from and spec.period_to:
        bounds = "requested"
    elif spec.period_from:
        bounds = "from_requested"
    elif spec.period_to:
        bounds = "to_requested"
    else:
        bounds = "series_extent"
    return Prepared(
        frequency=frequency,
        period_from=period_label(start, frequency),
        period_to=period_label(end, frequency),
        bounds=bounds,
        candidates=dated,
        series=series,
        unit=unit,
        measure=measure,
        undated=len(candidates) - len(dated),
    )


def output_unit(spec: CalculationSpec, unit: UnitSpec) -> UnitSpec:
    if spec.operation == "percent_change":
        return UnitSpec(kind="percent")
    if spec.operation == "pp_difference":
        return UnitSpec(kind="percentage_points")
    if spec.operation == "rebase":
        return UnitSpec(kind="index", base_period=spec.base_period)
    if spec.operation == "difference":
        return unit.model_copy(update={"base_period": None})
    return unit


def output_measure(spec: CalculationSpec, measure: tuple) -> MeasureSpec:
    if spec.operation in LAGGED:
        return MeasureSpec(type="change", basis=spec.basis)
    if spec.operation == "rebase":
        return MeasureSpec(type="index")
    return MeasureSpec(type=measure[0], basis=measure[1])


def locator_text(observation: Observation) -> str:
    locator = observation.locator
    if locator.format == "csv":
        return f"record {locator.record}, column {locator.column}"
    return f"{locator.sheet}!{locator.cell}"


def size(response: CalculationResponse) -> int:
    return len(rendered(response.model_dump(mode="json")))


def inline_summary(response: CalculationResponse, ties: int) -> CalculationResponse:
    """The inline view of a saved calculation: counts, coverage and extrema values.

    Inputs and results stay in the artifact, and so does every tied period:
    `tie_count` says how many the inline `periods` list cut.
    """
    return response.model_copy(
        update={
            "inline_complete": False,
            "series": [
                item.model_copy(
                    update={
                        "inputs": [],
                        "results": [],
                        "extrema": [
                            e.model_copy(
                                update={"periods": e.periods[:ties], "inputs": e.inputs[:ties]}
                            )
                            for e in item.extrema
                        ],
                    }
                )
                for item in response.series
            ],
        }
    )


def csv_bytes(header: list[str], rows: list[list]) -> bytes:
    stream = io.StringIO()
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return ("﻿" + stream.getvalue()).encode()


class CalculationService:
    def __init__(self, catalog_service):
        self.catalog = catalog_service

    async def calculate(
        self,
        resource_id: str,
        operation: str,
        filters: dict[str, str] | None = None,
        basis: str | None = None,
        periods: list[str] | None = None,
        window: int | None = None,
        base_period: str | None = None,
        period_from: str | None = None,
        period_to: str | None = None,
        decimals: int = 2,
        snapshot_id: str | None = None,
        save: bool = False,
    ) -> CalculationResponse:
        spec = checked_spec(
            operation,
            filters,
            basis,
            periods,
            window,
            base_period,
            period_from,
            period_to,
            decimals,
        )
        validate_id(resource_id)
        held = unavailable(resource_id)
        if held is not None:
            raise SourceError(
                "unavailable", f"No reviewed observations for {held.domain}: {held.reason}", PORTAL
            )
        recipe = BY_RESOURCE.get(resource_id) or BY_WORKBOOK.get(resource_id)
        if recipe is None:
            raise SourceError(
                "unsupported",
                "Calculations need a reviewed observation recipe. Call get_tuik_observations "
                "without resource_id to list them.",
                PORTAL,
            )
        normalized: ObservationService = self.catalog.normalized
        try:
            recipe, resource, file, table = await normalized.table(resource_id, snapshot_id)
        except SchemaChanged as exc:
            raise SourceError(
                "schema_changed",
                "The published file no longer matches the reviewed recipe: "
                + "; ".join(exc.issues),
                PORTAL,
            ) from exc
        files = self.catalog.files
        prepared = await files.work(prepare, spec, table)
        series = await files.work(
            compute,
            spec,
            prepared.frequency,
            prepared.period_from,
            prepared.period_to,
            prepared.series,
        )
        coverage = (
            await files.work(
                ObservationService.coverage,
                table,
                prepared.candidates,
                prepared.frequency,
                prepared.period_from,
                prepared.period_to,
            )
        ).model_copy(update={"bounds": prepared.bounds})
        values = [value for item in series for value in item.results]
        counts = CalculationCounts(
            series=len(series),
            results=len(values),
            **{
                status: sum(1 for value in values if value.status == status)
                for status in ("valid", "missing", "undefined")
            },
        )
        issues = []
        if counts.missing or counts.undefined:
            issues.append(
                f"{counts.missing} results are missing and {counts.undefined} undefined; each "
                "result's reason names the input period. Nothing was filled or skipped."
            )
        if not coverage.complete and spec.operation in {"extrema", "moving_average", "rebase"}:
            issues.append(
                f"Only {coverage.valid} of {coverage.expected} series periods from "
                f"{coverage.period_from} to {coverage.period_to} have a valid value; "
                f"{spec.operation} describes those periods only, not the whole range."
            )
        if prepared.undated:
            issues.append(f"{prepared.undated} selected records have no valid period.")
        workbook = isinstance(recipe, WorkbookRecipe)
        response = CalculationResponse(
            source_url=resource.source_url if workbook else file.link.source_url,
            retrieved_at=file.retrieved_at,
            message=(
                "Derived values, not published by TÜİK. Every result lists its formula, "
                "input observation IDs, unit and rounding; recompute any of them from "
                "series[].inputs."
            ),
            resource_id=resource_id,
            recipe=ObservationService.reference(recipe),
            snapshot_id="sha256:" + file.digest,
            publication_date=resource.publication_date,
            source_scope="reviewed_cells" if workbook else "whole_export",
            spec=spec,
            formula=formula(spec, prepared.frequency),
            frequency=prepared.frequency,
            period_from=prepared.period_from,
            period_to=prepared.period_to,
            input_unit=prepared.unit,
            output_unit=output_unit(spec, prepared.unit),
            output_measure=output_measure(spec, prepared.measure),
            rounding=Rounding(decimals=spec.decimals),
            counts=counts,
            coverage=coverage,
            series=series,
            notes=[DERIVED, *recipe.notes],
            issues=issues,
        )
        budget = self.catalog.queries.limits.max_output_bytes
        if not save:
            if size(response) > budget:
                raise SourceError(
                    "query_limit_exceeded",
                    "The calculation with its inputs exceeds the output budget. Narrow "
                    "period_from/period_to, or pass save=true to keep the whole calculation "
                    "in an artifact.",
                    response.source_url,
                )
            return response
        # The artifact keeps the complete calculation; the budget bounds what is
        # returned inline, artifact summary included.
        manifest = await self.save(response, recipe, resource, file, table)
        response = response.model_copy(
            update={"artifact": self.catalog.artifacts.summary(manifest)}
        )
        for ties in (MAX_INLINE_TIES, 0):
            if size(response) <= budget:
                return response
            response = inline_summary(response, ties)
        if size(response) > budget:
            raise SourceError(
                "query_limit_exceeded",
                f"Even the summary of the saved calculation exceeds the output budget; read "
                f"calculation.json from artifact {manifest.artifact_id}.",
                response.source_url,
            )
        return response

    async def save(self, response: CalculationResponse, recipe, resource, file, table):
        by_id = {o.id: o for o in table.observations}
        bundle = response.model_dump(mode="json", exclude={"artifact"})
        input_rows, result_rows = [], []
        for item in response.series:
            dimensions = json.dumps(item.dimensions, ensure_ascii=False, sort_keys=True)
            for value in item.inputs:
                observation = by_id[value.id]
                input_rows.append(
                    [
                        dimensions,
                        value.id,
                        value.period,
                        value.status,
                        observation.value_text,
                        value.value or "",
                        locator_text(observation),
                    ]
                )
            for value in item.results:
                result_rows.append(
                    [
                        dimensions,
                        "result",
                        value.period,
                        value.status,
                        value.value or "",
                        value.rounded or "",
                        value.reason or "",
                        " ".join(value.inputs),
                    ]
                )
            for extremum in item.extrema:
                result_rows.append(
                    [
                        dimensions,
                        extremum.kind,
                        " ".join(extremum.periods),
                        "valid",
                        extremum.value,
                        extremum.rounded,
                        "",
                        " ".join(extremum.inputs),
                    ]
                )
        citation = (
            f"Kaynak: TÜİK · {resource.source_url[:120]} · Erişim: "
            f"{file.retrieved_at.date().isoformat()} · tuik-mcp hesaplaması"
        )
        readme = "\n".join(
            [
                f"Calculation: {response.spec.operation} ({response.recipe.id} "
                f"v{response.recipe.version})",
                f"Formula: {response.formula}",
                f"Periods: {response.period_from} to {response.period_to} ({response.frequency})",
                f"Input unit: {response.input_unit.model_dump(exclude_none=True)}",
                f"Output unit: {response.output_unit.model_dump(exclude_none=True)}",
                f"Rounding: divisions to {response.rounding.value_places} places, display to "
                f"{response.rounding.decimals}, {response.rounding.mode}",
                "",
                DERIVED,
                "calculation.json holds the specification and every input value. Recompute:",
                "  from tuik_mcp.calculations import recompute",
                "  recompute(json.load(open('calculation.json')))",
                "and compare with its series. inputs.csv gives each input's published text "
                "and CSV record or workbook cell.",
                "",
                *(f"Issue: {issue}" for issue in response.issues),
                f"Source: {citation}",
            ]
        ).encode()
        return await self.catalog.artifacts.create(
            kind="calculation",
            title=f"{response.spec.operation}: {recipe.title}"[:200],
            citation=citation,
            inputs=[
                ArtifactInput(
                    resource_id=response.resource_id,
                    source_url=resource.source_url,
                    file_url=file.link.source_url,
                    format=file.link.format,
                    retrieved_at=file.retrieved_at,
                    publication_date=resource.publication_date,
                    sha256=file.digest,
                    size=len(file.body),
                    selection=response.spec.model_dump(mode="json", exclude_none=True),
                )
            ],
            files=[
                PendingFile(
                    name="calculation.json",
                    role="output",
                    mime_type="application/json",
                    content=json.dumps(bundle, ensure_ascii=False, indent=2).encode(),
                ),
                PendingFile(
                    name="results.csv",
                    role="data",
                    mime_type="text/csv",
                    content=csv_bytes(
                        [
                            "series",
                            "kind",
                            "period",
                            "status",
                            "value",
                            "rounded",
                            "reason",
                            "inputs",
                        ],
                        result_rows,
                    ),
                ),
                PendingFile(
                    name="inputs.csv",
                    role="data",
                    mime_type="text/csv",
                    content=csv_bytes(
                        ["series", "id", "period", "status", "value_text", "value", "locator"],
                        input_rows,
                    ),
                ),
                PendingFile(
                    name="README.txt", role="notes", mime_type="text/plain", content=readme
                ),
            ],
            sources={
                0: (
                    f"source.{file.link.format}",
                    SOURCE_TYPES.get(file.link.format, "application/octet-stream"),
                    file.body,
                )
            },
            transformations=[
                f"{response.spec.operation}: {response.formula}.",
                "Inputs are normalized observations (tuik.observation/1); only status valid "
                "values enter a formula. Missing periods are reported, never filled.",
                f"Divisions rounded half-to-even to {response.rounding.value_places} places.",
            ],
            warnings=list(response.issues),
        )


__all__ = ["FORMULAS", "CalculationService"]
