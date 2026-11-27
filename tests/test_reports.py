"""Evidence-bound reports and numeric claim validation (#91)."""

import csv
import io
import json
from dataclasses import replace
from decimal import Decimal

import pytest
from pydantic import ValidationError
from test_observations import LABOUR, labour, labour_catalog

from tuik_mcp import artifacts
from tuik_mcp.models.calculation import UnitSpec
from tuik_mcp.models.report import SCHEMA_VERSION, ClaimBinding, Report, ReportSpec
from tuik_mcp.observations import BY_RESOURCE
from tuik_mcp.reports.claims import Fact, check_text
from tuik_mcp.reports.numbers import display, parse_number
from tuik_mcp.reports.render import escape, markdown
from tuik_mcp.server import reply, respond

RESOURCE = LABOUR.resource_ids[0]
RATE = {"İşgücü Göstergeleri": "İşsizlik Oranı (%)"}
UNEMPLOYED = {"İşgücü Göstergeleri": "İşsiz (Bin Kişi)"}
RATES = {
    "2025-01": "9,0",
    "2025-02": "8,8",
    "2025-03": "8,6",
    "2025-12": "8,2",
    "2026-01": "8,4",
    "2026-02": "8,1",
    "2026-03": "8,0",
}


def rows(filters: dict, values: dict[str, str], **fixed) -> list[dict[str, str]]:
    return [
        {**filters, "Zaman": period, "Gözlem Değeri": value, **fixed}
        for period, value in values.items()
    ]


DATA = labour(
    *rows(RATE, RATES),
    *rows(UNEMPLOYED, {"2025-03": "3402", "2026-03": "3214"}),
)


def observation(key, period, filters=RATE, **extra):
    return {
        "kind": "observation",
        "key": key,
        "resource_id": RESOURCE,
        "filters": filters,
        "period": period,
        **extra,
    }


def calculation(key, operation, filters=RATE, **extra):
    return {
        "kind": "calculation",
        "key": key,
        "resource_id": RESOURCE,
        "filters": filters,
        "operation": operation,
        **extra,
    }


EVIDENCE = [
    observation("rate", "2026-03"),
    calculation("gap", "pp_difference", basis="same_period_previous_year", period="2026-03"),
    calculation("peak", "extrema", extremum="max", period_from="2025-01", period_to="2026-03"),
    observation("unemployed", "2026-03", UNEMPLOYED),
    calculation(
        "fewer", "difference", UNEMPLOYED, basis="same_period_previous_year", period="2026-03"
    ),
    {
        "kind": "series",
        "key": "trend",
        "resource_id": RESOURCE,
        "filters": RATE,
        "period_from": "2025-01",
        "period_to": "2026-03",
    },
]


def report_spec(*sections, evidence=EVIDENCE, **fields) -> dict:
    return {"title": "İşgücü bülteni", "evidence": evidence, "sections": list(sections), **fields}


def summary(*findings) -> dict:
    return {"kind": "summary", "findings": list(findings)}


def host(text: str, *claims: tuple[str, str], evidence=None) -> dict:
    keys = evidence or list(dict.fromkeys(key for key, _ in claims)) or ["rate"]
    return {
        "evidence": keys,
        "text": text,
        "claims": [{"evidence": key, "quote": quote} for key, quote in claims],
    }


async def build(spec: dict, tmp_path, data: bytes = DATA, **settings) -> dict:
    http, catalog = labour_catalog(data, artifact_dir=str(tmp_path), **settings)
    try:
        return await respond(catalog.report(spec))
    finally:
        await http.close()


def read(payload: dict, name: str, root) -> bytes:
    """A bundle file, found by the artifact ID and name in its resource URI."""
    (entry,) = [f for f in payload["artifact"]["files"] if f["name"] == name]
    artifact_id, file_name = artifacts.parse_uri(entry["uri"])
    return next(root.rglob(f"{artifact_id}/{file_name}")).read_bytes()


