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
- every date mentioned is a period of the bound evidence, and the date nearest
  a quote in its sentence is a period of that quote's own evidence;
- a currency stated next to a quote is the evidence's currency, and a
  non-currency value is never stated in a currency;
- a province named in the text is part of the bound evidence's labels or
  dimensions, and a national scope word is not used for provincial evidence;
- every other number in the text is unbound and fails.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction

from tuik_mcp.calculations import place
from tuik_mcp.models.calculation import UnitSpec
from tuik_mcp.models.report import Claim, ClaimBinding
from tuik_mcp.observations.geography import PROVINCES

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
# Clause breaks: punctuation followed by a space (never a decimal comma) or a conjunction.
CLAUSE_BREAK = re.compile(
    r"[,;:]\s+|\s+(?:ve|ama|fakat|ancak|iken|and|but|while|whereas)(?=\s)", re.IGNORECASE
)
# Explicit units written after a number, with the unit kind each one states; None is a
# physical unit no reviewed recipe publishes (tonnes, kilograms, …), never a match.
EXPLICIT_UNITS = re.compile(
    r"(?<![^\W\d_])("
    r"(?P<persons>kişi\w*|insan\w*|people|persons?)"
    r"|(?P<terajoule>tj|terajoule?s?|terajul\w*)"
    r"|(?P<other>tonne?s?|ton(?:luk|u|a|da|dan)?|kg|kilogram\w*|gram\w*|km|kilomet\w*|metre\w*"
    r"|meters?|m2|m²|m3|m³|litre\w*|liters?|lt|kwh|mwh|gwh|twh|hektar\w*|hectares?|dekar\w*"
    r"|adet\w*|hane\w*|households?|firma\w*|işyer\w*|araç\w*|vehicles?)"
    r")(?![^\W\d_])"
)
# Every word a quote may hold besides its number: scales, percent and point words,
# currencies, persons and terajoules. Any other word in a quote is an unrecognised unit
# or qualifier (`3.214 bin varil`) and leaves the claim unverified.
QUANTITY_WORD = re.compile(
    r"(?:"
    + "|".join(sorted(SCALE_WORDS, key=len, reverse=True))
    + r"|yüzde|puan\w*|pp|percentage|points?|percent|per|cent"
    + r"|kişi\w*|persons?|people|tj|terajoule?s?|terajul\w*"
    + r"|abd|us|türk|tl|try|lira\w*|usd|dolar\w*|dollars?|eur|euros?|avro\w*"
    + r")"
)
WORD = re.compile(r"[^\W\d_]+")
# The word or symbol attached to a number from outside its quote: right after it
# (`3.214 bin` + `ton`), or right before it (`USD` + `3.214 bin`).
ATTACHED_AFTER = re.compile(r"\s*((?:abd|us|türk)\s+[^\W\d_]+|[^\W\d_]+|[%$₺€])")
ATTACHED_BEFORE = re.compile(r"((?:abd|us)\s+[^\W\d_]+|[^\W\d_]+|[$₺€])\s*$")
# Unit words a quote's number may carry right after it, before the next number.
UNIT_TAIL = 40
ASCII = str.maketrans("çğıöşüâîû", "cgiosuaiu")
# Province names keep their case suffix after an apostrophe (İstanbul'da, Van'ın).
PLACE = re.compile(
    r"(?<!\w)("
    + "|".join(
        sorted({lowered(name, "tr").translate(ASCII) for name in PROVINCES}, key=len, reverse=True)
    )
    + r")(?:['’]\w*)?(?!\w)"
)
NATIONAL_SCOPE = re.compile(
    r"(?<!\w)(türkiye\w*|turkey|türkiye\s+geneli\w*|ülke\s+genel\w*|ulusal|national\w*|"
    r"nationwide)(?![\w])"
)


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


LETTER = r"[^\W\d_]"


def currencies(low: str) -> list[tuple[str, str]]:
    """(code, marker) for every currency marker.

    Symbols ($, ₺, €) count even when touching a digit (`$3.214`); codes and words
    need no letter around them, so `USD3.214` counts too, and words keep Turkish
    suffixes (doları).
    """
    found = []
    for code, markers in CURRENCY_MARKERS.items():
        for marker in markers:
            if not marker.isalnum():
                pattern = re.escape(marker)
            elif len(marker) > 3:
                pattern = rf"(?<!{LETTER}){re.escape(marker)}\w*"
            else:
                pattern = rf"(?<!{LETTER}){re.escape(marker)}(?!{LETTER})"
            if re.search(pattern, low):
                found.append((code, marker))
    return found


def places(folded: str) -> list[tuple[str, tuple[int, int]]]:
    return [(match.group(1), match.span()) for match in PLACE.finditer(folded)]


def geography_terms(text: str) -> tuple[set[str], bool]:
    """Provinces a text names (ASCII-folded) and whether it uses national wording."""
    low = lowered(text, "tr")
    return {name for name, _ in places(low.translate(ASCII))}, bool(NATIONAL_SCOPE.search(low))


def clauses(text: str, sentence: tuple[int, int]) -> list[tuple[int, int]]:
    """Clause bounds of a sentence: between commas, semicolons, colons or conjunctions."""
    start, end = sentence
    bounds = []
    for match in CLAUSE_BREAK.finditer(text, start, end):
        bounds.append((start, match.start()))
        start = match.end()
    bounds.append((start, end))
    return bounds


