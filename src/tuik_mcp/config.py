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
    # Reserved for the optional indexed-search backend; base tools stay live-only.
    opensearch_url: HttpUrl | None = None
    opensearch_index: str = Field(
        default="tuik-resources", max_length=255, pattern=r"^[a-z0-9][a-z0-9._-]*$"
    )

    timeout_seconds: float = Field(default=20, ge=1, le=60)
    attempts: int = Field(default=3, ge=1, le=3)
    min_interval_seconds: float = Field(default=0.5, ge=0, le=60)
    # TÜİK serves a "wait" page instead of a file to back-to-back table/document downloads.
    download_interval_seconds: float = Field(default=6, ge=0, le=60)
    max_response_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)

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