def findings(payload: dict) -> list[dict]:
    return [f for section in payload["sections"] for f in section["findings"]]


async def test_a_template_report_is_reproducible_without_any_language_model(tmp_path):
    spec = report_spec(
        summary({"evidence": ["rate", "gap"]}, {"evidence": ["peak"]}),
        {"kind": "findings", "findings": [{"evidence": ["unemployed", "fewer"]}]},
        {"kind": "table", "table": {"rows": ["rate", "gap", "peak", "unemployed"]}},
        {"kind": "chart", "chart": {"series": ["trend"]}},
    )
    first = await build(spec, tmp_path / "a")
    second = await build(spec, tmp_path / "b")
    assert first["schema_version"] == SCHEMA_VERSION and first["status"] == "ok"
    assert first["report_id"] == second["report_id"]
    assert [f["text"] for f in findings(first)] == [f["text"] for f in findings(second)]
    assert [f["text"] for f in findings(first)] == [
        "İşsizlik Oranı (%), 2026-03 döneminde %8,00 oldu. İşsizlik Oranı (%), puan farkı, "
        "2026-03 döneminde 2025-03 dönemine göre 0,60 yüzde puan azaldı.",
        "2025-01 ile 2026-03 arasında İşsizlik Oranı (%) en yüksek değeri %9,00 ile 2025-01 "
        "döneminde aldı.",
        "İşsiz (Bin Kişi), 2026-03 döneminde 3.214,00 bin kişi oldu. İşsiz (Bin Kişi), fark, "
        "2026-03 döneminde 2025-03 dönemine göre 188,00 bin kişi azaldı.",
    ]
    assert all(f["origin"] == "template" and f["status"] == "verified" for f in findings(first))
    assert first["counts"] == {"evidence": 6, "findings": 3, "verified": 3, "unverified": 0}

    evidence = {item["key"]: item for item in first["evidence"]}
    # Published values cite observation IDs; derived values cite deterministic calculation IDs.
    assert evidence["rate"]["id"].startswith("obs:") and not evidence["rate"]["derived"]
    assert evidence["gap"]["id"] == evidence["gap"]["calculation_id"] + ":2026-03"
    assert evidence["gap"]["calculation_id"] == second["evidence"][1]["calculation_id"]
    assert evidence["gap"]["value"] == "-0.6" and evidence["gap"]["reference_period"] == "2025-03"
    assert evidence["gap"]["unit"]["kind"] == "percentage_points" and evidence["gap"]["derived"]
    assert evidence["peak"]["id"].endswith(":max") and evidence["peak"]["periods"] == ["2025-01"]
    assert evidence["unemployed"]["display"] == "3.214,00 bin kişi"
    assert len(evidence["trend"]["points"]) == 15

    kinds = [section["kind"] for section in first["sections"]]
    # Methods, limitations and sources are generated and always present.
    assert kinds == ["summary", "findings", "table", "chart", "methods", "limitations", "sources"]
    methods = first["sections"][4]["items"]
    assert any(evidence["gap"]["calculation_id"] in line for line in methods)
    chart = first["sections"][3]["chart"]
    assert chart["unit"]["kind"] == "percent" and "en yüksek %9,00 (2025-01)" in chart["alt_text"]

    names = [f["name"] for f in first["artifact"]["files"]]
    assert names == ["report.json", "report.md", "evidence.csv", "README.txt", "source-1.csv"]
    saved = Report.model_validate_json(read(first, "report.json", tmp_path / "a"))
    assert saved.report_id == first["report_id"] and saved.spec == ReportSpec(**spec)
    table = list(
        csv.DictReader(io.StringIO(read(first, "evidence.csv", tmp_path / "a").decode("utf-8-sig")))
    )
    assert {row["key"] for row in table} == set(evidence)
    assert sum(row["kind"] == "point" for row in table) == 15


