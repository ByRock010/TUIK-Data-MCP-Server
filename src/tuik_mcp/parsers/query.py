"""Deterministic filtering/sorting; inferred hints never change source strings."""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pydantic_core

from tuik_mcp.models.query import Comparison, Equality, Membership, TableQuery

from .models import ParsedTable
from .profile import PLACEHOLDERS, shape


class QueryError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class QueryLimits:
    max_rows: int = 100_000
    max_cells: int = 2_000_000
    max_sort_rows: int = 50_000
    max_snapshot_bytes: int = 8 * 1024 * 1024
    max_output_bytes: int = 64 * 1024

    @classmethod
    def from_settings(cls, settings):
        return cls(**{key: getattr(settings, "query_" + key) for key in cls.__dataclass_fields__})


def missing(value: str) -> bool:
    return not value.strip() or value.strip() in PLACEHOLDERS


def converter(spec: Comparison, values: list[str], column: str):
    separator = spec.decimal_separator
    if spec.value_type == "number" and separator is None:
        shapes = {shape(value) for value in values if not missing(value)}
        dots, commas = shapes & {"dot", "dot3"}, shapes & {"comma", "comma3"}
        if dots and commas or dots == {"dot3"} or commas == {"comma3"}:
            raise QueryError(
                "invalid_query", f"Ambiguous numeric notation in {column!r}; set decimal_separator."
            )
        separator = "," if commas else "."

    def convert(value: str):
        if missing(value):
            return None
        text = value.strip()
        try:
            if spec.value_type == "text":
                return value  # Deterministic Unicode code-point order, no locale guessing.
            if len(text) > 200:
                raise ValueError("typed value exceeds 200 characters")
            if spec.value_type == "number":
                point = re.escape(separator)
                if not re.fullmatch(rf"[+-]?(?:\d+(?:{point}\d+)?|{point}\d+)", text):
                    raise ValueError(
                        "use plain numbers without grouping, exponents or percent signs"
                    )
                return Decimal(text.replace(",", "."))
            if spec.value_type == "date":
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
                    raise ValueError("use ISO YYYY-MM-DD")
                return date.fromisoformat(text).toordinal()
            patterns = {
                "year": r"(\d{4})",
                "month": r"(\d{4})(?:-|M)(0[1-9]|1[0-2])",
                "quarter": r"(\d{4})-?Q([1-4])",
                "half": r"(\d{4})-?[HS]([12])",
                "week": r"(\d{4})-?W(0[1-9]|[1-4]\d|5[0-3])",
            }
            match = re.fullmatch(patterns[spec.period_format], text)
            if match is None:
                raise ValueError(f"use the declared {spec.period_format} period format")
            parts = tuple(int(part) for part in match.groups())
            date(parts[0], 1, 1)
            if spec.period_format == "week":
                date.fromisocalendar(parts[0], parts[1], 1)
            return parts
        except ValueError as exc:
            raise QueryError(
                "invalid_query",
                f"Column {column!r} has incompatible {spec.value_type} value {value[:80]!r}: {exc}",
            ) from exc

    return convert


def evaluate(table: ParsedTable, query: TableQuery, filters: dict[str, str], limits: QueryLimits):
    if table.total_rows != len(table.rows):
        raise QueryError("query_limit_exceeded", "A complete table is required; no partial count.")
    if (
        table.total_rows > limits.max_rows
        or table.total_rows * len(table.columns) > limits.max_cells
    ):
        raise QueryError("query_limit_exceeded", "Table exceeds the query row/cell scan budget.")
    referenced = (
        set(query.columns or table.columns)
        | set(filters)
        | {p.column for p in query.predicates}
        | {key.column for key in query.sort}
    )
    unknown = referenced - set(table.columns)
    if unknown:
        raise QueryError("invalid_columns", f"Unknown exact column names: {sorted(unknown)}")
    overlap = set(filters) & {p.column for p in query.predicates}
    if overlap:
        raise QueryError(
            "invalid_query",
            f"Use either legacy filters or predicates for columns: {sorted(overlap)}",
        )
    converted = {}

    def typed(spec):
        key = spec.column, spec.value_type, spec.decimal_separator, spec.period_format
        if key not in converted:
            values = [row[spec.column] for row in table.rows]
            convert = converter(spec, values, spec.column)
            converted[key] = [convert(value) for value in values], convert
        return converted[key]

    checks = []
    for predicate in query.predicates:
        if isinstance(predicate, Equality):
            checks.append(lambda i, p=predicate: table.rows[i][p.column] == p.value)
        elif isinstance(predicate, Membership):
            allowed = set(predicate.values)
            checks.append(
                lambda i, p=predicate, allowed=allowed: table.rows[i][p.column] in allowed
            )
        else:
            values, convert = typed(predicate)
            low = convert(predicate.minimum) if predicate.minimum is not None else None
            high = convert(predicate.maximum) if predicate.maximum is not None else None
            if (
                predicate.minimum is not None
                and low is None
                or (predicate.maximum is not None and high is None)
            ):
                raise QueryError("invalid_query", "Range bounds cannot be empty or placeholders.")
            if low is not None and high is not None and low > high:
                raise QueryError("invalid_query", "range minimum must not exceed maximum")
            checks.append(
                lambda i, v=values, lo=low, hi=high: (
                    v[i] is not None and (lo is None or v[i] >= lo) and (hi is None or v[i] <= hi)
                )
            )
    # Validate sort types over the full source, even if invalid rows would be filtered out.
    sorts = [(key, typed(key)[0]) for key in query.sort]
    indexes = [
        i
        for i, row in enumerate(table.rows)
        if all(row[key] == value for key, value in filters.items())
        and all(check(i) for check in checks)
    ]
    if sorts and len(indexes) > limits.max_sort_rows:
        raise QueryError("query_limit_exceeded", "Matched rows exceed the sorting-memory budget.")
    # Stable least-significant-key passes retain original source positions for ties.
    for key, values in reversed(sorts):
        absent = [i for i in indexes if values[i] is None]
        present = sorted(
            (i for i in indexes if values[i] is not None),
            key=lambda i: values[i],
            reverse=key.direction == "desc",
        )
        indexes = absent + present if key.missing == "first" else present + absent
    columns = query.columns if query.columns is not None else table.columns
    rows, size = [], 0
    for i in indexes:
        row = {name: table.rows[i][name] for name in columns}
        size += len(pydantic_core.to_json(row))
        if size > limits.max_snapshot_bytes:
            raise QueryError(
                "query_limit_exceeded", "Projected result exceeds the snapshot budget."
            )
        rows.append(row)
    return columns, rows
