from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


def now() -> datetime:
    return datetime.now(UTC)


class Provenance(BaseModel):
    source_url: str
    retrieved_at: datetime = Field(default_factory=now)
    publisher: str = "Türkiye İstatistik Kurumu (TÜİK)"


class ErrorResponse(Provenance):
    status: Literal["error"] = "error"
    code: str
    message: str


class SearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=10, ge=1, le=50, strict=True)

    @field_validator("query")
    @classmethod
    def clean_query(cls, value: str) -> str:
        import unicodedata

        value = unicodedata.normalize("NFC", value).strip()
        if not value or any(unicodedata.category(c).startswith("C") for c in value):
            raise ValueError("Query must contain text and no control characters")
        return value
