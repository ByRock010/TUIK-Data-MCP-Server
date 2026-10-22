"""Service boundary for local file parsing.

Parser errors describe the downloaded file, never the caller's input, so they are
translated into `SourceError`s that cite the file's source URL. MCP responses then
report a source error code instead of `invalid_input`.
"""

import asyncio

from tuik_mcp.clients.base import SourceError
from tuik_mcp.parsers import ParsedTable, parse_table
from tuik_mcp.parsers.errors import InvalidFiltersError, ParseError, UnsupportedFormatError


def parse_source(
    body: bytes,
    fmt: str,
    source_url: str,
    *,
    limit: int | None = None,
    filters: dict[str, str] | None = None,
) -> list[ParsedTable]:
    try:
        return parse_table(body, fmt, limit=limit, filters=filters)
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
) -> list[ParsedTable]:
    """`parse_source` off the event loop; parsing is CPU-bound."""
    return await asyncio.to_thread(
        parse_source, body, fmt, source_url, limit=limit, filters=filters
    )
