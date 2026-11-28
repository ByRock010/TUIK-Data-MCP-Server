"""Deterministic series calculations over normalized observation values (#89).

`compute` is pure: it reads only the specification, the frequency, the output
range and the listed input values. The service and an auditor call the same
function, so a saved `calculation.json` recomputes to the same results with
`recompute`.

Periods are matched by calendar position, never by row order: the previous
period is t − 1 and the same period of the previous year is t − 12 months,
t − 4 quarters, t − 2 halves or t − 1 year. A missing period is reported, never
filled, skipped or extrapolated.
"""

import hashlib
import json
from collections import defaultdict
from decimal import (
    MAX_EMAX,
    MAX_PREC,
    MIN_EMIN,
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    localcontext,
)
from fractions import Fraction

from tuik_mcp.models.calculation import (
    VALUE_PLACES,
    CalculatedValue,
    CalculationInput,
    CalculationSpec,
    Extremum,
    SeriesResult,
)
from tuik_mcp.services.observations import ordinal, period_label

PERIODS_PER_YEAR = {"year": 1, "half": 2, "quarter": 4, "month": 12}
YEAR_AGO = {
    "year": "the previous year",
    "half": "the same half of the previous year",
    "quarter": "the same quarter of the previous year",
    "month": "the same month of the previous year",
}
FORMULAS = {
    "difference": "y[t] = x[t] − x[r]",
    "pp_difference": "y[t] = x[t] − x[r], in percentage points",
    "percent_change": "y[t] = (x[t] − x[r]) × 100 ÷ x[r]",
    "moving_average": "y[t] = (x[t−w+1] + … + x[t]) ÷ w",
    "extrema": "min and max of x[t] over the valid periods in range, with every tied period",
    "rebase": "y[t] = x[t] × 100 ÷ x[b]",
}
# Sums, differences and products of the inputs are exact, whatever the caller's
# decimal context: an inexact result would raise instead of rounding silently.
EXACT = Context(
    prec=MAX_PREC,
    rounding=ROUND_HALF_EVEN,
    Emax=MAX_EMAX,
    Emin=MIN_EMIN,
    traps=[InvalidOperation, DivisionByZero, Overflow, Inexact],
)


