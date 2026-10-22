"""Input budgets shared by synchronous parsers and async services."""

from dataclasses import dataclass

from tuik_mcp.config import Settings

from .errors import ParseLimitError


@dataclass(frozen=True)
class ParseLimits:
    max_sheets: int = 16
    max_cells: int = 200_000
    max_uncompressed_bytes: int = 50 * 1024 * 1024
    max_pdf_pages: int = 1000
    max_pdf_pages_per_call: int = 5
    max_pdf_chars_per_page: int = 200_000

    @classmethod
    def from_settings(cls, settings: Settings) -> "ParseLimits":
        return cls(
            **{name: getattr(settings, "parse_" + name) for name in cls.__dataclass_fields__}
        )

    def check(self, value: int, maximum: int, label: str) -> None:
        if value > maximum:
            raise ParseLimitError(f"{label} exceeds the configured limit ({maximum})")
