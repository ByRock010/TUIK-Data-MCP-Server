"""Bounded, conservative column profiles of parsed tables (#51).

Values are counted exactly as published. Type hints are inferred from observed
strings only: ambiguous separators and non-ISO dates are never resolved, and
placeholder symbols are reported without being read as zero or missing.
"""

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date

import pydantic_core

from tuik_mcp.config import Settings
from tuik_mcp.utils.normalization import turkish_casefold

from .models import ColumnProfile, ObservedValue, ParsedTable, TableProfile

MAX_VALUE_CHARS = 200
# Symbols statistical tables commonly print instead of a number; meaning is not interpreted.
PLACEHOLDERS = frozenset({"-", "–", "—", ".", "..", "...", "…", ":", "x", "X", "*"})
INTEGER = re.compile(r"[+-]?\d+%?")
DOT_DECIMAL = re.compile(r"[+-]?\d*\.(\d+)%?")
COMMA_DECIMAL = re.compile(r"[+-]?\d*,(\d+)%?")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
PERIOD = re.compile(
    r"\d{4}-?(?:Q[1-4]|S[12]|H[12]|M(?:0[1-9]|1[0-2])|W(?:0[1-9]|[1-4]\d|5[0-3])|0[1-9]|1[0-2])"
)
TIME_WORDS = ("yıl", "yil", "year", "dönem", "donem", "period", "zaman", "time", "tarih")


@dataclass(frozen=True)
class InspectLimits:
    max_rows: int = 100_000
    max_columns: int = 100
    max_distinct: int = 10_000
    max_output_bytes: int = 64 * 1024

    @classmethod
    def from_settings(cls, settings: Settings) -> "InspectLimits":
        return cls(
            **{name: getattr(settings, "inspect_" + name) for name in cls.__dataclass_fields__}
        )


class UnknownColumnsError(ValueError):
    def __init__(self, names: list[str]):
        super().__init__(f"Unknown column names: {names}")
        self.names = names


def shape(value: str) -> str:
    """Classify one non-empty value without changing it."""
    text = value.strip()
    if text in PLACEHOLDERS:
        return "placeholder"
    if INTEGER.fullmatch(text):
        digits = text.lstrip("+-").rstrip("%")
        if len(digits) > 1 and digits.startswith("0"):
            return "string"  # Zero-padded codes such as "01" are identifiers.
        return "year" if len(digits) == 4 and 1900 <= int(digits) <= 2100 else "integer"
    if match := DOT_DECIMAL.fullmatch(text):
        return "dot3" if len(match[1]) == 3 else "dot"
    if match := COMMA_DECIMAL.fullmatch(text):
        return "comma3" if len(match[1]) == 3 else "comma"
    if ISO_DATE.fullmatch(text):
        try:
            date.fromisoformat(text)
            return "date"
        except ValueError:
            return "string"
    if PERIOD.fullmatch(text):
        return "period"
    return "string"


def time_header(name: str) -> bool:
    tokens = re.findall(r"\w+", turkish_casefold(name))
    return any(t == "ay" or t == "month" or t.startswith(TIME_WORDS) for t in tokens)


def type_hint(shapes: set[str], name: str) -> tuple[str, str | None]:
    """A conservative hint and, for numbers, the observed decimal separator."""
    shapes = shapes - {"placeholder"}
    numeric = {"integer", "year", "dot", "dot3", "comma", "comma3"}
    if not shapes:
        return "unknown", None
    if shapes == {"string"}:
        return "string", None
    if shapes == {"date"}:
        return "date", None
    if shapes <= {"period", "year"} and "period" in shapes:
        return "period", None
    if shapes == {"year"}:
        # Bare years are measurements unless the header names a time dimension.
        return ("period", None) if time_header(name) else ("number", None)
    if shapes <= numeric:
        dots, commas = shapes & {"dot", "dot3"}, shapes & {"comma", "comma3"}
        if dots and commas:
            return "mixed", None
        decimals = dots or commas
        if not decimals:
            return "number", None
        # "1.234" alone may be a thousands separator or a decimal point: do not guess.
        if decimals in ({"dot3"}, {"comma3"}):
            return "number", "ambiguous"
        return "number", "." if dots else ","
    return "mixed", None


def observed(counter: Counter, order: dict[str, int], limit: int) -> list[ObservedValue]:
    ranked = sorted(counter.items(), key=lambda item: (-item[1], order[item[0]]))[:limit]
    return [
        ObservedValue(
            value=value[:MAX_VALUE_CHARS], count=count, truncated=len(value) > MAX_VALUE_CHARS
        )
        for value, count in ranked
    ]


