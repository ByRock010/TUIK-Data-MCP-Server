"""How tool results reach the client: one place, so output budgets match the wire."""

from typing import Any

import pydantic_core


def rendered(payload: Any) -> bytes:
    """Compact UTF-8 JSON of a tool result.

    FastMCP's default text rendering is indented, which is 10-30% more bytes for the
    same data; a model reading the text pays for every space.
    """
    return pydantic_core.to_json(payload)
