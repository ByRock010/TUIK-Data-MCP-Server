"""Deterministic, evidence-backed sentences in Turkish and English (#91).

A template writes the sentence and its claim bindings together; the report
service then runs the same claim checks as on host narrative, so a template can
never state more than its evidence.
"""

from decimal import Decimal

from tuik_mcp.models.report import ClaimBinding, Evidence

from .numbers import display

TEXT = {
    "tr": {
        "headings": {
            "summary": "Özet",
            "findings": "Bulgular",
            "table": "Tablo",
            "chart": "Grafik",
            "methods": "Yöntem",
            "limitations": "Sınırlılıklar",
            "sources": "Kaynaklar",
        },
        "columns": ["Gösterge", "Dönem", "Değer", "Durum", "Kanıt"],
        "unknown": "bilinmiyor",
        "derived": (
            "Türetilmiş değerler tuik-mcp tarafından listelenen gözlemlerden hesaplanmıştır; "
            "TÜİK tarafından yayımlanmamıştır."
        ),
        "rounding": (
            "Bölmeler yarıya-çift kuralıyla 10 ondalık basamağa, gösterilen değerler "
            "belirtilen basamağa yuvarlanır."
        ),
        "normalized": (
            "Değerler incelenmiş TÜİK kaynak tariflerinin normalize gözlemlerinden "
            "(tuik.observation/1) okunur; eksik dönemler doldurulmaz."
        ),
        "unverified": "DOĞRULANMAMIŞ TASLAK",
        "missing": "değer yok",
    },
    "en": {
        "headings": {
            "summary": "Summary",
            "findings": "Findings",
            "table": "Table",
            "chart": "Chart",
            "methods": "Methods",
            "limitations": "Limitations",
            "sources": "Sources",
        },
        "columns": ["Indicator", "Period", "Value", "Status", "Evidence"],
        "unknown": "unknown",
        "derived": (
            "Derived values are computed by tuik-mcp from the listed observations; TÜİK did "
            "not publish them."
        ),
        "rounding": (
            "Divisions round half-to-even to 10 decimal places; displayed values round to "
            "the stated decimals."
        ),
        "normalized": (
            "Values are normalized observations (tuik.observation/1) of reviewed TÜİK "
            "source recipes; missing periods are never filled."
        ),
        "unverified": "UNVERIFIED DRAFT",
        "missing": "no value",
    },
}


def shown(evidence: Evidence, value: Decimal, language: str) -> str:
    return display(value, evidence.decimals, evidence.unit, language)


def sentence(evidence: Evidence, language: str) -> tuple[str, list[ClaimBinding]]:
    """One sentence and its bindings for a valid evidence value."""
    value = Decimal(evidence.value)
    label = evidence.label
    period = evidence.period
    tr = language == "tr"
    if evidence.kind == "calculation" and evidence.measure.type == "change":
        magnitude = shown(evidence, abs(value), language)
        reference = evidence.reference_period
        if value == 0:
            quote = shown(evidence, value, language)
            text = (
                f"{label}, {period} döneminde {reference} dönemine göre değişmedi ({quote})."
                if tr
                else f"{label} was unchanged in {period} compared with {reference} ({quote})."
            )
        elif tr:
            verb = "arttı" if value > 0 else "azaldı"
            quote = magnitude
            text = f"{label}, {period} döneminde {reference} dönemine göre {quote} {verb}."
        else:
            verb = "increased" if value > 0 else "decreased"
            quote = magnitude
            text = f"{label} {verb} by {quote} in {period} compared with {reference}."
        return text, [ClaimBinding(evidence=evidence.key, quote=quote)]
    quote = shown(evidence, value, language)
    if evidence.kind == "calculation" and evidence.periods:
        listed = ", ".join(evidence.periods[:3])
        more = (
            (" ve diğer dönemlerde" if tr else " and other periods")
            if len(evidence.periods) > 3
            else ""
        )
        if tr:
            extreme = "en yüksek" if evidence.id.endswith(":max") else "en düşük"
            text = (
                f"{evidence.period_from} ile {evidence.period_to} arasında {label} "
                f"{extreme} değeri {quote} ile {listed}{more} döneminde aldı."
            )
        else:
            extreme = "highest" if evidence.id.endswith(":max") else "lowest"
            text = (
                f"Between {evidence.period_from} and {evidence.period_to}, {label} was "
                f"{extreme} at {quote} in {listed}{more}."
            )
        return text, [ClaimBinding(evidence=evidence.key, quote=quote)]
    text = (
        f"{label}, {period} döneminde {quote} oldu." if tr else f"{label} was {quote} in {period}."
    )
    return text, [ClaimBinding(evidence=evidence.key, quote=quote)]


def finding_text(items: list[Evidence], language: str) -> tuple[str, list[ClaimBinding]]:
    """Sentences for every entry; one without a value says so, and the reason is a limitation."""
    parts, claims = [], []
    for evidence in items:
        if evidence.status != "valid":
            when = evidence.period or f"{evidence.period_from}–{evidence.period_to}"
            parts.append(f"{evidence.label}: {when}, {TEXT[language]['missing']}.")
            continue
        text, bound = sentence(evidence, language)
        parts.append(text)
        claims.extend(bound)
    return " ".join(parts), claims
