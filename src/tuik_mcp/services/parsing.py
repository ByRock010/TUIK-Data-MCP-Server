"""Service boundary for local file parsing.

Parser errors describe the downloaded file, never the caller's input, so they are
translated into `SourceError`s that cite the file's source URL. MCP responses then
report a source error code instead of `invalid_input`.
"""

import asyncio

from tuik_mcp.clients.base import SourceError
from tuik_mcp.parsers import ParsedTable, parse_table
from tuik_mcp.parsers.errors import (
    InvalidFiltersError,
    ParseError,
    ParseLimitError,
    UnsupportedFormatError,
)
from tuik_mcp.parsers.limits import ParseLimits
from tuik_mcp.parsers.models import ParsedDocument
from tuik_mcp.parsers.pdf import parse_pdf


def parse_source(
    body: bytes,
    fmt: str,
    source_url: str,
    *,
    limit: int | None = None,
    filters: dict[str, str] | None = None,
    sheet: str | None = None,
    budgets: ParseLimits | None = None,
) -> list[ParsedTable]:
    try:
        return parse_table(body, fmt, limit=limit, filters=filters, sheet=sheet, budgets=budgets)
    except ParseLimitError as exc:
        raise SourceError("parse_limit_exceeded", str(exc), source_url) from exc
    except InvalidFiltersError as exc:
        raise SourceError("invalid_filters", str(exc), source_url) from exc
    except UnsupportedFormatError as exc:
        raise SourceError("unsupported_download", str(exc), source_url) from exc
    except ParseError as exc:
        # CSV keeps the code its MCP responses have always used.
        code = "malformed_response" if fmt == "csv" else "parse_failed"
        raise SourceError(code, str(exc), source_url) from exc


async def parse_source_in_thread(
    body: bytes,
    fmt: str,
    source_url: str,
    *,
    limit: int | None = None,
    filters: dict[str, str] | None = None,
    sheet: str | None = None,
    budgets: ParseLimits | None = None,
) -> list[ParsedTable]:
    """`parse_source` off the event loop; parsing is CPU-bound."""
    return await asyncio.to_thread(
        parse_source,
        body,
        fmt,
        source_url,
        limit=limit,
        filters=filters,
        sheet=sheet,
        budgets=budgets,
    )


def parse_document_source(
    body: bytes,
    source_url: str,
    *,
    page: int = 1,
    budgets: ParseLimits | None = None,
) -> ParsedDocument:
    try:
        return parse_pdf(body, page=page, budgets=budgets)
    except ParseLimitError as exc:
        raise SourceError("parse_limit_exceeded", str(exc), source_url) from exc
    except ParseError as exc:
        raise SourceError("parse_failed", str(exc), source_url) from exc
