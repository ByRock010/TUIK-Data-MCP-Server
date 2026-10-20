"""Shared representations and dispatch for bounded, local file parsing."""

from .models import DocumentChunk, DocumentPage, ParsedTable
from .registry import parse_document, parse_table

__all__ = ["DocumentChunk", "DocumentPage", "ParsedTable", "parse_document", "parse_table"]