async def test_english_template_and_host_narrative(tmp_path):
    spec = report_spec(
        summary(
            {"evidence": ["rate", "gap"]},
            host(
                "In March 2026 unemployment was 8.0%, down 0.6 percentage points from "
                "March 2025; unemployed persons fell by 188 thousand to 3.2 million.",
                ("rate", "8.0%"),
                ("gap", "0.6 percentage points"),
                ("fewer", "188 thousand"),
                ("unemployed", "3.2 million"),
            ),
        ),
        language="en",
    )
    payload = await build(spec, tmp_path)
    assert payload["status"] == "ok", findings(payload)
    template, narrative = findings(payload)
    assert template["text"] == (
        "İşsizlik Oranı (%) was 8.00% in 2026-03. İşsizlik Oranı (%), percentage-point "
        "change decreased by 0.60 percentage points in 2026-03 compared with 2025-03."
    )
    assert narrative["origin"] == "host" and narrative["status"] == "verified"
    assert [c["status"] for c in narrative["claims"]] == ["verified"] * 4


@pytest.mark.parametrize(
    ("text", "claims", "problem"),
    [
        # Changed sign: the year-ago difference is −0.6 points.
        (
            "Mart 2026'da işsizlik oranı bir yıl önceye göre 0,6 yüzde puan arttı.",
            [("gap", "0,6 yüzde puan")],
            "contradicts the sign",
        ),
        ("Fark +0,6 yüzde puan oldu.", [("gap", "+0,6 yüzde puan")], "does not match gap"),
        (
            "Fark 0,6 yüzde puan oldu.",
            [("gap", "0,6 yüzde puan")],
            "write a minus sign or a decrease word",
        ),
        # Changed percentages: a rate is %, a difference of rates is points.
        ("Oran 8,0 puan oldu.", [("rate", "8,0 puan")], "is a percent value"),
        ("Oran %0,6 azaldı.", [("gap", "%0,6")], "percentage points"),
        ("Oran %8,5 oldu.", [("rate", "%8,5")], "does not match rate"),
        ("İşsiz sayısı 3.214 kişi.", [("unemployed", "3.214 kişi")], "3.214.000"),
        # Changed dates.
        ("Şubat 2026'da oran %8,0 oldu.", [("rate", "%8,0")], "date 'Şubat 2026'"),
        ("2026-02 döneminde oran %8,0 oldu.", [("rate", "%8,0")], "date '2026-02'"),
        ("2024 yılında oran %8,0 oldu.", [("rate", "%8,0")], "date '2024'"),
        # Unsupported comparisons.
        (
            "Oran %8,0 ile 2025'e göre daha düşük.",
            [("rate", "%8,0")],
            "unsupported comparison 'daha düşük'",
        ),
        ("Oran %8,0 ile en düşük seviyede.", [("rate", "%8,0")], "unsupported comparison"),
        ("Oran %8,0 oldu ve geriledi.", [("rate", "%8,0")], "no change or difference"),
        # Unbound and misleading numbers.
        ("Oran %8,0 oldu; 12 ayın en iyisi.", [("rate", "%8,0")], "number '12' is not bound"),
        ("Oran 8.0% oldu.", [("rate", "8.0%")], "tr separators"),
        ("Oran %8,0 oldu.", [("rate", "%8,1")], "does not occur in the text"),
    ],
)
async def test_narrative_with_changed_values_is_rejected(tmp_path, text, claims, problem):
    payload = await build(report_spec(summary(host(text, *claims))), tmp_path)
    assert payload["status"] == "rejected" and payload["artifact"] is None
    (finding,) = findings(payload)
    messages = finding["problems"] + [p for c in finding["claims"] for p in c["problems"]]
    assert any(problem in message for message in messages), messages
    assert not list(tmp_path.rglob("report.json"))