def profile_column(
    name: str,
    label_en: str | None,
    values: list[str],
    *,
    complete: bool,
    max_values: int,
    max_distinct: int,
) -> tuple[ColumnProfile, Counter, dict[str, int]]:
    counter, order, shapes = Counter(), {}, set()
    placeholders, empty, overflow = Counter(), 0, False
    for value in values:
        if not value.strip():
            empty += 1
            continue
        kind = shape(value)
        shapes.add(kind)
        if kind == "placeholder":
            placeholders[value] += 1
        if value in counter or len(counter) < max_distinct:
            order.setdefault(value, len(order))
            counter[value] += 1
        else:
            overflow = True
    hint, separator = type_hint(shapes, name)
    listed = observed(counter, order, max_values)
    profile = ColumnProfile(
        name=name,
        label_en=label_en,
        type_hint=hint,
        decimal_separator=separator,
        non_empty=len(values) - empty,
        empty=empty,
        placeholders=dict(placeholders),
        distinct_count=None if overflow else len(counter),
        distinct_exact=complete and not overflow,
        values=listed,
        # Every distinct value of the whole table is listed: needs a full scan.
        values_complete=complete and not overflow and len(listed) == len(counter),
    )
    return profile, counter, order


def size(profiles: list[ColumnProfile]) -> int:
    """UTF-8 bytes of the profiles as FastMCP renders tool results (indented JSON)."""
    payload = {"columns": [p.model_dump(mode="json") for p in profiles]}
    return len(pydantic_core.to_json(payload, indent=2))


def profile_table(
    table: ParsedTable,
    *,
    max_values: int,
    limits: InspectLimits,
    columns: list[str] | None = None,
) -> TableProfile:
    """Profile the first `max_rows` rows of a parsed table within an output budget."""
    if columns is not None:
        unknown = [name for name in columns if name not in table.columns]
        if unknown:
            raise UnknownColumnsError(unknown)
        selected = list(dict.fromkeys(columns))
    else:
        selected = table.columns[: limits.max_columns]
    labels = dict(zip(table.columns, table.columns_en, strict=False))
    rows = table.rows[: limits.max_rows]
    complete = len(rows) == table.total_rows
    profiles, counters = [], []
    for name in selected:
        profile, counter, order = profile_column(
            name,
            labels.get(name) or None,
            [row.get(name, "") for row in rows],
            complete=complete,
            max_values=max_values,
            max_distinct=limits.max_distinct,
        )
        profiles.append(profile)
        counters.append((counter, order))
    warnings = list(table.warnings)
    listed = max_values
    # Shrink listed values first, then drop trailing columns, to bound the response.
    while profiles and size(profiles) > limits.max_output_bytes and listed > 0:
        listed //= 2
        for profile, (counter, order) in zip(profiles, counters, strict=True):
            profile.values = observed(counter, order, listed)
            profile.values_complete = profile.values_complete and len(profile.values) == len(
                counter
            )
    if listed < max_values:
        warnings.append(
            f"The output budget limited listed values to {listed} per column; "
            "request fewer columns to see more values."
        )
    # Keep the longest prefix of columns that fits; even a single profile may not.
    fits, high = 0, len(profiles)
    while fits < high:
        middle = (fits + high + 1) // 2
        if size(profiles[:middle]) <= limits.max_output_bytes:
            fits = middle
        else:
            high = middle - 1
    dropped = len(profiles) - fits
    del profiles[fits:]
    if dropped:
        warnings.append(f"The output budget omitted {dropped} trailing column profile(s).")
    # A caller-chosen subset is not truncation; budget-omitted columns are.
    truncated = len(profiles) < len(selected) or (
        columns is None and len(selected) < len(table.columns)
    )
    if columns is None and len(selected) < len(table.columns):
        warnings.append(
            f"Only the first {len(selected)} of {len(table.columns)} columns were profiled; "
            "pass columns to profile others."
        )
    return TableProfile(
        sheet=table.sheet,
        title=table.title,
        title_en=table.title_en,
        unit=table.unit,
        notes=table.notes,
        source_note=table.source_note,
        extraction=table.extraction,
        layout=table.layout,
        warnings=warnings,
        total_rows=table.total_rows,
        rows_examined=len(rows),
        complete=complete,
        column_count=len(table.columns),
        columns=profiles,
        columns_truncated=truncated,
    )
