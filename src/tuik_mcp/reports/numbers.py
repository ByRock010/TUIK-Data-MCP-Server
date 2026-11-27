"""Localized numbers and units for report text (#91).

Turkish text groups thousands with `.` and writes decimals with `,`; English
does the reverse. A number is read only with its report language's separators,
so `1.234` is one thousand two hundred thirty-four in Turkish and never 1.234.
"""

import re
from decimal import Decimal
from fractions import Fraction

from tuik_mcp.calculations import place
from tuik_mcp.models.calculation import UnitSpec

SEPARATORS = {"tr": (".", ","), "en": (",", ".")}
NUMBER = re.compile(r"(?<![\w])\d+(?:[.,]\d+)*")
SCALES = {
    "tr": {3: "bin", 6: "milyon", 9: "milyar"},
    "en": {3: "thousand", 6: "million", 9: "billion"},
}
SCALE_WORDS = {word: power for words in SCALES.values() for power, word in words.items()}
CURRENCIES = {"TRY": "TL", "USD": "USD", "EUR": "EUR"}
CURRENCY_MARKERS = {
    "TRY": ("tl", "₺", "lira", "try"),
    "USD": ("usd", "$", "dolar", "dollar"),
    "EUR": ("eur", "€", "avro", "euro"),
}
UNIT_WORDS = {
    "tr": {"percentage_points": "yüzde puan", "persons": "kişi"},
    "en": {"percentage_points": "percentage points", "persons": "persons"},
}


def lowered(text: str, language: str) -> str:
    """Lower case with Turkish dotted and dotless i, keeping every offset."""
    mapped = text.replace("İ", "i")
    if language == "tr":
        mapped = mapped.replace("I", "ı")
    return "".join(c.lower() if len(c.lower()) == 1 else c for c in mapped)


def localized(value: Decimal, language: str) -> str:
    """Plain decimal text with the language's grouping and decimal separators."""
    plain = format(value, "f")
    sign = "-" if plain.startswith("-") else ""
    whole, _, fraction = plain.lstrip("-").partition(".")
    group, decimal = SEPARATORS[language]
    grouped = f"{int(whole):,}".replace(",", group)
    return sign + grouped + (decimal + fraction if fraction else "")


def parse_number(token: str, language: str) -> tuple[Decimal, int] | None:
    """The value and decimal places of an unsigned token, or None if not in `language`."""
    group, decimal = SEPARATORS[language]
    if token.count(decimal) > 1:
        return None
    whole, _, fraction = token.partition(decimal)
    parts = whole.split(group)
    if len(parts) > 1 and (
        not 1 <= len(parts[0]) <= 3 or any(len(part) != 3 for part in parts[1:])
    ):
        return None
    digits = "".join(parts)
    if not digits.isdigit() or (fraction and not fraction.isdigit()):
        return None
    if decimal in token and not fraction:
        return None
    return Decimal(digits + ("." + fraction if fraction else "")), len(fraction)


def scaled(value: Decimal, unit: UnitSpec) -> Fraction:
    """The value in whole units: thousands of persons become persons."""
    return Fraction(value) * Fraction(10) ** unit.multiplier


def display(value: Decimal, decimals: int, unit: UnitSpec, language: str) -> str:
    """`value` (in the published multiplier) rounded for display, with its unit."""
    multiplier = unit.multiplier
    scale = SCALES[language].get(multiplier)
    if scale is None and multiplier:
        number = place(scaled(value, unit), decimals)
        multiplier = 0
    else:
        number = place(Fraction(value), decimals)
    if number == 0:
        number = abs(number)
    text = localized(number, language)
    words = " ".join(filter(None, [scale if multiplier else None]))
    kind = unit.kind
    if kind == "percent":
        return f"%{text}" if language == "tr" else f"{text}%"
    if kind == "percentage_points":
        return f"{text} {UNIT_WORDS[language]['percentage_points']}"
    if kind == "persons":
        return " ".join(filter(None, [text, words, UNIT_WORDS[language]["persons"]]))
    if kind == "currency":
        currency = CURRENCIES.get(unit.currency or "", unit.currency or "")
        return " ".join(filter(None, [text, words, currency]))
    if kind == "terajoule":
        return " ".join(filter(None, [text, words, "TJ"]))
    return " ".join(filter(None, [text, words]))


def unit_label(unit: UnitSpec, language: str) -> str | None:
    if unit.kind == "percent":
        return "%" if language == "tr" else "percent"
    if unit.kind == "percentage_points":
        return UNIT_WORDS[language]["percentage_points"]
    if unit.kind == "index":
        base = f" ({unit.base_period}=100)" if unit.base_period else ""
        return ("endeks" if language == "tr" else "index") + base
    scale = SCALES[language].get(unit.multiplier, "")
    if unit.kind == "persons":
        return " ".join(filter(None, [scale, UNIT_WORDS[language]["persons"]]))
    if unit.kind == "currency":
        return " ".join(filter(None, [scale, CURRENCIES.get(unit.currency or "", unit.currency)]))
    if unit.kind == "terajoule":
        return " ".join(filter(None, [scale, "TJ"]))
    return None
