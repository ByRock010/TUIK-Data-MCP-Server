"""Validated v0.3 metadata documents shared by crawling and indexing (#14)."""

import re
from datetime import date, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from tuik_mcp.models.common import now
from tuik_mcp.utils.normalization import official_url, validate_id


def index_resource_id(value: str) -> str:
    validate_id(value)
    if not value.startswith(("flow:", "press:")) or ":file:" in value:
        raise ValueError("v0.3 indexes only dataflows, bulletins and bulletin metadata")
    return value


def source_date(value):
    """Require ISO dates or timezone-aware instants; never guess source timezones."""
    if value is None or isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Use an ISO date or a timestamp with an explicit timezone")
    return value


class IndexDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str
    kind: Literal["dataset", "bulletin", "metadata"]
    title: str = Field(min_length=1, max_length=4000)
    title_en: str | None = Field(default=None, max_length=4000)
    description: str | None = Field(default=None, max_length=2_000_000)
    body: str | None = Field(default=None, max_length=2_000_000)
    theme: list[str] = Field(default_factory=list, max_length=128)
    subtheme: list[str] = Field(default_factory=list, max_length=256)
    category_path: list[str] = Field(default_factory=list, max_length=512)
    series_id: str | None = Field(default=None, max_length=200)
    related_ids: list[str] = Field(default_factory=list, max_length=4096)
    period: str | None = Field(default=None, max_length=500)
    publication_date: date | AwareDatetime | None = None
    updated_at: date | AwareDatetime | None = None
    formats: list[str] = Field(default_factory=list, max_length=32)
    downloadable: bool = Field(default=False, strict=True)
    source_url: str
    parent_id: str | None = None
    retrieved_at: AwareDatetime = Field(default_factory=now)
    indexed_at: AwareDatetime = Field(default_factory=now)

    _dates = field_validator("publication_date", "updated_at", mode="before")(source_date)
    _identity = field_validator("id")(index_resource_id)
    _source = field_validator("source_url")(official_url)

    @field_validator("parent_id")
    @classmethod
    def parent(cls, value):
        return index_resource_id(value) if value is not None else None

    @field_validator("related_ids", "theme", "subtheme", "category_path", "formats")
    @classmethod
    def unique_values(cls, values, info):
        for value in values:
            if not value or not value.strip():
                raise ValueError("Keyword values must not be empty")
            if info.field_name == "related_ids":
                index_resource_id(value)
            if info.field_name == "formats" and not re.fullmatch(r"[a-z0-9]{1,20}", value):
                raise ValueError("Formats must be lowercase source format identifiers")
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def identity_matches_kind(self):
        expected = (
            "dataset"
            if self.id.startswith("flow:")
            else "metadata"
            if self.id.endswith(":metadata")
            else "bulletin"
        )
        if self.kind != expected:
            raise ValueError("Document kind does not match its stable ID")
        if self.kind == "metadata":
            if self.parent_id != self.id.removesuffix(":metadata"):
                raise ValueError("Metadata parent must be its own bulletin ID")
        elif self.parent_id is not None:
            raise ValueError("Only metadata documents have a parent in v0.3")
        if self.id in self.related_ids:
            raise ValueError("A document cannot link to itself")
        return self
