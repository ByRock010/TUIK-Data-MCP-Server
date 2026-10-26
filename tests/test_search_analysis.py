"""Regression observations captured from OpenSearch 3.3.2; no server required."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from tuik_mcp.search import analysis_settings

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "inspect_search_analysis", ROOT / "scripts/inspect_search_analysis.py"
)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
REPORT = json.loads((ROOT / "docs/verification/search-analysis-2026-10-26.json").read_text())


def tokens(text, analyzer):
    return [token["token"] for token in REPORT["tokens"][text][analyzer]]


def test_evidence_covers_real_engine_and_apostrophe_case_and_stemming_behavior():
    assert REPORT["status"] == "ok" and REPORT["cleanup"]["acknowledged"]
    assert REPORT["engine"]["number"] == "3.3.2"
    assert len(REPORT["tokens"]) >= 30
    assert tokens("İ", "tuik_tr_index") == tokens("i", "tuik_tr_index") == ["i"]
    assert tokens("I", "tuik_tr_index") == tokens("ı", "tuik_tr_index") == ["ı"]
    for text in ("İstanbul'un", "İstanbul’un"):
        assert tokens(text, "tuik_tr_index") == ["istanbul"]
    assert tokens("işsizliğin", "tuik_tr_index") == tokens("işsizlik", "tuik_tr_index")
    assert tokens("işsizliğin", "probe_fold_then_stem") != tokens(
        "işsizlik", "probe_fold_then_stem"
    )
    assert tokens("issizligin", "tuik_ascii_index") == ["issizligin"]


def test_synonym_graph_handles_phrases_hyphens_and_one_way_expansion():
    assert tokens("Yİ-ÜFE", "tuik_ascii_index") == ["yi", "ufe"]
    for text in ("Yİ-ÜFE", "YI-UFE"):
        assert "uretici" in tokens(text, "tuik_ascii_search")
    assert "tuketici" in tokens("TUFE", "tuik_ascii_search")
    assert {"tuketici", "uretici", "enflasyon"} <= set(tokens("enflasyon", "tuik_ascii_search"))
    assert "enflasyon" not in tokens("Tüketici Fiyat Endeksi", "tuik_ascii_search")
    assert tokens("ihracat", "tuik_ascii_search") == ["ihracat"]
    assert tokens("büyüme", "tuik_ascii_search") == ["buyume"]
    assert any(t.get("positionLength", 1) > 1 for t in REPORT["tokens"]["TÜFE"]["tuik_tr_search"])


def test_probe_records_successes_and_known_ascii_inflection_limit():
    def hits(text, variant):
        return REPORT["retrieval_probes"][text][variant]["hits"]

    assert "unemployment" in hits("işsizliğin", "multi_field")
    assert "unemployment" in hits("issizlik", "multi_field")
    assert "cpi" in hits("TÜFE", "multi_field_synonyms")
    assert "cpi" in hits("TUFE", "multi_field_synonyms")
    assert hits("TÜFE", "multi_field") == []
    for variant in ("fold_only", "fold_fuzzy", "ascii_edge", "multi_field_synonyms"):
        assert hits("issizligin", variant) == []
    assert hits("nufusun", "ascii_edge") == []
    assert "population" in hits("nufusun", "fold_fuzzy")
    assert hits("nüfusun", "builtin_turkish") == []
    assert "population" in hits("nüfusun", "fold_fuzzy")


def test_packaged_settings_are_independent_and_probe_uses_current_rules():
    settings = analysis_settings()
    recorded = REPORT["definition"]["settings"]["analysis"]
    # This guards against claiming that an edited rule set was actually measured.
    for section, entries in settings["analysis"].items():
        for name, value in entries.items():
            assert value == recorded[section][name]
    settings["analysis"]["filter"]["tuik_tr_synonyms"]["synonyms"].clear()
    assert analysis_settings()["analysis"]["filter"]["tuik_tr_synonyms"]["synonyms"]


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_probe_deletes_only_owned_index_after_analyze_failure(monkeypatch, tmp_path, cleanup_fails):
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/":
            return httpx.Response(200, json={"version": {"number": "3.3.2"}})
        if request.method == "PUT":
            return httpx.Response(200, json={"acknowledged": True})
        if request.method == "DELETE":
            return httpx.Response(500 if cleanup_fails else 200, json={"acknowledged": True})
        return httpx.Response(400, json={"error": "injected analyze failure"})

    client_type = httpx.Client
    monkeypatch.setattr(
        probe.httpx,
        "Client",
        lambda **kwargs: client_type(**kwargs, transport=httpx.MockTransport(handler)),
    )
    output = tmp_path / "report.json"
    with pytest.raises(httpx.HTTPStatusError):
        probe.inspect("http://localhost:9200", output)
    report = json.loads(output.read_text())
    assert report["status"] == "error"
    assert report["index"].startswith("tuik-analysis-probe-")
    assert calls[-1] == ("DELETE", "/" + report["index"])
    assert all(path == "/" or path.startswith("/" + report["index"]) for _, path in calls)
    assert ("cleanup_error" in report) is cleanup_fails
