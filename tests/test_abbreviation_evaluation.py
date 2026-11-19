"""Archived routing must preserve the #21 pool and fail closed on changed evidence."""

import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
EVIDENCE = ROOT / "docs/verification"
SPEC = importlib.util.spec_from_file_location(
    "abbreviation_evaluation", ROOT / "scripts/evaluate_search.py"
)
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


@pytest.fixture
def archives():
    return tuple(
        evaluation.read_json(EVIDENCE / name)
        for name in (
            "search-hybrid-pool-2026-11-05.json.gz",
            "search-full-variants-2026-11-05.json.gz",
            "search-hybrid-variants-2026-11-05.json.gz",
        )
    )


def reviewed_scores(suite, lexical, hybrid, routed):
    return evaluation.score_ai(
        suite,
        [
            evaluation.read_json(EVIDENCE / "search-full-baseline-2026-11-05.json.gz"),
            lexical,
            hybrid,
            routed,
        ],
        evaluation.read_json(EVIDENCE / "search-hybrid-ai-assessment-2026-11-05.json"),
        evaluation.read_json(EVIDENCE / "search-hybrid-ai-reviewed-assessment-2026-11-05.json"),
        evaluation.read_json(EVIDENCE / "search-hybrid-ai-live-evidence-2026-11-05.json.gz"),
    )


async def test_archived_routing_meets_fixed_thresholds_without_new_pairs(archives):
    suite, lexical, hybrid = archives
    before = deepcopy(archives)
    routed = await evaluation.route_abbreviations(*archives)
    assert routed["method"] == "archived_routing_replay"
    system = routed["systems"]["hybrid_abbreviation_lexical"]
    switched = {
        qid
        for qid, route in system["routing"].items()
        if route["source_system"] == "multi_field_synonyms"
    }
    assert switched == {q["id"] for q in suite["queries"] if q["category"] == "abbreviation"}
    for qid, route in system["routing"].items():
        run = lexical if qid in switched else hybrid
        assert system["results"][qid] == run["systems"][route["source_system"]]["results"][qid]
    scores = reviewed_scores(suite, lexical, hybrid, routed)
    assert scores["status"] == "ai_reviewed" and scores["judgments"] == 1689
    metrics = scores["systems"]["hybrid_abbreviation_lexical"]
    assert metrics["overall"]["ndcg_at_10"] >= 0.8516
    assert metrics["by_category"]["abbreviation"]["ndcg_at_10"] >= 0.911
    assert metrics["overall"]["unjudged_share"] == 0
    assert archives == before


@pytest.mark.parametrize(
    "changed", ["corpus_sha256", "engine", "analysis", "mapping", "request", "ids"]
)
async def test_replay_rejects_incompatible_archives(archives, changed):
    suite, lexical, hybrid = archives
    system = hybrid["systems"]["hybrid_rrf_equal"]
    if changed == "corpus_sha256":
        hybrid[changed] = "different"
    elif changed == "engine":
        hybrid[changed]["number"] = "different"
    elif changed == "analysis":
        system["definition"]["settings"]["analysis"]["filter"]["tuik_stemmer"] = {}
    elif changed == "mapping":
        system["definition"]["mappings"]["properties"]["title"]["analyzer"] = "different"
    elif changed == "request":
        system["query_template"]["query"]["hybrid"]["pagination_depth"] = 100
    else:
        system["corpus_ids"] = list(reversed(system["corpus_ids"]))
    with pytest.raises(ValueError):
        await evaluation.route_abbreviations(suite, lexical, hybrid)


async def test_replay_rejects_a_query_without_archived_lexical_evidence(archives):
    suite, lexical, hybrid = archives
    lexical["systems"]["multi_field_synonyms"]["results"]["q01"] = []
    with pytest.raises(ValueError, match="lexical-evidence guard"):
        await evaluation.route_abbreviations(suite, lexical, hybrid)


async def test_new_pair_cannot_reuse_the_existing_assessment(archives):
    suite, lexical, hybrid = archives
    routed = await evaluation.route_abbreviations(*archives)
    result = routed["systems"]["hybrid_abbreviation_lexical"]["results"]["q11"][0]
    result["id"] = next(
        doc_id
        for doc_id in lexical["systems"]["multi_field_synonyms"]["corpus_ids"]
        if doc_id not in suite["judgments"]["q11"]
    )
    with pytest.raises(ValueError, match="pooled source evidence"):
        reviewed_scores(suite, lexical, hybrid, routed)


def test_cli_does_not_overwrite_archived_input_or_accept_missing_runs(tmp_path):
    suite = EVIDENCE / "search-hybrid-pool-2026-11-05.json.gz"
    with pytest.raises(SystemExit):
        evaluation.main(["route-abbreviations", "--suite", str(suite), "--output", str(suite)])
    with pytest.raises(SystemExit):
        evaluation.main(
            ["route-abbreviations", "--suite", str(suite), "--output", str(tmp_path / "run.json")]
        )