async def test_supported_comparisons_and_dates_pass(tmp_path):
    spec = report_spec(
        summary(
            host(
                "Mart 2026'da oran %8,0 oldu; Mart 2025'e göre 0,6 yüzde puan daha düşük.",
                ("rate", "%8,0"),
                ("gap", "0,6 yüzde puan"),
            ),
            host(
                "Oran 2025-01 ile 2026-03 arasında en yüksek %9,0 ile Ocak 2025'te görüldü.",
                ("peak", "%9,0"),
            ),
            host("Fark -0,6 yüzde puan oldu.", ("gap", "-0,6 yüzde puan")),
            host("İşsiz sayısı 2026 yılında 3.214 bin kişi.", ("unemployed", "3.214 bin")),
        )
    )
    payload = await build(spec, tmp_path)
    assert payload["status"] == "ok", [f["problems"] for f in findings(payload)]


async def test_marked_drafts_keep_unverified_text_visible(tmp_path):
    text = "Oran %8,5 oldu ve 12 ayın en iyisi."
    spec = report_spec(
        summary({"evidence": ["rate"]}, host(text, ("rate", "%8,5"))),
        on_unverified="mark",
    )
    payload = await build(spec, tmp_path)
    assert payload["status"] == "draft" and payload["message"].startswith("DRAFT")
    template, draft = findings(payload)
    assert template["status"] == "verified" and draft["status"] == "unverified"
    assert payload["counts"]["unverified"] == 1
    assert any("Doğrulanmamış taslak içerik" in item for item in payload["limitations"])
    rendered = read(payload, "report.md", tmp_path).decode()
    assert "**DOĞRULANMAMIŞ TASLAK:** Oran %8,5 oldu ve 12 ayın en iyisi\\." in rendered


async def test_titles_state_no_unbound_numbers(tmp_path):
    spec = report_spec(
        {"kind": "table", "title": "İşsizlik %20 arttı", "table": {"rows": ["rate"]}},
        title="2026-03 işgücü bülteni",
    )
    payload = await build(spec, tmp_path)
    assert payload["status"] == "rejected"
    assert payload["problems"] == [
        "section 1 title: 'arttı' states a change, but no change or difference is bound in "
        "the same sentence",
        "section 1 title: number '20' is not bound to evidence; add a claim with its quote",
    ]


async def test_four_dates_stay_distinct_and_unknown_release_stays_unknown(tmp_path):
    payload = await build(report_spec(summary({"evidence": ["rate"]})), tmp_path)
    timeline = payload["timeline"]
    # The fixture's flow record has no release date: it is unknown, not the retrieval time.
    assert timeline["latest_release"] is None
    assert timeline["release_unknown"] == [RESOURCE]
    assert timeline["latest_observation"] == "2026-03"
    assert timeline["retrieved_from"] == timeline["retrieved_to"]
    assert timeline["data_cutoff"] is None
    assert any("yayım tarihi bilinmiyor" in item for item in payload["limitations"])

    cut = await build(
        report_spec(summary({"evidence": ["rate"]}), source_cutoff="2026-02-28"), tmp_path
    )
    assert cut["code"] == "invalid_input" and "ends after source_cutoff" in cut["message"]
    kept = await build(
        report_spec(summary({"evidence": ["rate"]}), source_cutoff="2026-04-30"), tmp_path
    )
    assert kept["timeline"]["data_cutoff"] == "2026-04-30"
    assert any("veri kesim tarihinden" in item for item in kept["limitations"])


async def test_release_after_the_cutoff_is_refused(tmp_path, monkeypatch):
    http, catalog = labour_catalog(DATA, artifact_dir=str(tmp_path))
    resource = catalog.resource

    async def released(resource_id):
        return (await resource(resource_id)).model_copy(
            update={"publication_date": "2026-04-15T10:00:00"}
        )

    catalog.resource = released
    try:
        refused = await respond(
            catalog.report(report_spec(summary({"evidence": ["rate"]}), source_cutoff="2026-03-31"))
        )
        allowed = await respond(catalog.report(report_spec(summary({"evidence": ["rate"]}))))
    finally:
        await http.close()
    assert refused["code"] == "invalid_input"
    assert "released on 2026-04-15, after source_cutoff" in refused["message"]
    assert allowed["timeline"]["latest_release"] == "2026-04-15T10:00:00"
    assert allowed["timeline"]["release_unknown"] == []


