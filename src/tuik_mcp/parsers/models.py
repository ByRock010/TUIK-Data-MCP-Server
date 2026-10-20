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
    warnings: list[str] = Field(default_factory=list)


class DocumentPage(BaseModel):
    page: int = Field(ge=1)
    heading: str | None = None
    text: str


class DocumentChunk(DocumentPage):
    """A text passage retaining its one-based source page for citation."""
