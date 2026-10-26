"""Hand-calculated ranking examples; no invented thesis relevance judgments."""

import importlib.util
import math
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "evaluate_search", Path(__file__).parents[1] / "scripts/evaluate_search.py"
)
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


def hit(number, kind="dataset"):
    return {
        "id": f"flow:DF_TEST_{number}+V1.0" if kind == "dataset" else f"press:{number}",
        "title": f"Test {number}",
        "source_url": "https://veriportali.tuik.gov.tr/",
        "resource_type": kind,
    }


def judgment(a, b, resolved=None, reason=""):
    return {"judge_a": a, "judge_b": b, "resolved": resolved, "reason": reason}


@pytest.fixture
def suite():
    return {
        "version": 1,
        "queries": [{"id": "q1", "text": "işsizlik", "category": "exact"}],
        "judgments": {},
    }


def run(hits, name="literal", corpus_ids=None):
    return {
        "queries": {"q1": "işsizlik"},
        "systems": {name: {"corpus": name, "corpus_ids": corpus_ids, "results": {"q1": hits}}},
    }


def test_fixture_has_balanced_queries_and_no_fabricated_labels():
    data = evaluation.validate_suite(
        evaluation.read_json(Path(__file__).parent / "fixtures/search_eval.json")
    )
    assert len(data["queries"]) == 48
    assert len({q["text"] for q in data["queries"]}) == 48
    assert set(evaluation.Counter(q["category"] for q in data["queries"]).values()) == {6}
    assert all(
        all(j.get(k) is None for k in ("judge_a", "judge_b", "resolved"))
        for docs in data["judgments"].values()
        for j in docs.values()
    )


def test_metrics_use_graded_dcg_and_binary_recall():
    docs = {hit(1)["id"]: judgment(2, 2), hit(2)["id"]: judgment(1, 1)}
    result = evaluation.query_metrics([hit(3), hit(2), hit(1)], docs)
    expected = (1 / math.log2(3) + 3 / math.log2(4)) / (3 + 1 / math.log2(3))
    assert result["ndcg_at_10"] == pytest.approx(expected)
    assert result["mrr"] == 0.5
    assert result["recall_at_10"] == 1
    assert result["unjudged"] == 1


def test_pool_is_union_of_top_ten_preserves_judgments_and_blinds_systems(suite):
    suite["judgments"] = {"q1": {hit(1)["id"]: judgment(2, 2)}}
    pooled = evaluation.pool(suite, [run([hit(i) for i in range(1, 12)]), run([hit(12)], "portal")])
    assert len(pooled["judgments"]["q1"]) == 11
    assert hit(11)["id"] not in pooled["documents"]
    assert pooled["judgments"]["q1"][hit(1)["id"]]["judge_a"] == 2
    assert hit(12)["id"] not in suite.get("documents", {})
    assert "systems" not in pooled and "rank" not in pooled["documents"][hit(1)["id"]]


def test_incomplete_or_duplicate_rankings_are_rejected(suite):
    invalid = run([hit(1)])
    invalid["systems"]["literal"]["results"].clear()
    with pytest.raises(ValueError, match="Incomplete"):
        evaluation.pool(suite, [invalid])
    with pytest.raises(ValueError, match="Duplicate IDs"):
        evaluation.pool(suite, [run([hit(1), hit(1)])])
    with pytest.raises(ValueError, match="outside"):
        evaluation.pool(suite, [run([hit(1)], corpus_ids=[])])
    invalid = run([hit(1)])
    invalid["queries"]["q1"] = "changed"
    with pytest.raises(ValueError, match="match"):
        evaluation.pool(suite, [invalid])


@pytest.mark.parametrize("grade", [True, 3, -1, "2", 1.0])
def test_invalid_grades_rejected(suite, grade):
    suite["judgments"] = {"q1": {hit(1)["id"]: judgment(grade, 2)}}
    with pytest.raises(ValueError, match="integer"):
        evaluation.validate_suite(suite)


def test_disagreement_requires_reason_and_cannot_bypass_second_judge(suite):
    assert evaluation.final_grade(judgment(2, 1)) is None
    suite["judgments"] = {"q1": {hit(1)["id"]: judgment(2, None, 2, "reason")}}
    with pytest.raises(ValueError, match="both independent"):
        evaluation.validate_suite(suite)
    suite["judgments"]["q1"][hit(1)["id"]] = judgment(2, 1, 2)
    with pytest.raises(ValueError, match="reason"):
        evaluation.validate_suite(suite)


def test_kappa_uses_original_labels_and_handles_undefined_agreement():
    result = evaluation.kappa([judgment(0, 0), judgment(1, 1), judgment(2, 1, 2, "resolved")])
    assert result["cohen_kappa"] == pytest.approx(0.5)
    assert result["agreement"] == pytest.approx(2 / 3)
    assert evaluation.kappa([judgment(2, 2)])["cohen_kappa"] is None
    assert evaluation.kappa([judgment(2, None)])["paired"] == 0


