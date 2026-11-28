"""Response contract for explicitly selected, reviewed DataBrowser dataflows."""

from typing import Literal

from pydantic import Field

from tuik_mcp.models.common import Provenance
from tuik_mcp.models.observation import Observation


class DimensionResponse(Provenance):
    schema_version: Literal["tuik.dimensions/1"] = "tuik.dimensions/1"
    status: Literal["catalog", "structure", "ok", "empty"]
    message: str
    resource_id: str | None = None
    dataflow: str | None = None
    dsd: str | None = None
    structure_sha256: str | None = None
    snapshot_id: str | None = None
    period: str | None = None
    profiles: list[dict] = Field(default_factory=list)
    dimensions: list[dict] = Field(default_factory=list)
    attributes: list[dict] = Field(default_factory=list)
    dimension: str | None = None
    codes: list[dict] = Field(default_factory=list)
    filters: dict[str, list[dict]] = Field(default_factory=dict)
    constant_dimensions: dict[str, str] = Field(default_factory=dict)
    total: int = 0
    complete: bool = False
    observations: list[Observation] = Field(default_factory=list)
    offset: int = 0
    next_offset: int | None = None
    response_bytes: int | None = None
    upstream_row_limit: int = 200
    upstream_byte_limit: int = 256 * 1024