def text(value: Decimal) -> str:
    """Plain decimal notation without exponent or trailing zeros."""
    result = format(value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return "0" if result in ("-0", "") else result


def place(value: Fraction, places: int) -> Decimal:
    """`value` rounded once, half-to-even, to `places` decimals; exact otherwise.

    Integer arithmetic on the exact rational, so the result never depends on a
    decimal context's precision and is never rounded twice.
    """
    scaled = round(value * 10**places)  # round() of a Fraction is half-to-even.
    return Decimal(f"{scaled}E-{places}")


def rounded(value: Decimal, decimals: int) -> str:
    result = place(Fraction(value), decimals)
    return format(result.copy_abs() if result == 0 else result, "f")


def divided(numerator: Decimal, denominator: Decimal) -> Decimal:
    return place(Fraction(numerator) / Fraction(denominator), VALUE_PLACES)


def formula(spec: CalculationSpec, frequency: str) -> str:
    base = FORMULAS[spec.operation]
    if spec.basis == "previous_period":
        return f"{base}; r = t − 1 (the previous {frequency})"
    if spec.basis == "same_period_previous_year":
        lag = PERIODS_PER_YEAR[frequency]
        return f"{base}; r = t − {lag} ({YEAR_AGO[frequency]})"
    if spec.basis == "periods":
        return f"{base}; r = {spec.periods[0]}, t = {spec.periods[1]}"
    if spec.operation == "moving_average":
        return f"{base}; w = {spec.window}, trailing"
    if spec.operation == "rebase":
        return f"{base}; b = {spec.base_period}"
    return base


class Lookup:
    """Valid values of one series by period position, and the IDs each read touched."""

    def __init__(self, inputs: list[CalculationInput], frequency: str):
        self.frequency = frequency
        self.records: dict[int, list[CalculationInput]] = defaultdict(list)
        for item in inputs:
            self.records[ordinal(item.period, frequency)].append(item)
        self.used: set[str] = set()

    def get(self, number: int) -> tuple[Decimal | None, str | None, list[str]]:
        records = self.records.get(number, [])
        ids = [item.id for item in records]
        self.used.update(ids)
        label = period_label(number, self.frequency)
        if not records:
            return None, f"no source record for {label}", ids
        if len(records) > 1 or records[0].status == "duplicate":
            return None, f"{label} has duplicate source records", ids
        if records[0].status != "valid" or records[0].value is None:
            return None, f"{label} is {records[0].status}", ids
        return Decimal(records[0].value), None, ids


def result(label, value, decimals, ids, reason=None, status=None) -> CalculatedValue:
    if value is None:
        return CalculatedValue(period=label, status=status or "missing", reason=reason, inputs=ids)
    return CalculatedValue(
        period=label,
        status="valid",
        value=text(value),
        rounded=rounded(value, decimals),
        inputs=ids,
    )


def changes(spec: CalculationSpec, lookup: Lookup, targets, frequency) -> list[CalculatedValue]:
    values = []
    for target in targets:
        if spec.basis == "periods":
            reference = ordinal(spec.periods[0], frequency)
        elif spec.basis == "previous_period":
            reference = target - 1
        else:
            reference = target - PERIODS_PER_YEAR[frequency]
        label = period_label(target, frequency)
        current, problem, ids = lookup.get(target)
        prior, prior_problem, prior_ids = lookup.get(reference)
        ids = ids + prior_ids
        if current is None or prior is None:
            values.append(result(label, None, spec.decimals, ids, problem or prior_problem))
        elif spec.operation != "percent_change":
            values.append(result(label, current - prior, spec.decimals, ids))
        elif prior <= 0:
            sign = "zero" if prior == 0 else "negative"
            values.append(
                result(
                    label,
                    None,
                    spec.decimals,
                    ids,
                    f"reference value is {sign}; percent change is undefined",
                    "undefined",
                )
            )
        else:
            values.append(
                result(label, divided((current - prior) * 100, prior), spec.decimals, ids)
            )
    return values


def compute(
    spec: CalculationSpec,
    frequency: str,
    period_from: str,
    period_to: str,
    series: list[tuple[dict[str, str], list[CalculationInput]]],
) -> list[SeriesResult]:
    with localcontext(EXACT):
        return series_results(spec, frequency, period_from, period_to, series)


def series_results(spec, frequency, period_from, period_to, series) -> list[SeriesResult]:
    start, end = ordinal(period_from, frequency), ordinal(period_to, frequency)
    span = range(start, end + 1)
    output = []
    for dimensions, inputs in series:
        lookup = Lookup(inputs, frequency)
        values: list[CalculatedValue] = []
        extrema: list[Extremum] = []
        if spec.operation in {"difference", "percent_change", "pp_difference"}:
            targets = [ordinal(spec.periods[1], frequency)] if spec.basis == "periods" else span
            values = changes(spec, lookup, targets, frequency)
        elif spec.operation == "moving_average":
            for target in span:
                window = [lookup.get(n) for n in range(target - spec.window + 1, target + 1)]
                ids = [i for _, _, record_ids in window for i in record_ids]
                problem = next((reason for value, reason, _ in window if value is None), None)
                label = period_label(target, frequency)
                if problem is not None:
                    values.append(result(label, None, spec.decimals, ids, f"window: {problem}"))
                else:
                    total = sum((value for value, _, _ in window), Decimal(0))
                    values.append(
                        result(label, divided(total, Decimal(spec.window)), spec.decimals, ids)
                    )
        elif spec.operation == "rebase":
            base, problem, base_ids = lookup.get(ordinal(spec.base_period, frequency))
            if base is None or base <= 0:
                reason = problem or f"{spec.base_period} is {'zero' if base == 0 else 'negative'}"
                raise ValueError(
                    f"Cannot rebase{' ' + str(dimensions) if dimensions else ''} to "
                    f"{spec.base_period}: {reason}"
                )
            for target in span:
                label = period_label(target, frequency)
                current, problem, ids = lookup.get(target)
                ids = list(dict.fromkeys(ids + base_ids))
                values.append(
                    result(
                        label,
                        None if current is None else divided(current * 100, base),
                        spec.decimals,
                        ids,
                        problem,
                    )
                )
        else:
            found = []
            for target in span:
                value, _, ids = lookup.get(target)
                if value is not None:
                    found.append((value, period_label(target, frequency), ids))
            for kind, chosen in (("min", min), ("max", max)):
                if not found:
                    break
                best = chosen(value for value, _, _ in found)
                ties = [(label, ids) for value, label, ids in found if value == best]
                extrema.append(
                    Extremum(
                        kind=kind,
                        value=text(best),
                        rounded=rounded(best, spec.decimals),
                        periods=[label for label, _ in ties],
                        inputs=[i for _, ids in ties for i in ids],
                        tie_count=len(ties),
                    )
                )
        used = [item for item in inputs if item.id in lookup.used]
        used.sort(key=lambda item: ordinal(item.period, frequency))
        output.append(
            SeriesResult(dimensions=dimensions, inputs=used, results=values, extrema=extrema)
        )
    return output


def calculation_id(
    resource_id: str, recipe_id: str, recipe_version: int, snapshot_id: str, spec: CalculationSpec
) -> str:
    """Stable ID of one calculation: a digest of everything that determines it."""
    identity = {
        "resource_id": resource_id,
        "recipe": [recipe_id, recipe_version],
        "snapshot_id": snapshot_id,
        "spec": spec.model_dump(mode="json"),
    }
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "calc:" + hashlib.sha256(canonical.encode()).hexdigest()[:16]


def recompute(bundle: dict) -> list[dict]:
    """Recompute the series of a saved `calculation.json` from its inputs alone."""
    spec = CalculationSpec.model_validate(bundle["spec"])
    series = [
        (
            item["dimensions"],
            [CalculationInput.model_validate(value) for value in item["inputs"]],
        )
        for item in bundle["series"]
    ]
    results = compute(spec, bundle["frequency"], bundle["period_from"], bundle["period_to"], series)
    return [item.model_dump(mode="json") for item in results]