def test_empty_unjudged_and_different_corpora(suite):
    runs = [run([hit(1)], corpus_ids=[hit(1)["id"]]), run([hit(2, "bulletin")], "portal")]
    pending = evaluation.score(suite, runs)
    assert pending["status"] == "pending_judgments"
    assert pending["systems"]["literal"]["overall"]["unjudged_share"] == 1
    suite = evaluation.pool(suite, runs)
    suite["judgments"]["q1"] = {
        hit(1)["id"]: judgment(2, 2),
        hit(2, "bulletin")["id"]: judgment(2, 2),
    }
    report = evaluation.score(suite, runs)
    assert report["status"] == "complete"
    assert report["systems"]["literal"]["overall"]["recall_at_10"] == 1
    assert report["systems"]["portal"]["overall"]["recall_at_10"] == 0.5
    empty = evaluation.score(suite, [run([])])
    assert empty["systems"]["literal"]["overall"]["unjudged_share"] is None


def test_mrr_is_bounded_at_ten_and_unstable_ids_rejected():
    docs = {hit(11)["id"]: judgment(2, 2)}
    assert evaluation.query_metrics([hit(i) for i in range(1, 12)], docs)["mrr"] == 0
    with pytest.raises(ValueError, match="Session-dependent"):
        evaluation.stable_id("resource:" + "a" * 64)


def test_non_file_portal_keys_depend_on_title_and_type_not_url():
    from tuik_mcp.models.search import TuikSearchResult

    item = TuikSearchResult(
        id="resource:" + "a" * 64,
        title="İSTANBUL  Nüfusu",
        resource_type="unknown",
        source_url="https://veriportali.tuik.gov.tr/",
    )
    first = evaluation.evaluation_hit(item)
    item.id = "resource:" + "b" * 64
    item.title = "istanbul nüfusu"
    item.source_url += "different"
    assert evaluation.evaluation_hit(item)["id"] == first["id"]
    assert evaluation.stable_id(first["id"]) == first["id"]
    assert "evaluation_only" in first["identity_method"]


async def test_collection_checkpoints_errors_and_resumes_without_repeating_success(
    suite, monkeypatch, tmp_path
):
    from tuik_mcp.models.common import now
    from tuik_mcp.models.dataset import TuikResource
    from tuik_mcp.models.search import SearchResponse
    from tuik_mcp.services.catalog import CatalogService

    dataset = TuikResource(**hit(1), available_formats=["csv"])
    calls = []
    fail = True

    class FakeHttp:
        settings = evaluation.Settings()

        async def close(self):
            pass

    class FakeService(CatalogService):
        async def _datasets(self):
            cached = self.cache.get("datasets")
            return cached or (now(), [dataset])

        async def search(self, query, limit):
            calls.append("portal")
            if fail:
                raise RuntimeError("upstream unavailable")
            return SearchResponse(query=query, results=[dataset], source_url=dataset.source_url)

        async def search_datasets(self, query, limit):
            calls.append("literal")
            return SearchResponse(query=query, results=[dataset], source_url=dataset.source_url)

    monkeypatch.setattr(evaluation, "HttpClient", lambda settings: FakeHttp())
    monkeypatch.setattr(evaluation, "CatalogService", FakeService)
    output = tmp_path / "run.json"
    assert await evaluation.collect(suite, output) == 1
    failed = evaluation.read_json(output)
    assert "q1" not in failed["systems"]["portal"]["results"]
    with pytest.raises(ValueError, match="Incomplete"):
        evaluation.pool(suite, [failed])
    fail = False
    assert await evaluation.collect(suite, output, resume=True) == 0
    assert calls == ["portal", "literal", "portal"]
    resumed = evaluation.read_json(output)
    assert resumed["catalog_sha256"] == failed["catalog_sha256"]
    assert resumed["errors"] == {}
    evaluation.validate_run(resumed, suite)


def test_independent_judge_sheets_hide_other_labels_and_merge_validates_pool(suite):
    pooled = evaluation.pool(suite, [run([hit(1)])])
    pooled["judgments"]["q1"][hit(1)["id"]] = judgment(2, 1, 2, "adjudicated")
    a, b = (evaluation.judge_sheet(pooled, name) for name in ("a", "b"))
    assert a["grades"]["q1"][hit(1)["id"]] is None
    assert "judgments" not in a and "systems" not in a
    a["grades"]["q1"][hit(1)["id"]] = 0
    b["grades"]["q1"][hit(1)["id"]] = 1
    merged = evaluation.merge_judges(pooled, [a, b])
    assert merged["judgments"]["q1"][hit(1)["id"]] == {"judge_a": 0, "judge_b": 1, "resolved": None}
    b["grades"]["q1"].clear()
    with pytest.raises(ValueError, match="document IDs"):
        evaluation.merge_judges(pooled, [a, b])
