import hashlib
import re
import unicodedata
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from tuik_mcp.config import ALLOWED_HOSTS, PORTAL

# Title-derived key of a file whose download URL changes over time (ADR 0003):
# readable slug, "~", 10 hex digits of the exact title's digest, optional ".N" ordinal.
FILE_KEY = r"[a-z0-9çğıöşü]+(?:-[a-z0-9çğıöşü]+)*~[0-9a-f]{10}(?:\.(?:[2-9]|[1-9][0-9]))?"
ID_PATTERN = re.compile(
    rf"(?:press:[1-9][0-9]{{0,9}}(?::metadata|:file:{FILE_KEY})?|classification:[1-9][0-9]{{0,9}}"
    rf"|flow:[A-Z0-9_]{{1,150}}\+V[0-9]+(?:\.[0-9]+){{1,3}}|resource:[a-f0-9]{{64}}"
    rf"|(?:table|report|publication):{FILE_KEY})\Z"
)


def validate_id(value: str) -> str:
    if not ID_PATTERN.fullmatch(value):
        raise ValueError("Invalid resource ID; use an ID returned by search_tuik")
    return value


def official_url(value: str, base: str = PORTAL, *, fetch: bool = False) -> str:
    if any(ord(c) < 33 for c in value) or "\\" in value:
        raise ValueError("Invalid source URL")
    url = urljoin(base, value)
    parsed = urlsplit(url)
    if (
        parsed.scheme not in ({"https"} if fetch else {"https", "http"})
        or parsed.hostname not in ALLOWED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443 if parsed.scheme == "https" else 80)
    ):
        raise ValueError("URL is not an allowed official TÜİK source")
    return url


def plain_text(value: str | None) -> str | None:
    if not value:
        return None
    soup = BeautifulSoup(value, "html.parser")
    for node in soup(["script", "style"]):
        node.decompose()
    return " ".join(soup.get_text(" ").split()) or None


def turkish_casefold(value: str) -> str:
    return (
        unicodedata.normalize("NFC", value).translate(str.maketrans({"İ": "i", "I": "ı"})).lower()
    )


def resource_id(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.hostname == "veriportali.tuik.gov.tr":
        match = re.fullmatch(r"/tr/press/([1-9][0-9]*)(/metadata)?", parsed.path)
        if match:
            return f"press:{match[1]}" + (":metadata" if match[2] else "")
    if parsed.hostname == "databrowser2.tuik.gov.tr":
        match = re.search(r"TR,([A-Z0-9_]+),([0-9]+(?:\.[0-9]+)+)$", parsed.fragment)
        if match:
            return validate_id(f"flow:{match[1]}+V{match[2]}")
    return "resource:" + hashlib.sha256(url.encode()).hexdigest()
