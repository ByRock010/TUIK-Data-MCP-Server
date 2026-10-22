"""Text-layer PDF reading; only requested pages undergo layout/text extraction."""

import io
import re

from .errors import ParseError, ParseLimitError
from .limits import ParseLimits
from .models import DocumentChunk, DocumentPage, ParsedDocument


def normalize_text(text: str) -> str:
    text = re.sub(r"(?<=[^\W\d_])[-\u00ad][ \t]*\n[ \t]*(?=[^\W\d_])", "", text)
    lines = [" ".join(line.replace("\u00ad", "").split()) for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def parse_pdf(
    body: bytes,
    *,
    page: int = 1,
    pages: int = 1,
    budgets: ParseLimits | None = None,
) -> ParsedDocument:
    import pdfplumber
    from pdfminer.pdfpage import PDFPage
    from pdfplumber.page import Page

    budgets = budgets or ParseLimits()
    if type(page) is not int or page < 1 or type(pages) is not int or pages < 1:
        raise ValueError("PDF page and pages must be positive integers")
    budgets.check(pages, budgets.max_pdf_pages_per_call, "PDF pages per call")
    selected: list[DocumentPage] = []
    total = 0
    try:
        # Own the byte stream. Avoid PDF.pages, which would enumerate/create every
        # page before a page-count budget could be checked.
        with io.BytesIO(body) as stream:
            pdf = pdfplumber.open(stream)
            objects = []
            for number, obj in enumerate(PDFPage.create_pages(pdf.doc), 1):
                total = number
                budgets.check(number, budgets.max_pdf_pages, "PDF document pages")
                if page <= number < page + pages:
                    objects.append((number, obj))
            for number, obj in objects:
                source = Page(pdf, obj, page_number=number)
                clean = None
                try:
                    budgets.check(
                        len(source.chars), budgets.max_pdf_chars_per_page, "PDF page characters"
                    )
                    clean = source.dedupe_chars()
                    text = normalize_text(clean.extract_text() or "")
                    first = text.splitlines()[0] if text else None
                    heading = first if first and len(first) <= 120 else None
                    selected.append(DocumentPage(page=number, text=text, heading=heading))
                finally:
                    if clean is not None:
                        clean.close()
                    source.close()
            pdf.flush_cache()
    except ParseLimitError:
        raise
    except Exception as exc:
        raise ParseError("TÜİK returned an unreadable or encrypted PDF") from exc
    if total == 0:
        raise ParseError("PDF has no pages")
    if page > total:
        raise ValueError(f"PDF page {page} is out of range; document has {total} pages")
    return ParsedDocument(page_count=total, pages=selected)


def chunk_pages(pages: list[DocumentPage], max_chars: int = 1500) -> list[DocumentChunk]:
    """Bounded chunks keep their source page and heading; never cross page boundaries."""
    if type(max_chars) is not int or max_chars < 1:
        raise ValueError("Chunk size must be a positive integer")
    chunks = []
    for page in pages:
        remaining = page.text.strip()
        while remaining:
            end = min(len(remaining), max_chars)
            if end < len(remaining):
                boundary = remaining.rfind("\n", 0, end + 1)
                if boundary <= 0:
                    boundary = remaining.rfind(" ", 0, end + 1)
                if boundary > 0:
                    end = boundary
            chunks.append(DocumentChunk(page=page.page, heading=page.heading, text=remaining[:end]))
            remaining = remaining[end:].lstrip()
    return chunks