async def test_gaps_adjustment_and_method_notes_become_limitations(tmp_path, monkeypatch):
    note = "Method changed in 2021; earlier months are not comparable."
    monkeypatch.setitem(BY_RESOURCE, RESOURCE, replace(LABOUR, notes=(note,)))
    data = labour(
        *rows(RATE, RATES),
        *rows(
            RATE, {"2026-03": "7,9"}, **{"Mevsimsel Düzeltme": "Mevsim etkilerinden arındırılmış"}
        ),
    )
    adjusted = {**RATE, "Mevsimsel Düzeltme": "Mevsim etkilerinden arındırılmış"}
    raw = {**RATE, "Mevsimsel Düzeltme": "Arındırılmamış"}
    evidence = [
        observation("raw", "2026-03", raw),
        observation("adjusted", "2026-03", adjusted),
        observation("absent", "2025-06", raw),
        {
            "kind": "series",
            "key": "trend",
            "resource_id": RESOURCE,
            "filters": raw,
            "period_from": "2025-01",
            "period_to": "2026-03",
        },
    ]
    spec = report_spec(
        {"kind": "table", "table": {"rows": ["raw", "adjusted", "absent"]}},
        {"kind": "chart", "chart": {"series": ["trend"]}},
        summary({"evidence": ["absent"]}),
        evidence=evidence,
    )
    payload = await build(spec, tmp_path, data)
    limitations = payload["limitations"]
    assert payload["status"] == "ok"
    assert any("2025-06 için değer yok (no source record for 2025-06)" in i for i in limitations)
    assert any("2025-01–2026-03 aralığındaki her dönem" in i for i in limitations)
    assert any("arındırılmamış değerler birlikte" in i for i in limitations)
    assert f"{LABOUR.title} kaynak notu: {note}" in limitations
    (absent,) = findings(payload)
    assert absent["text"] == "İşsizlik Oranı (%) (Arındırılmamış): 2025-06, değer yok."
    chart = payload["sections"][1]["chart"]
    assert "8 dönem değersiz" in chart["alt_text"]
    assert [p["status"] for p in chart["series"][0]["points"]][3:5] == ["missing", "missing"]


