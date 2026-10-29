from typing import Literal

from pydantic import BaseModel, Field


class ParsedTable(BaseModel):
    sheet: str | None = None
    title: str | None = None
    title_en: str | None = None
    unit: str | None = None
    columns: list[str] = Field(default_factory=list)
    columns_en: list[str] = Field(default_factory=list)
    rows: list[dict[str, str]] = Field(default_factory=list)
    total_rows: int = Field(default=0, ge=0)
    notes: list[str] = Field(default_factory=list)
    source_note: str | None = None
    extraction: Literal["csv", "xls", "xlsx", "pdf"]
    # "raw" when no header/data layout was recognized and rows mirror the source grid.
    layout: Literal["table", "raw"] = "table"
    warnings: list[str] = Field(default_factory=list)


class DocumentPage(BaseModel):
    page: int = Field(ge=1)
    heading: str | None = None
    text: str


class DocumentChunk(DocumentPage):
    """A text passage retaining its one-based source page for citation."""


class ParsedDocument(BaseModel):
    page_count: int = Field(ge=1)
    pages: list[DocumentPage]


class ObservedValue(BaseModel):
    value: str
    count: int = Field(ge=1)
    # A long value is shortened for display and cannot be used as an exact filter.
    truncated: bool = False


class ColumnProfile(BaseModel):
    """Observed contents of one column; never an official dimension or code list."""

    name: str
    label_en: str | None = None
    type_hint: Literal["string", "number", "date", "period", "mixed", "unknown"]
    type_hint_source: Literal["inferred"] = "inferred"
    decimal_separator: Literal[".", ",", "ambiguous"] | None = None
    non_empty: int = Field(ge=0)
    empty: int = Field(ge=0)
    placeholders: dict[str, int] = Field(default_factory=dict)
    distinct_count: int | None = Field(default=None, ge=0)
    distinct_exact: bool = False
    values: list[ObservedValue] = Field(default_factory=list)
    values_complete: bool = False


class TableProfile(BaseModel):
    sheet: str | None = None
    title: str | None = None
    title_en: str | None = None
    unit: str | None = None
    notes: list[str] = Field(default_factory=list)
    source_note: str | None = None
    extraction: Literal["csv", "xls", "xlsx", "pdf"]
    layout: Literal["table", "raw"] = "table"
    warnings: list[str] = Field(default_factory=list)
    total_rows: int = Field(ge=0)
    rows_examined: int = Field(ge=0)
    complete: bool
    column_count: int = Field(ge=0)
    columns: list[ColumnProfile] = Field(default_factory=list)
    columns_truncated: bool = False
