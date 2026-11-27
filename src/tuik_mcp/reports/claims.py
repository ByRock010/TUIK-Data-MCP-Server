"""Numeric claim checks for report narrative (#91).

A narrative may state a number only through an explicit binding: a `quote` of
the text that names one evidence entry. Each check is conservative; whatever it
cannot verify is reported, never assumed:

- the quoted number equals the evidence value at the quoted precision, in
  whole units (`3.214 bin` is 3,214,000 persons), with the stated sign;
- percent, percentage points, persons, currency and index values keep their
  own unit markers;
- a negative value is written with a minus sign or a decrease word;
- direction words (rose, azaldı) need a bound change with the same sign;
  comparatives (higher than, daha düşük) need a bound change too; superlatives
  (highest, en düşük, record) need a bound extremum of that kind;
- every date mentioned is a period of the bound evidence;
- every other number in the text is unbound and fails.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction

from tuik_mcp.calculations import place
from tuik_mcp.models.calculation import UnitSpec
from tuik_mcp.models.report import Claim, ClaimBinding

from .numbers import (
    CURRENCY_MARKERS,
    NUMBER,
    SCALE_WORDS,
    localized,
    lowered,
    parse_number,
    scaled,
)

MONTHS = {
    **{
        name: number
        for number, name in enumerate(
            [
                "ocak",
                "şubat",
                "mart",
                "nisan",
                "mayıs",
                "haziran",
                "temmuz",
                "ağustos",
                "eylül",
                "ekim",
                "kasım",
                "aralık",
            ],
            1,
        )
    },
    **{
        name: number
        for number, name in enumerate(
            [
                "january",
                "february",
                "march",
                "april",
                "may",
                "june",
                "july",
                "august",
                "september",
                "october",
                "november",
                "december",
            ],
            1,
        )
    },
}
MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4}
YEAR = r"(1[89]\d{2}|20\d{2})"
# Ordered most specific first; a later pattern never matches inside an earlier one.
DATES = [
    ("month", re.compile(rf"(?<![\w.,]){YEAR}-(0[1-9]|1[0-2])(?!\d)")),
    ("quarter", re.compile(rf"(?<![\w.,]){YEAR}-?q([1-4])(?!\w)")),
    ("quarter_first", re.compile(rf"(?<!\w)q([1-4])\s*{YEAR}(?!\d)")),
    ("half", re.compile(rf"(?<![\w.,]){YEAR}-?h([12])(?!\w)")),
    (
        "quarter",
        re.compile(rf"(?<![\w.,]){YEAR}(?:\s+yılı(?:nın)?)?\s+([1-4])\.\s*çeyre\w*"),
    ),
    ("quarter_first", re.compile(rf"(?<!\w)([1-4])\.\s*çeyre\w*\s+{YEAR}(?!\d)")),
    (
        "quarter_words",
        re.compile(rf"(?<!\w)(first|second|third|fourth)\s+quarter\s+of\s+{YEAR}(?!\d)"),
    ),
    ("month_name", re.compile(rf"(?<!\w)({MONTH})(?:['’]?\w*)?\s+{YEAR}(?!\d)")),
    (
        "year_month",
        re.compile(rf"(?<![\w.,]){YEAR}(?:\s+yılı(?:nın)?)?\s+({MONTH})(?:['’]?\w*)?"),
    ),
    # A month without a year: Turkish `Ekim ayında`, `Ekim'de`; English names except May.
    (
        "month_only",
        re.compile(
            rf"(?<!\w)({MONTH})(?:\s+ay\w*|['’]\w+|(?=[\s,.;:!?)]|$))",
        ),
    ),
    ("year", re.compile(rf"(?<![\w.,]){YEAR}(?!\d)(?![.,]\d)")),
]
MENTION_KINDS = {
    "month": "month",
    "month_name": "month",
    "year_month": "month",
    "quarter": "quarter",
    "quarter_first": "quarter",
    "quarter_words": "quarter",
    "half": "half",
    "month_only": "month_only",
    "year": "year",
}
DIRECTIONS = {
    "up": re.compile(
        r"(?<!\w)(art(tı|an|arak|ış|mış|maya|tığı)\w*|yüksel(di|en|erek|iş|miş)\w*|"
        r"increas\w*|rose|risen|rise[sn]?|grew|grow(n|s|th)?|climb\w*|gain(ed|s)?|"
        r"up(?=\s+(?:by\s+)?[-+−%]?\d))(?!\w)"
    ),
    "down": re.compile(
        r"(?<!\w)(azal(dı|an|arak|ış|mış|ma)\w*|düş(tü|en|erek|üş|müş)\w*|"
        r"geril(edi|eyen|eyerek|eme|emiş)\w*|decreas\w*|fell|fallen|fall(s|ing)?|"
        r"declin\w*|drop(ped|s)?|shr[ai]nk\w*|down(?=\s+(?:by\s+)?[-+−%]?\d))(?!\w)"
    ),
    "flat": re.compile(
        r"(?<!\w)(değişmedi|değişmeden|sabit\s+kaldı|aynı\s+kaldı|unchanged|stable|flat)(?!\w)"
    ),
}
COMPARATIVES = {
    "up": re.compile(
        r"(?<!\w)(daha\s+yüksek|daha\s+fazla|üzerinde|aştı|higher\s+than|more\s+than|"
        r"above|exceed\w*)(?!\w)"
    ),
    "down": re.compile(
        r"(?<!\w)(daha\s+düşük|daha\s+az|altında|lower\s+than|less\s+than|fewer\s+than|"
        r"below)(?!\w)"
    ),
}
SUPERLATIVES = {
    "max": re.compile(r"(?<!\w)(en\s+yüksek\w*|highest|peak\w*|zirve\w*)(?!\w)"),
    "min": re.compile(r"(?<!\w)(en\s+düşük\w*|lowest|dip)(?!\w)"),
    "any": re.compile(r"(?<!\w)(rekor\w*|record\w*|tüm\s+zamanlar\w*|all-time)(?!\w)"),
}
SENTENCE_END = re.compile(r"[.!?;]+(?=\s+[A-ZÇĞİÖŞÜ\"'(]|\s*$)")
SCALE = re.compile(r"\s*(" + "|".join(SCALE_WORDS) + r")(?!\w)")
PERCENTAGE_POINTS = re.compile(
    r"yüzde\s+puan\w*|(?<!\w)puan\w*|(?<!\w)pp(?!\w)|percentage[\s-]+points?|(?<!\w)points?(?!\w)"
)
PERCENT = re.compile(r"%|(?<!\w)yüzde(?!\w)|(?<!\w)per\s*cent(?!\w)")


@dataclass
class Fact:
    """What a claim may state about one resolved evidence entry."""

    key: str
    status: str
    reason: str | None
    value: Decimal | None
    unit: UnitSpec
    change: bool
    extremum: str | None
    periods: set[str] = field(default_factory=set)


@dataclass
class Mention:
    kind: str
    value: str
    text: str
    span: tuple[int, int]


def masked(text: str, spans) -> str:
    chars = list(text)
    for start, end in spans:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def occurrences(low: str, needle: str) -> list[tuple[int, int]]:
    spans = []
    if not needle:
        return spans
    start = low.find(needle)
    while start != -1:
        spans.append((start, start + len(needle)))
        start = low.find(needle, start + 1)
    return spans


def dates(low: str) -> list[Mention]:
    found: list[Mention] = []
    taken: list[tuple[int, int]] = []
    for kind, pattern in DATES:
        for match in pattern.finditer(low):
            span = match.span()
            if any(span[0] < end and start < span[1] for start, end in taken):
                continue
            groups = match.groups()
            if kind == "month":
                value = f"{groups[0]}-{groups[1]}"
            elif kind == "quarter":
                value = f"{groups[0]}-Q{groups[1]}"
            elif kind == "quarter_first":
                value = f"{groups[1]}-Q{groups[0]}"
            elif kind == "quarter_words":
                value = f"{groups[1]}-Q{ORDINALS[groups[0]]}"
            elif kind == "half":
                value = f"{groups[0]}-H{groups[1]}"
            elif kind == "month_name":
                value = f"{groups[1]}-{MONTHS[groups[0]]:02d}"
            elif kind == "year_month":
                value = f"{groups[0]}-{MONTHS[groups[1]]:02d}"
            elif kind == "month_only":
                if groups[0] == "may":
                    continue
                value = f"{MONTHS[groups[0]]:02d}"
            else:
                value = groups[0]
            taken.append(span)
            found.append(Mention(MENTION_KINDS[kind], value, low[span[0] : span[1]], span))
    return found


def supported(mention: Mention, allowed: set[str]) -> bool:
    if mention.kind == "year":
        return any(period[:4] == mention.value for period in allowed)
    if mention.kind == "month_only":
        return any(len(p) == 7 and p[4] == "-" and p[5:] == mention.value for p in allowed)
    return mention.value in allowed


def sentences(text: str) -> list[tuple[int, int]]:
    bounds, start = [], 0
    for match in SENTENCE_END.finditer(text):
        bounds.append((start, match.end()))
        start = match.end()
    if start < len(text):
        bounds.append((start, len(text)))
    return bounds


def sign(value: Decimal) -> str:
    return "up" if value > 0 else "down" if value < 0 else "flat"


def words(pattern_map: dict, low: str, start: int, end: int):
    for kind, pattern in pattern_map.items():
        for match in pattern.finditer(low, start, end):
            yield kind, match.group(0)


def check_quote(quote: str, fact: Fact, language: str, decrease_stated: bool) -> list[str]:
    """Problems with one bound quote; empty when it states the evidence faithfully."""
    if fact.status != "valid" or fact.value is None:
        reason = fact.reason or fact.status
        return [f"{fact.key} has no valid value ({reason}); it cannot be stated"]
    low = lowered(quote, language)
    tokens = list(NUMBER.finditer(low))
    if len(tokens) != 1:
        return [f"quote {quote!r} must contain exactly one number"]
    token = tokens[0]
    parsed = parse_number(token.group(0), language)
    if parsed is None:
        example = "1.234,5" if language == "tr" else "1,234.5"
        return [
            f"{token.group(0)!r} is not written with {language} separators (like {example}); "
            "numbers are read only in the report language"
        ]
    number, decimals = parsed
    before = low[: token.start()].rstrip(" %")
    explicit = before[-1:] if before[-1:] in ("-", "−", "+") else None
    if explicit and len(before) > 1 and (before[-2].isalnum()):
        explicit = None
    scale_match = SCALE.match(low, token.end())
    scale = SCALE_WORDS[scale_match.group(1)] if scale_match else 0
    problems = []
    points = PERCENTAGE_POINTS.search(low)
    percent = PERCENT.search(masked(low, [points.span()]) if points else low)
    kind = fact.unit.kind
    if kind == "percent" and (points or not percent):
        problems.append(
            f"{fact.key} is a percent value; write it with % (yüzde/percent), not as "
            "points or a bare number"
        )
    elif kind == "percentage_points" and not points:
        problems.append(
            f"{fact.key} is a difference in percentage points; write puan/percentage "
            "points, never %"
        )
    elif kind not in ("percent", "percentage_points") and (points or percent):
        problems.append(f"{fact.key} is in {kind}, not percent or percentage points")
    if kind == "currency" and fact.unit.currency:
        others = [
            marker
            for code, markers in CURRENCY_MARKERS.items()
            if code != fact.unit.currency
            for marker in markers
            if re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", low)
        ]
        if others:
            problems.append(f"{fact.key} is in {fact.unit.currency}, not {others[0]!r}")
    actual = place(scaled(fact.value, fact.unit) / Fraction(10) ** scale, decimals)
    if explicit:
        stated = -number if explicit in ("-", "−") else number
        if stated != actual:
            problems.append(mismatch(quote, fact, actual, language, scale))
    elif number != abs(actual):
        problems.append(mismatch(quote, fact, actual, language, scale))
    elif actual < 0 and not decrease_stated:
        problems.append(
            f"{fact.key} is negative ({localized(actual, language)}); write a minus sign or "
            "a decrease word"
        )
    return problems


def mismatch(quote: str, fact: Fact, actual: Decimal, language: str, scale: int) -> str:
    unit = f" × 10^{scale}" if scale else ""
    return (
        f"{quote!r} does not match {fact.key}: the evidence is "
        f"{localized(actual, language)}{unit} at that precision"
    )


def check_text(
    text: str,
    claims: list[ClaimBinding],
    facts: dict[str, Fact],
    language: str,
    labels: list[str],
    allowed: set[str],
) -> tuple[list[Claim], list[str]]:
    """Check one narrative: (claims with their problems, problems of the text itself)."""
    low = lowered(text, language)
    label_spans = [
        span for label in labels if label for span in occurrences(low, lowered(label, language))
    ]
    words_low = masked(low, label_spans)
    quote_spans: list[tuple[int, int] | None] = []
    for claim in claims:
        needle = lowered(claim.quote, language)
        span = next(
            (
                candidate
                for candidate in occurrences(low, needle)
                if not any(
                    other and candidate[0] < other[1] and other[0] < candidate[1]
                    for other in quote_spans
                )
            ),
            None,
        )
        quote_spans.append(span)
    bounds = sentences(text)

    def sentence_of(position: int) -> tuple[int, int]:
        return next((b for b in bounds if b[0] <= position < b[1]), (0, len(text)))

    results: list[Claim] = []
    for claim, span in zip(claims, quote_spans, strict=True):
        fact = facts[claim.evidence]
        if span is None:
            problems = [f"quote {claim.quote!r} does not occur in the text"]
        else:
            start, end = sentence_of(span[0])
            decrease = any(
                kind == "down"
                for kind, _ in [
                    *words(DIRECTIONS, words_low, start, end),
                    *words(COMPARATIVES, words_low, start, end),
                ]
            )
            problems = check_quote(text[span[0] : span[1]], fact, language, decrease)
        results.append(
            Claim(
                evidence=claim.evidence,
                quote=claim.quote,
                status="rejected" if problems else "verified",
                problems=problems,
            )
        )

    problems: list[str] = []
    for start, end in bounds:
        bound = [
            facts[claim.evidence]
            for claim, span in zip(claims, quote_spans, strict=True)
            if span is not None and start <= span[0] < end
        ]
        changes = [f for f in bound if f.change and f.value is not None]
        for kind, word in words(DIRECTIONS, words_low, start, end):
            if not changes:
                problems.append(
                    f"{word!r} states a change, but no change or difference is bound in "
                    "the same sentence"
                )
            elif not any(sign(f.value) == kind for f in changes):
                problems.append(
                    f"{word!r} contradicts the sign of "
                    + ", ".join(f"{f.key} ({f.value})" for f in changes)
                )
        for kind, word in words(COMPARATIVES, words_low, start, end):
            if not any(sign(f.value) == kind for f in changes):
                problems.append(
                    f"unsupported comparison {word!r}: bind a difference or change with "
                    "that sign in the same sentence"
                )
        for kind, word in words(SUPERLATIVES, words_low, start, end):
            if not any(f.extremum is not None and kind in (f.extremum, "any") for f in bound):
                wanted = {"max": "a max", "min": "a min"}.get(kind, "an")
                problems.append(
                    f"unsupported comparison {word!r}: bind {wanted} extremum over the "
                    "compared range in the same sentence"
                )
    rest = masked(low, [*label_spans, *(span for span in quote_spans if span)])
    mentions = dates(rest)
    for mention in mentions:
        if not supported(mention, allowed):
            shown = ", ".join(sorted(allowed)) or "none"
            problems.append(
                f"date {text[mention.span[0] : mention.span[1]]!r} is not a period of the "
                f"bound evidence ({shown})"
            )
    rest = masked(rest, [mention.span for mention in mentions])
    for token in NUMBER.finditer(rest):
        problems.append(
            f"number {text[token.start() : token.end()]!r} is not bound to evidence; add a "
            "claim with its quote"
        )
    return results, problems