async def test_untrusted_text_is_escaped_never_executed_or_fetched(tmp_path):
    spec = report_spec(
        summary({"evidence": ["rate"]}),
        title="<script>alert('x')</script> ![x](https://example.com/a.png) {{ config }}",
        subtitle="[tıkla](javascript:alert) # başlık | tablo",
    )
    payload = await build(spec, tmp_path)
    assert payload["status"] == "ok"
    rendered = read(payload, "report.md", tmp_path).decode()
    first = rendered.splitlines()[0]
    assert first == (
        "# \\<script\\>alert\\('x'\\)\\<\\/script\\> \\!\\[x\\]\\(https\\:\\/\\/example\\.com"
        "\\/a\\.png\\) \\{\\{ config \\}\\}"
    )
    assert "](" not in rendered.replace("\\](", "").replace("\\(", "")
    assert "<script>" not in rendered and "javascript:" not in rendered
    # The report itself keeps the text as data, unchanged.
    assert payload["title"].startswith("<script>")
    for hidden in ("a‮b", "a\x00b", "a\nb"):
        with pytest.raises(ValidationError, match="control or formatting"):
            ReportSpec(**report_spec(summary({"evidence": ["rate"]}), title=hidden))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"evidence": EVIDENCE + [observation("rate", "2026-01")]}, "Duplicate evidence key"),
        ({"sections": [summary({"evidence": ["nope"]})]}, "unknown evidence 'nope'"),
        (
            {"sections": [{"kind": "chart", "chart": {"series": ["rate"]}}]},
            "needs series evidence",
        ),
        ({"sections": [{"kind": "table", "table": {"rows": ["trend"]}}]}, "needs calculation"),
        (
            {"sections": [{"kind": "table", "findings": [{"evidence": ["rate"]}]}]},
            "takes table and nothing else",
        ),
        (
            {"sections": [{"kind": "methods", "table": {"rows": ["rate"]}}]},
            "generated from the evidence",
        ),
        ({"sections": [summary({"evidence": ["rate"]})] * 13}, "at most 12"),
        ({"sections": [{"kind": "limitations"}, {"kind": "limitations"}]}, "may each appear once"),
        ({"sections": [{"kind": "slides"}]}, "Input should be"),
        (
            {"evidence": [observation("rate", "2026-03", label="Oran %20")]},
            "may not contain digits",
        ),
        (
            {
                "sections": [
                    summary({"evidence": ["rate"], "claims": [{"evidence": "rate", "quote": "%8"}]})
                ]
            },
            "give text or drop claims",
        ),
        (
            {"evidence": [calculation("x", "extrema", period="2026-03")]},
            "extrema takes extremum",
        ),
    ],
)
def test_schema_refuses_unsupported_layouts_and_references(change, message):
    with pytest.raises(ValidationError, match=message):
        ReportSpec(**{**report_spec(summary({"evidence": ["rate"]})), **change})


async def test_selection_errors_name_the_evidence(tmp_path):
    several = report_spec(
        summary({"evidence": ["all"]}), evidence=[observation("all", "2026-03", {})]
    )
    assert "all: the filters leave several series" in (await build(several, tmp_path))["message"]
    mixed = report_spec(
        {"kind": "chart", "chart": {"series": ["rates", "people"]}},
        evidence=[
            {**EVIDENCE[5], "key": "rates"},
            {**EVIDENCE[5], "key": "people", "filters": UNEMPLOYED},
        ],
    )
    assert "never get a second axis" in (await build(mixed, tmp_path))["message"]
    held = report_spec(
        summary({"evidence": ["x"]}),
        evidence=[observation("x", "2026-03", {}, resource_id="flow:DF_UNKNOWN+V1.0")],
    )
    assert (await build(held, tmp_path))["code"] == "unsupported"


async def test_an_oversized_report_keeps_its_bundle_and_shrinks_inline(tmp_path):
    spec = report_spec(
        summary({"evidence": ["rate", "gap"]}),
        {"kind": "chart", "chart": {"series": ["trend"]}},
    )
    payload = await build(spec, tmp_path, query_max_output_bytes=4096)
    assert payload["status"] == "ok" and not payload["inline_complete"]
    assert payload["artifact"]["files"][0]["name"] == "report.json"
    assert len(json.dumps(payload, ensure_ascii=False).encode()) < 8192
    complete = Report.model_validate_json(read(payload, "report.json", tmp_path))
    assert len(complete.evidence) == 6 and len(complete.evidence[5].points) == 15
    links = [c for c in reply(payload).content if c.type == "resource_link"]
    assert [link.name for link in links][:2] == ["report.json", "report.md"]


def fact(key, value, kind, *, change=False, extremum=None, periods=("2026-03",), multiplier=0):
    return Fact(
        key=key,
        status="valid",
        reason=None,
        value=Decimal(value),
        unit=UnitSpec(kind=kind, multiplier=multiplier),
        change=change,
        extremum=extremum,
        periods=set(periods),
    )


