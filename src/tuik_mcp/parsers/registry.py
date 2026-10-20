from .csv import parse_csv
from .errors import UnsupportedFormatError
from .models import DocumentPage, ParsedTable


def parse_table(
    body: bytes,
    fmt: str,
    *,
    limit: int | None = None,
    filters: dict[str, str] | None = None,
) -> list[ParsedTable]:
    """Parse locally; async callers must offload this synchronous function to a thread.

    The format comes from verified download metadata.
    XLS/XLSX/PDF parsers will be registered by their ingestion features. A format
    without an implementation fails explicitly rather than guessing from the bytes.
    """
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("Parser limit must be a positive integer")
    if fmt == "csv":
        return parse_csv(body, limit=limit, filters=filters)
    raise UnsupportedFormatError(f"No table parser for format: {fmt}")


def parse_document(body: bytes, fmt: str) -> list[DocumentPage]:
    """Document parsing is introduced by the PDF ingestion feature (#7)."""
    raise UnsupportedFormatError(f"No document parser for format: {fmt}")
