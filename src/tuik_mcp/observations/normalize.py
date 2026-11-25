"""Turn one CSV snapshot into observations with a reviewed recipe (#87).

The whole file is read or nothing is returned: a scan budget, malformed record or
changed column set raises instead of producing a partial series. Every record
keeps its one-based CSV record number and published value text.
"""

import csv
import io
import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from tuik_mcp.models.observation import (
    Adjustments,
    CsvLocator,
    Geography,
    Measure,
    Observation,
    Period,
    Unit,
)
from tuik_mcp.parsers.errors import ParseError, ParseLimitError
from tuik_mcp.parsers.profile import PLACEHOLDERS

from .geography import NATIONAL
from .recipes import (
    CONFIDENTIAL,
    EMPTY_ATTRIBUTES,
    FREQUENCIES,
    RELEASED,
    STATUS_COLUMNS,
    Recipe,
)

PERIODS = {
    "year": re.compile(r"[0-9]{4}"),
    "half": re.compile(r"[0-9]{4}-H[12]"),
    "quarter": re.compile(r"[0-9]{4}-Q[1-4]"),
    "month": re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])"),
}
NUMBERS = {
    ",": re.compile(r"-?[0-9]+(,[0-9]+)?"),
    ".": re.compile(r"-?[0-9]+(\.[0-9]+)?"),
}
# A trailing code the export copies from the first record, e.g. `Zaman (2015-01)`.
CODE_SUFFIX = re.compile(r"(?P<concept>.+) \([^()]*\)")


class SchemaChanged(Exception):
    def __init__(self, issues: list[str]):
        super().__init__("; ".join(issues))
        self.issues = issues


@dataclass
class Normalized:
    columns: dict[str, str]
    observations: list[Observation]
    # Published value of every descriptive concept, parallel to `observations`.
    facets: list[dict[str, str]]
    constant: dict[str, str]
    varying: dict[str, list[str]]

    @property
    def source_records(self) -> int:
        return len(self.observations)


def match_columns(recipe: Recipe, header: list[str]) -> dict[str, str]:
    """Map each recipe concept to its header; refuse any other column set."""
    required = recipe.concepts()
    known = set(required) | set(STATUS_COLUMNS) | set(EMPTY_ATTRIBUTES)
    mapping: dict[str, str] = {}
    issues = []
    for name in header:
        match = CODE_SUFFIX.fullmatch(name)
        if name in known:
            concept = name
        elif match and match["concept"] in known:
            concept = match["concept"]
        else:
            issues.append(f"unexpected column {name!r}")
            continue
        if concept in mapping:
            issues.append(f"columns {mapping[concept]!r} and {name!r} are both {concept!r}")
        mapping[concept] = name
    issues += [f"missing column {concept!r}" for concept in required if concept not in mapping]
    if issues:
        raise SchemaChanged(issues)
    return mapping


def read_csv(
    body: bytes, delimiter: str, max_rows: int, max_cells: int
) -> tuple[list[str], list[list[str]]]:
    try:
        text = body.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text), delimiter=delimiter, strict=True)
        header = next(reader, None)
        if not header or len(set(header)) != len(header) or any(not name for name in header):
            raise ParseError("Missing or duplicate CSV columns")
        records = []
        for count, record in enumerate(reader, 1):
            if count > max_rows or count * len(header) > max_cells:
                raise ParseLimitError("CSV exceeds the observation scan budget; no partial result")
            if len(record) != len(header):
                raise ParseError(f"CSV record {count + 1} has {len(record)} fields")
            records.append(record)
        return header, records
    except (UnicodeError, csv.Error) as exc:
        raise ParseError("TÜİK returned unsupported or malformed CSV") from exc