@pytest.mark.parametrize(
    ("language", "text", "quote", "facts", "ok"),
    [
        (
            "tr",
            "2026 3. çeyrekte %8,0.",
            "%8,0",
            [fact("r", "8", "percent", periods=["2026-Q3"])],
            True,
        ),
        (
            "tr",
            "2026 2. çeyrekte %8,0.",
            "%8,0",
            [fact("r", "8", "percent", periods=["2026-Q3"])],
            False,
        ),
        ("en", "Q3 2026: 8%.", "8%", [fact("r", "8.04", "percent", periods=["2026-Q3"])], True),
        (
            "en",
            "In 2026-H1 it was 8.0%.",
            "8.0%",
            [fact("r", "8", "percent", periods=["2026-H1"])],
            True,
        ),
        (
            "tr",
            "İhracat 1,2 milyar TL.",
            "1,2 milyar TL",
            [fact("x", "1204", "currency", multiplier=6)],
            True,
        ),
        (
            "tr",
            "İhracat 1,2 milyar USD.",
            "1,2 milyar USD",
            [fact("x", "1204", "currency", multiplier=6)],
            True,
        ),
        (
            "en",
            "Exports reached a record 1.2 billion.",
            "1.2 billion",
            [fact("x", "1.2", "currency", multiplier=9, extremum="max")],
            True,
        ),
        (
            "en",
            "Index fell 2.5% to its lowest level.",
            "2.5%",
            [fact("c", "-2.5", "percent", change=True)],
            False,
        ),
        ("tr", "Endeks %2,5 düştü.", "%2,5", [fact("c", "-2.5", "percent", change=True)], True),
        ("tr", "Endeks %-2,5 değişti.", "%-2,5", [fact("c", "-2.5", "percent", change=True)], True),
        ("tr", "Endeks değişmedi (%0,0).", "%0,0", [fact("c", "0", "percent", change=True)], True),
        ("tr", "Endeks 132,45 oldu.", "132,45", [fact("i", "132.448", "index")], True),
        ("tr", "Endeks %132,45 oldu.", "%132,45", [fact("i", "132.448", "index")], False),
    ],
)
def test_claim_checks(language, text, quote, facts, ok):
    by_key = {f.key: f for f in facts}
    allowed = set().union(*(f.periods for f in facts))
    claims, problems = check_text(
        text, [ClaimBinding(evidence=facts[0].key, quote=quote)], by_key, language, [], allowed
    )
    assert (not problems and claims[0].status == "verified") is ok, (problems, claims)


def test_numbers_follow_the_report_language():
    assert parse_number("1.234,5", "tr") == (Decimal("1234.5"), 1)
    assert parse_number("1.234", "tr") == (Decimal("1234"), 0)
    assert parse_number("1.234", "en") == (Decimal("1.234"), 3)
    assert parse_number("8.4", "tr") is None and parse_number("8,4", "en") is None
    assert parse_number("1,23,4", "en") is None
    persons = UnitSpec(kind="persons", multiplier=3)
    assert display(Decimal("3214"), 0, persons, "tr") == "3.214 bin kişi"
    assert display(Decimal("-0.004"), 2, UnitSpec(kind="percent"), "en") == "0.00%"
    assert display(Decimal("1234.5"), 1, UnitSpec(kind="currency", currency="TRY"), "tr") == (
        "1.234,5 TL"
    )
    assert escape("a*b_[c](d)") == "a\\*b\\_\\[c\\]\\(d\\)"


def test_markdown_rendering_is_a_pure_function_of_the_report():
    report = Report.model_validate(
        {
            "source_url": "https://veriportali.tuik.gov.tr",
            "status": "ok",
            "message": "m",
            "report_id": "rpt:0",
            "title": "T",
            "language": "en",
            "timeline": {
                "retrieved_from": "2026-11-27T00:00:00Z",
                "retrieved_to": "2026-11-27T00:00:00Z",
            },
            "counts": {"evidence": 0, "findings": 0, "verified": 0, "unverified": 0},
        }
    )
    assert markdown(report) == markdown(report)
    assert "- Latest release: unknown" in markdown(report)
