"""Small, non-executable query language for one retained CSV/Excel table."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Column = Annotated[str, Field(min_length=1, max_length=200)]
Value = Annotated[str, Field(max_length=200)]
PeriodFormat = Literal["year", "month", "quarter", "half", "week"]


class QueryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Equality(QueryModel):
    op: Literal["eq"]
    column: Column
    value: Value


class Membership(QueryModel):
    op: Literal["in"]
    column: Column
    values: list[Value] = Field(min_length=1, max_length=100)


class Comparison(QueryModel):
    value_type: Literal["text", "number", "date", "period"] = "text"
    decimal_separator: Literal[".", ","] | None = None
    period_format: PeriodFormat | None = None

    @model_validator(mode="after")
    def formats_match_type(self):
        if self.decimal_separator is not None and self.value_type != "number":
            raise ValueError("decimal_separator applies only to number comparisons")
        if (self.period_format is not None) != (self.value_type == "period"):
            raise ValueError("period comparisons require period_format; other types omit it")
        return self


class Range(Comparison):
    op: Literal["range"]
    column: Column
    value_type: Literal["number", "date", "period"]
    minimum: Value | None = None
    maximum: Value | None = None

    @model_validator(mode="after")
    def has_bound(self):
        if self.minimum is None and self.maximum is None:
            raise ValueError("range requires minimum and/or maximum (inclusive)")
        return self


Predicate = Annotated[Equality | Membership | Range, Field(discriminator="op")]


class SortKey(Comparison):
    column: Column
    direction: Literal["asc", "desc"] = "asc"
    missing: Literal["first", "last"] = "last"


class TableQuery(QueryModel):
    columns: list[Column] | None = Field(default=None, min_length=1, max_length=50)
    predicates: list[Predicate] = Field(default_factory=list, max_length=20)
    sort: list[SortKey] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def unique_columns(self):
        if self.columns is not None and len(set(self.columns)) != len(self.columns):
            raise ValueError("projection columns must be unique")
        keys = [key.column for key in self.sort]
        if len(set(keys)) != len(keys):
            raise ValueError("sort columns must be unique")
        return self
