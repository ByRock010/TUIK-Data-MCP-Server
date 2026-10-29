import os

from pydantic import BaseModel, Field, HttpUrl, field_validator

PORTAL = "https://veriportali.tuik.gov.tr"
CLASSIFICATION = "https://siniflama.tuik.gov.tr"
# Additional source hosts were linked by the official homepage/search responses.
ALLOWED_HOSTS = frozenset(
    {
        "www.tuik.gov.tr",
        "veriportali.tuik.gov.tr",
        "siniflama.tuik.gov.tr",
        "databrowser2.tuik.gov.tr",
        "nip.tuik.gov.tr",
        "biruni.tuik.gov.tr",
    }
)
USER_AGENT = "Mozilla/5.0 (compatible; tuik-mcp/0.1; public statistical research)"


class Settings(BaseModel):
    # Optional indexed-search backend; unset keeps literal/portal search.
    opensearch_url: HttpUrl | None = None
    opensearch_index: str = Field(
        default="tuik-resources", max_length=255, pattern=r"^[a-z0-9][a-z0-9._-]*$"
    )
    # Searches fall back quickly when the cluster does not answer.
    opensearch_timeout_seconds: float = Field(default=5, ge=0.5, le=60)

    timeout_seconds: float = Field(default=20, ge=1, le=60)
    attempts: int = Field(default=3, ge=1, le=3)
    min_interval_seconds: float = Field(default=0.5, ge=0, le=60)
    # TÜİK serves a "wait" page instead of a file to back-to-back table/document downloads.
    download_interval_seconds: float = Field(default=6, ge=0, le=60)
    max_response_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    parse_max_sheets: int = Field(default=16, ge=1, le=128)
    parse_max_cells: int = Field(default=200_000, ge=1, le=2_000_000)
    parse_max_uncompressed_bytes: int = Field(
        default=50 * 1024 * 1024, ge=1024, le=200 * 1024 * 1024
    )
    parse_max_pdf_pages: int = Field(default=1000, ge=1, le=5000)
    parse_max_pdf_pages_per_call: int = Field(default=5, ge=1, le=20)
    parse_max_pdf_chars_per_page: int = Field(default=200_000, ge=1000, le=1_000_000)
    parse_cache_bytes: int = Field(default=32 * 1024 * 1024, ge=1024, le=256 * 1024 * 1024)
    # inspect_tuik_data profiling budgets: rows scanned, columns profiled, tracked
    # distinct values per column and serialized column-profile bytes per response.
    inspect_max_rows: int = Field(default=100_000, ge=1, le=1_000_000)
    inspect_max_columns: int = Field(default=100, ge=1, le=1000)
    inspect_max_distinct: int = Field(default=10_000, ge=10, le=100_000)
    inspect_max_output_bytes: int = Field(default=64 * 1024, ge=8 * 1024, le=1024 * 1024)

    @field_validator("opensearch_url", mode="before")
    @classmethod
    def blank_search_url_is_unset(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            **{
                key: os.environ[f"TUIK_{key.upper()}"]
                for key in cls.model_fields
                if f"TUIK_{key.upper()}" in os.environ
            }
        )
