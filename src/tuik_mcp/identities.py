"""Stable IDs for TÜİK files whose download URLs change over time (ADR 0003).

A key is `<slug>~<digest>[.<ordinal>]`: the slug keeps the ID readable and doubles as
the search text, the digest pins the exact (case-sensitive) title, and the ordinal
separates later duplicates that share the same title in source order.
"""

import hashlib
import re
import unicodedata

from tuik_mcp.utils.normalization import FILE_KEY, turkish_casefold

SEARCH_KINDS = {2: "table", 4: "report", 6: "publication"}
KIND_TYPES = {kind: type_id for type_id, kind in SEARCH_KINDS.items()}
SLUG_LIMIT = 80
SLUG_LETTERS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789çğıöşü")
FILE_ID = re.compile(rf"press:([1-9][0-9]{{0,9}}):file:({FILE_KEY})")
SEARCH_ID = re.compile(rf"(table|report|publication):({FILE_KEY})")
KEY_PARTS = re.compile(r"(?P<slug>[^~]+)~(?P<digest>[0-9a-f]{10})(?:\.(?P<ordinal>[0-9]+))?")


def slug(title: str) -> str:
    """Lowercase Turkish words of the title, dash-separated, at most SLUG_LIMIT chars."""
    characters = []
    for character in turkish_casefold(title):
        if character not in SLUG_LETTERS:
            # Keep accented Latin letters searchable without their marks (â → a).
            base = unicodedata.normalize("NFKD", character)[0]
            character = base if base in SLUG_LETTERS else "-"
        characters.append(character)
    words = "".join(characters).split("-")
    result = ""
    for word in filter(None, words):
        candidate = f"{result}-{word}" if result else word
        if len(candidate) > SLUG_LIMIT:
            break
        result = candidate
    return result or "file"


def digest(title: str, discriminator: str) -> str:
    return hashlib.sha256(f"{title}\x1f{discriminator}".encode()).hexdigest()[:10]


def file_keys(entries: list[tuple[str, str]]) -> list[str]:
    """Keys for (title, discriminator) pairs; repeated pairs get ordinals in source order."""
    seen: dict[str, int] = {}
    keys = []
    for title, discriminator in entries:
        base = f"{slug(title)}~{digest(title, discriminator)}"
        seen[base] = seen.get(base, 0) + 1
        keys.append(base if seen[base] == 1 else f"{base}.{seen[base]}")
    return keys


def search_id(type_id: int, title: str) -> str | None:
    """ID for a downloadable search result of a supported type, else None."""
    kind = SEARCH_KINDS.get(type_id)
    if kind is None:
        return None
    return f"{kind}:{slug(title)}~{digest(title, str(type_id))}"


def key_parts(key: str) -> tuple[str, str, int]:
    """(search text, digest, ordinal) of a validated key."""
    match = KEY_PARTS.fullmatch(key)
    if match is None:
        raise ValueError("Invalid file key")
    return match["slug"].replace("-", " "), match["digest"], int(match["ordinal"] or 1)