def normalize(
    recipe: Recipe,
    body: bytes,
    digest: str,
    *,
    max_rows: int = 100_000,
    max_cells: int = 2_000_000,
) -> Normalized:
    header, records = read_csv(body, recipe.delimiter, max_rows, max_cells)
    columns = match_columns(recipe, header)
    position = {concept: header.index(name) for concept, name in columns.items()}
    descriptive = [c for c in recipe.concepts() if c not in {recipe.period, recipe.value}]
    number = NUMBERS[recipe.decimal_separator]

    seen: dict[str, dict[str, None]] = {concept: {} for concept in descriptive}
    for record in records:
        for concept in descriptive:
            seen[concept].setdefault(record[position[concept]], None)
    constant = {c: next(iter(values)) for c, values in seen.items() if len(values) == 1}
    varying = {c: list(values) for c, values in seen.items() if len(values) > 1}
    shown = [c for c in recipe.dimensions if c in varying]

    observations, facets = [], []
    identities: dict[tuple[str, ...], list[int]] = defaultdict(list)
    for index, record in enumerate(records):

        def get(concept: str | None, record=record) -> str:
            return record[position[concept]] if concept in position else ""

        number_of_record = index + 2
        problems: list[tuple[str, str]] = []

        for attribute in EMPTY_ATTRIBUTES:
            if get(attribute).strip():
                problems.append(
                    ("unmapped", f"{attribute} is {get(attribute)!r}; the recipe expects none")
                )

        period = None
        frequency_label, period_text = get(recipe.frequency), get(recipe.period)
        frequency = FREQUENCIES.get(frequency_label)
        if frequency is None:
            problems.append(("unmapped", f"unknown frequency {frequency_label!r}"))
        elif not PERIODS[frequency].fullmatch(period_text):
            problems.append(("invalid", f"period {period_text!r} is not a {frequency} period"))
        else:
            period = Period(
                value=period_text,
                frequency=frequency,
                frequency_label=frequency_label,
                source="source",
            )

        geography = None
        place = get(recipe.geography)
        if place in recipe.geographies:
            code, level = recipe.geographies[place]
            geography = Geography(label=place, code=code, level=level, source="recipe")
        else:
            problems.append(("unmapped", f"geography {place!r} has no reviewed mapping"))
        if recipe.coverage is not None and get(recipe.coverage) not in NATIONAL:
            problems.append(("unmapped", f"coverage {get(recipe.coverage)!r} is not national"))

        measure = unit = None
        label = get(recipe.measure)
        rule = recipe.measures.get(label)
        unit_label = get(recipe.unit)
        if rule is None:
            problems.append(("unmapped", f"measure {label!r} has no reviewed mapping"))
        elif unit_label and unit_label not in recipe.units:
            problems.append(("unmapped", f"unit {unit_label!r} has no reviewed mapping"))
        else:
            base = get(recipe.base_period) if rule.type == "index" else None
            if rule.type == "index" and not base:
                problems.append(("unmapped", "index without a published base period"))
            measure = Measure(type=rule.type, label=label, basis=rule.basis, source="recipe")
            unit = Unit(
                kind=rule.unit,
                label=unit_label or None,
                multiplier=rule.multiplier,
                currency=recipe.units.get(unit_label),
                base_period=base or None,
                source="recipe",
            )

        if recipe.adjustment is not None:
            adjustment_label = get(recipe.adjustment)
            pair = recipe.adjustments.get(adjustment_label)
            if pair is None:
                problems.append(
                    ("unmapped", f"adjustment {adjustment_label!r} has no reviewed mapping")
                )
                pair = ("unknown", "unknown")
            adjustment = Adjustments(
                seasonal=pair[0], calendar=pair[1], label=adjustment_label, source="source"
            )
        else:
            seasonal, calendar = recipe.fixed_adjustment
            known = recipe.fixed_adjustment != ("unknown", "unknown")
            adjustment = Adjustments(
                seasonal=seasonal, calendar=calendar, source="recipe" if known else "unknown"
            )

        statuses = {c: get(c) for c in STATUS_COLUMNS if get(c)}
        confidential = any(value in CONFIDENTIAL for value in statuses.values())
        for value in statuses.values():
            if value not in RELEASED | CONFIDENTIAL:
                problems.append(("unmapped", f"observation status {value!r} is not reviewed"))

        text = get(recipe.value)
        stripped = text.strip()
        value = None
        if confidential:
            outcome = ("confidential", "published as confidential")
            if stripped:
                outcome = ("invalid", "value published with a confidentiality flag")
        elif not stripped:
            outcome = ("missing", "empty in the source")
        elif stripped in PLACEHOLDERS:
            outcome = ("missing", f"source marker {stripped!r}")
        elif number.fullmatch(stripped):
            outcome = ("valid", None)
            value = Decimal(stripped.replace(",", "."))
        else:
            outcome = ("invalid", f"{stripped!r} is not a number in the recipe's notation")
        status, reason = problems[0] if problems else outcome
        if status != "valid":
            value = None

        facet = {concept: get(concept) for concept in descriptive}
        observations.append(
            Observation(
                id=f"obs:{digest[:16]}:{number_of_record}",
                status=status,
                reason=reason,
                value_text=text,
                value=value,
                locator=CsvLocator(record=number_of_record, column=columns[recipe.value]),
                period=period,
                geography=geography,
                unit=unit,
                measure=measure,
                adjustment=adjustment,
                dimensions={concept: facet[concept] for concept in shown},
                source_status=statuses,
            )
        )
        facets.append(facet)
        if status in {"valid", "missing", "confidential"}:
            key = tuple(facet[c] for c in descriptive if c not in recipe.identity_exclude)
            identities[(*key, period_text)].append(index)

    for indexes in identities.values():
        if len(indexes) > 1:
            records_text = ", ".join(str(i + 2) for i in indexes[:5])
            for index in indexes:
                observations[index] = observations[index].model_copy(
                    update={
                        "status": "duplicate",
                        "value": None,
                        "reason": f"same period and dimensions in records {records_text}",
                    }
                )
    return Normalized(columns, observations, facets, constant, varying)