def check_quote(
    quote: str,
    fact: Fact,
    language: str,
    decrease_stated: bool,
    tail: str = "",
    head: str = "",
) -> list[str]:
    """Problems with one bound quote; empty when it states the evidence faithfully.

    `head` and `tail` are the clause text right before and after the quote, where a
    currency or unit word may stand next to its number.
    """
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
    # The quote is a quantity: sign, number, scale and unit words, nothing else.
    rest = masked(low, [token.span()])
    unknown = [w.group(0) for w in WORD.finditer(rest) if not QUANTITY_WORD.fullmatch(w.group(0))]
    if unknown:
        problems.append(
            f"quote {quote!r} has an unrecognised unit or qualifier {unknown[0]!r}; quote only "
            "the number, its scale and its unit"
        )
    after = ATTACHED_AFTER.match(lowered(tail, language))
    before = ATTACHED_BEFORE.search(lowered(head, language))
    attached_after = after.group(1) if after else ""
    attached_before = before.group(1) if before else ""
    stated_currencies = currencies(attached_before + " " + low + " " + attached_after)
    if kind == "currency" and fact.unit.currency:
        others = [marker for code, marker in stated_currencies if code != fact.unit.currency]
        if others:
            problems.append(f"{fact.key} is in {fact.unit.currency}, not {others[0]!r}")
    elif kind != "currency" and stated_currencies:
        problems.append(f"{fact.key} is in {kind}, not a currency ({stated_currencies[0][1]!r})")
    # Physical units follow their number: the quote's own words and the one attached word.
    for match in EXPLICIT_UNITS.finditer(low[token.end() :] + " " + attached_after):
        stated = next(name for name, value in match.groupdict().items() if value)
        if stated != kind:
            problems.append(
                f"{fact.key} is in {kind}, not {match.group(0)!r}; an explicit unit must be "
                "the evidence's unit"
            )
            break
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


def period_problems(
    span: tuple[int, int],
    sentence: tuple[int, int],
    mentions: list[Mention],
    fact: Fact,
    text: str,
    quotes: list[tuple[int, int]],
) -> list[str]:
    """The date attached to a quote must be a period of that quote's evidence.

    The attached date is the nearest one in the quote's clause (between commas,
    semicolons or conjunctions). Without one, a date standing in a clause of its own
    right before (`Ocak 2026'da, oran …`) or right after the quote's clause is
    attached; a clause holding another quote stops the search, since its date may
    govern that value instead.
    """
    if not fact.periods:
        return []
    parts = clauses(text, sentence)
    index = next(
        i for i, (start, end) in enumerate(parts) if start <= span[0] < end or i == len(parts) - 1
    )

    def inside(bounds: tuple[int, int]) -> list[Mention]:
        return [m for m in mentions if bounds[0] <= m.span[0] and m.span[1] <= bounds[1]]

    def quoted(bounds: tuple[int, int]) -> bool:
        return any(bounds[0] <= q[0] < bounds[1] for q in quotes)

    near = inside(parts[index])
    if not near:
        for step in (-1, 1):
            position = index + step
            while 0 <= position < len(parts) and not quoted(parts[position]):
                found = inside(parts[position])
                if found:
                    near = [found[-1] if step < 0 else found[0]]
                    break
                position += step
            if near:
                break
    if not near:
        # A date leading the sentence, before its first quote, scopes every parallel
        # statement after it (`Ocak 2026'da oran %8,4, işsiz sayısı 3.214 bin kişi`).
        start, end = sentence
        first = min((q[0] for q in quotes if start <= q[0] < end), default=span[0])
        near = [m for m in mentions if start <= m.span[0] and m.span[1] <= first]
    if not near:
        return []

    def gap(mention: Mention) -> int:
        return max(mention.span[0] - span[1], span[0] - mention.span[1], 0)

    closest = min(gap(m) for m in near)
    candidates = [m for m in near if gap(m) == closest]
    if any(supported(m, fact.periods) for m in candidates):
        return []
    shown = text[candidates[0].span[0] : candidates[0].span[1]]
    return [
        f"{shown!r} is not a period of {fact.key} ({', '.join(sorted(fact.periods))}); the "
        "value is stated for the wrong period"
    ]


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
    rest = masked(low, [*label_spans, *(span for span in quote_spans if span)])
    mentions = dates(rest)

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
            low_clause, high_clause = next(
                (b for b in clauses(text, (start, end)) if b[0] <= span[0] < b[1]),
                (start, end),
            )
            # A neighbouring quote's own words (`1,2 milyar ABD doları`) never count here.
            others = [q for q in quote_spans if q and q != span]
            low_clause = max([low_clause, *(q[1] for q in others if q[1] <= span[0])])
            high_clause = min([high_clause, *(q[0] for q in others if q[0] >= span[1])])
            after = text[span[1] : min(max(high_clause, span[1]), span[1] + UNIT_TAIL)]
            next_number = NUMBER.search(lowered(after, language))
            tail = after[: next_number.start()] if next_number else after
            before = text[max(low_clause, span[0] - UNIT_TAIL) : span[0]]
            numbers_before = list(NUMBER.finditer(lowered(before, language)))
            head = before[numbers_before[-1].end() :] if numbers_before else before
            problems = check_quote(text[span[0] : span[1]], fact, language, decrease, tail, head)
            problems += period_problems(
                span, (start, end), mentions, fact, text, [q for q in quote_spans if q]
            )
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
    folded = words_low.translate(ASCII)
    for _, span in places(folded):
        problems.append(
            f"{text[span[0] : span[1]]!r} is not the geography of the bound evidence; the "
            "scope of a statement must match its evidence"
        )
    provincial = any(places(lowered(label, language).translate(ASCII)) for label in labels)
    if provincial:
        for match in NATIONAL_SCOPE.finditer(words_low):
            problems.append(
                f"{text[match.start() : match.end()]!r} states a national scope, but the bound "
                "evidence is provincial"
            )
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
