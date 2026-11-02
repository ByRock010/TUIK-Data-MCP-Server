"""Validated index documents shared by crawling and indexing (#14, #22).

Metadata documents are datasets, bulletins and bulletin metadata. File content
documents (#22) are spreadsheet tables and PDF passages; their parent is the stable
file ID (#27) that get_tuik_resource, get_tuik_data and read_tuik_document accept,
and their own ID only adds a page/chunk or table locator.
"""

import re
from datetime import date, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from tuik_mcp.models.common import now
from tuik_mcp.utils.normalization import FILE_KEY, official_url, validate_id

METADATA_KINDS = ("dataset", "bulletin", "metadata")
CONTENT_KINDS = ("table", "document_chunk")
FILE_PARENT = re.compile(
    rf"press:[1-9][0-9]{{0,9}}:file:{FILE_KEY}|(?:table|report|publication):{FILE_KEY}"
)
CONTENT_ID = re.compile(
    rf"(?P<parent>{FILE_PARENT.pattern})"
    r"#(?:p(?P<page>[1-9][0-9]{0,5})\.(?P<chunk>[1-9][0-9]{0,3})|t(?P<table>[1-9][0-9]{0,2}))"
)


def chunk_id(parent: str, page: int, chunk: int) -> str:
    return f"{parent}#p{page}.{chunk}"


def table_id(parent: str, ordinal: int) -> str:
    return f"{parent}#t{ordinal}"


def file_id(value: str) -> str:
    """A stable file identity that the MCP file tools resolve in a fresh session."""
    if not FILE_PARENT.fullmatch(validate_id(value)):
        raise ValueError("Expected a bulletin file or a table/report/publication file ID")
    return value


def index_resource_id(value: str) -> str:
    if CONTENT_ID.fullmatch(value):
        return value
    validate_id(value)
    if not value.startswith(("flow:", "press:")) or ":file:" in value:
        raise ValueError("Metadata documents are dataflows, bulletins and bulletin metadata")
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
    kind: Literal["dataset", "bulletin", "metadata", "table", "document_chunk"]
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
    # Citation locators of file content: a PDF page or a workbook sheet.
    page: int | None = Field(default=None, ge=1, le=100_000)
    sheet: str | None = Field(default=None, min_length=1, max_length=200)
    retrieved_at: AwareDatetime = Field(default_factory=now)
    indexed_at: AwareDatetime = Field(default_factory=now)

    _dates = field_validator("publication_date", "updated_at", mode="before")(source_date)
    _identity = field_validator("id")(index_resource_id)
    _source = field_validator("source_url")(official_url)

    @field_validator("parent_id")
    @classmethod
    def parent(cls, value):
        if value is None:
            return None
        return file_id(value) if FILE_PARENT.fullmatch(value) else index_resource_id(value)

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
        content = CONTENT_ID.fullmatch(self.id)
        expected = (
            ("document_chunk" if content["page"] else "table")
            if content
            else "dataset"
            if self.id.startswith("flow:")
            else "metadata"
            if self.id.endswith(":metadata")
            else "bulletin"
        )
        if self.kind != expected:
            raise ValueError("Document kind does not match its stable ID")
        if content:
            if self.parent_id != content["parent"]:
                raise ValueError("File content must name its own file as parent")
            if self.kind == "document_chunk" and self.page != int(content["page"]):
                raise ValueError("A passage must carry the page of its ID")
            if self.kind == "table" and self.sheet is None and self.page is None:
                raise ValueError("A table needs a sheet or page locator")
        elif self.page is not None or self.sheet is not None:
            raise ValueError("Only file content has page/sheet locators")
        elif self.kind == "metadata":
            if self.parent_id != self.id.removesuffix(":metadata"):
                raise ValueError("Metadata parent must be its own bulletin ID")
        elif self.parent_id is not None:
            raise ValueError("Only metadata and file content documents have a parent")
        if self.id in self.related_ids:
            raise ValueError("A document cannot link to itself")
        return self
