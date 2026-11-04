"""Human CSV round trips and explicit separation of single-AI pilot evidence."""

import csv
import hashlib
import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "evaluation_handoff", Path(__file__).parents[1] / "scripts/evaluate_search.py"
)
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


@pytest.fixture
def study(tmp_path):
    hits = [
        {
            "id": f"flow:DF_TEST_{n}+V1.0",
            "title": "İşsizlik, yaş grubu\nİstanbul" if n == 1 else '=HYPERLINK("example")',
            "description": "Genç kadın işsizlik oranı",
            "resource_type": "dataset",
            "source_url": "https://veriportali.tuik.gov.tr/",
        }
        for n in (1, 2)
    ]
    suite = {
        "version": 1,
        "queries": [{"id": "q1", "text": "işsizlik", "category": "exact"}],
        "judgments": {},
    }
    run = {
        "queries": {"q1": "işsizlik"},
        "systems": {"example": {"corpus": "example", "results": {"q1": hits}}},
    }
    suite = evaluation.pool(suite, [run])
    paths = {name: tmp_path / f"{name}.json" for name in ("suite", "run", "output")}
    evaluation.write_json(paths["suite"], suite)
    evaluation.write_json(paths["run"], run)
    return suite, run, paths


def edit_csv(path, edit):
    with path.open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    edit(rows)
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=evaluation.JUDGE_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def test_csv_cli_round_trip_blinds_labels_and_handles_turkish_and_formulas(study, tmp_path):
    suite, _, paths = study
    ids = list(suite["judgments"]["q1"])
    suite["judgments"]["q1"][ids[0]] = {"judge_a": 2, "judge_b": 1, "resolved": 1, "reason": "test"}
    evaluation.write_json(paths["suite"], suite)
    for judge, grades in (("a", (2, 0)), ("b", (2, 1))):
        path = tmp_path / f"judge-{judge}.csv"
        assert (
            evaluation.main(
                [
                    "judge",
                    "--suite",
                    str(paths["suite"]),
                    "--judge",
                    judge,
                    "--format",
                    "csv",
                    "--output",
                    str(path),
                ]
            )
            == 0
        )
        blank = evaluation.read_judge_sheet(path, suite, judge)
        assert set(blank["grades"]["q1"].values()) == {None}
        with path.open(encoding="utf-8-sig", newline="") as source:
            rows = list(csv.DictReader(source))
        assert rows[0]["title"] == "İşsizlik, yaş grubu\nİstanbul"
        assert rows[1]["title"].startswith("'=")
        assert not {"score", "rank", "system", "judge_a", "judge_b"} & set(rows[0])
        edit_csv(
            path,
            lambda rows, grades=grades: [
                r.update(grade=str(g)) for r, g in zip(rows, grades, strict=True)
            ],
        )
    assert (
        evaluation.main(
            [
                "merge",
                "--suite",
                str(paths["suite"]),
                "--judge-a",
                str(tmp_path / "judge-a.csv"),
                "--judge-b",
                str(tmp_path / "judge-b.csv"),
                "--output",
                str(paths["output"]),
            ]
        )
        == 0
    )
    merged = evaluation.read_json(paths["output"])
    assert evaluation.final_grade(merged["judgments"]["q1"][ids[0]]) == 2
    assert evaluation.final_grade(merged["judgments"]["q1"][ids[1]]) is None
    assert "reason" not in merged["judgments"]["q1"][ids[0]]


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "id", "query", "judge", "title", "grade"]
)
def test_csv_rejects_incomplete_wrong_or_modified_evidence(study, tmp_path, mutation):
    suite, _, _ = study
    path = tmp_path / "judge.csv"
    evaluation.write_judge_csv(path, suite, "a")

    def edit(rows):
        if mutation == "missing":
            rows.pop()
        elif mutation == "duplicate":
            rows.append(rows[0])
        else:
            field = {
                "id": "document_id",
                "query": "query",
                "judge": "judge",
                "title": "title",
                "grade": "grade",
            }[mutation]
            rows[0][field] = "3" if mutation == "grade" else "changed"

    edit_csv(path, edit)
    original = path.read_bytes()
    with pytest.raises(ValueError):
        evaluation.read_judge_sheet(path, suite, "a")
    assert path.read_bytes() == original


def test_json_judge_format_remains_supported(study, tmp_path):
    suite, _, _ = study
    path = tmp_path / "judge.json"
    sheet = evaluation.judge_sheet(suite, "a")
    evaluation.write_json(path, sheet)
    assert evaluation.read_judge_sheet(path, suite, "a") == sheet


def test_strict_human_scoring_fails_for_missing_labels_and_unresolved_disagreements(study):
    suite, _, paths = study
    argv = [
        "score",
        "--suite",
        str(paths["suite"]),
        "--runs",
        str(paths["run"]),
        "--output",
        str(paths["output"]),
    ]
    assert evaluation.main(argv) == 0  # Historical pending-report behavior.
    assert evaluation.main([*argv, "--require-complete"]) == 1
    assert evaluation.read_json(paths["output"])["status"] == "pending_judgments"
    values = suite["judgments"]["q1"].values()
    for judgment in values:
        judgment.update(judge_a=2, judge_b=1)
    evaluation.write_json(paths["suite"], suite)
    assert evaluation.main([*argv, "--require-complete"]) == 1
    for judgment in values:
        judgment.update(resolved=2, reason="Synthetic test resolution")
    evaluation.write_json(paths["suite"], suite)
    assert evaluation.main([*argv, "--require-complete"]) == 0
    assert evaluation.read_json(paths["output"])["status"] == "complete"


def ai_assessment(suite):
    ids = list(suite["judgments"]["q1"])
    return {
        "assessment_type": "single_model_ai_preliminary",
        "assessor": "Synthetic test AI",
        "assessed_at": "2026-11-04",
        "evidence_scope": "Synthetic metadata",
        "limitations": "One AI assessor, not human release evidence",
        "pool_sha256": evaluation.pool_fingerprint(suite),
        "grades": {"q1": {ids[0]: 2, ids[1]: 0}},
    }


def test_ai_scoring_uses_single_grades_without_creating_human_pairs(study):
    suite, run, _ = study
    original = deepcopy(suite)
    report = evaluation.score_ai(suite, [run], ai_assessment(suite))
    assert report["status"] == "ai_preliminary"
    assert report["release_gate_satisfied"] is False
    assert report["agreement"]["cohen_kappa"] is None
    assert report["agreement"]["paired"] == 0
    assert report["systems"]["example"]["overall"]["ndcg_at_10"] == 1
    assert suite == original
    assert evaluation.score(suite, [run])["status"] == "pending_judgments"


@pytest.mark.parametrize("mutation", ["evidence", "missing", "extra", "grade", "bool", "assessor"])
def test_ai_scoring_rejects_stale_incomplete_or_invalid_assessments(study, mutation):
    suite, run, _ = study
    assessment = ai_assessment(suite)
    ids = list(assessment["grades"]["q1"])
    if mutation == "evidence":
        suite["documents"][ids[0]]["description"] = "Different source evidence"
    elif mutation == "missing":
        assessment["grades"]["q1"].pop(ids[0])
    elif mutation == "extra":
        assessment["grades"]["q1"]["flow:DF_EXTRA+V1.0"] = 0
    elif mutation == "assessor":
        assessment.pop("assessor")
    else:
        assessment["grades"]["q1"][ids[0]] = True if mutation == "bool" else 3
    with pytest.raises(ValueError):
        evaluation.score_ai(suite, [run], assessment)


def test_ai_cli_cannot_satisfy_strict_human_release_gate(study, tmp_path):
    suite, _, paths = study
    assessment = tmp_path / "ai.json"
    evaluation.write_json(assessment, ai_assessment(suite))
    argv = [
        "score-ai",
        "--suite",
        str(paths["suite"]),
        "--runs",
        str(paths["run"]),
        "--assessment",
        str(assessment),
        "--output",
        str(paths["output"]),
    ]
    assert evaluation.main(argv) == 0
    assert evaluation.read_json(paths["output"])["status"] == "ai_preliminary"
    with pytest.raises(SystemExit):
        evaluation.main([*argv, "--require-complete"])
    assert (
        evaluation.main(
            [
                "score",
                "--suite",
                str(paths["suite"]),
                "--runs",
                str(paths["run"]),
                "--output",
                str(paths["output"]),
                "--require-complete",
            ]
        )
        == 1
    )


def test_cli_cannot_overwrite_imported_human_evidence(study, tmp_path):
    suite, _, paths = study
    a, b = [tmp_path / f"{judge}.csv" for judge in ("a", "b")]
    evaluation.write_judge_csv(a, suite, "a")
    evaluation.write_judge_csv(b, suite, "b")
    original = a.read_bytes()
    with pytest.raises(SystemExit):
        evaluation.main(
            [
                "merge",
                "--suite",
                str(paths["suite"]),
                "--judge-a",
                str(a),
                "--judge-b",
                str(b),
                "--output",
                str(a),
            ]
        )
    assert a.read_bytes() == original


def test_committed_ai_pilot_reproduces_with_unchanged_source_runs():
    root = Path(__file__).parents[1]
    assessment = evaluation.read_json(
        root / "docs/verification/search-ai-assessment-2026-11-04.json"
    )
    runs = []
    for source in assessment["runs"]:
        path = root / source["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"]
        runs.append(evaluation.read_json(path))
    suite = evaluation.read_json(root / "tests/fixtures/search_eval.json")
    report = evaluation.score_ai(suite, runs, assessment)
    assert report == evaluation.read_json(
        root / "docs/verification/search-ai-scores-2026-11-04.json"
    )
    assert evaluation.score(suite, runs)["completed"] == 0


def semantic_hash(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


@pytest.fixture
def source_review(study):
    suite, _, _ = study
    assessment = ai_assessment(suite)
    ids = list(suite["documents"])
    evidence = {
        "status": "partial",
        "searches": {"q1": 1},
        "pool_file_sha256": hashlib.sha256(
            (json.dumps(suite, ensure_ascii=False, indent=2) + "\n").encode()
        ).hexdigest(),
        "calls": [
            {
                "tool": "get_tuik_resource",
                "arguments": {"resource_id": ids[0]},
                "outcome": "ok",
                "response": {
                    "id": ids[0],
                    "title": "Synthetic source",
                    "source_url": "https://www.tuik.gov.tr/",
                },
            },
            {
                "tool": "search_tuik",
                "arguments": {"query": "işsizlik", "limit": 10, "live": True},
                "outcome": "ok",
                "response": {"backend": "portal"},
            },
        ],
    }
    review = deepcopy(assessment)
    review.update(
        review_type="single_ai_sequential_source_review",
        human_assessors=0,
        independent_assessors=False,
        first_assessment_sha256=semantic_hash(assessment),
        evidence_sha256=semantic_hash(evidence),
        source_checks={
            ids[0]: {"method": "live_metadata", "call": 0, "reason": "Inspected exact source"},
            ids[1]: {"method": "archived_metadata", "reason": "Only historical source available"},
        },
        query_notes={"q1": "Second pass with more source evidence"},
        changes=[
            {
                "query_id": "q1",
                "document_id": ids[1],
                "before": 0,
                "after": 1,
                "reason": "Synthetic contextual information",
            }
        ],
    )
    review["grades"]["q1"][ids[1]] = 1
    return assessment, review, evidence


def test_reviewed_ai_scores_preserve_one_assessor_and_original_pass(study, source_review):
    suite, run, _ = study
    assessment, review, evidence = source_review
    original = deepcopy(assessment)
    report = evaluation.score_ai(suite, [run], assessment, review, evidence)
    assert report["status"] == "ai_reviewed" and report["review_complete"] is True
    assert report["revised_pairs"] == 1
    assert report["source_checks"] == {"live_metadata": 1, "archived_metadata": 1}
    assert report["agreement"]["paired"] == 0 and report["agreement"]["cohen_kappa"] is None
    assert report["release_gate_satisfied"] is False
    assert assessment == original
    assert evaluation.score(suite, [run])["completed"] == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "human",
        "independence",
        "original",
        "running",
        "pool",
        "evidence",
        "source_id",
        "source_tool",
        "source_index",
        "missing_source",
        "missing_note",
        "missing_change",
        "old_grade",
        "missing_replay",
        "wrong_replay",
    ],
)
def test_review_rejects_false_identity_stale_sources_and_undocumented_changes(
    study, source_review, mutation
):
    suite, run, _ = study
    assessment, review, evidence = source_review
    doc_id = next(iter(suite["documents"]))
    if mutation == "human":
        review["human_assessors"] = 2
    elif mutation == "independence":
        review["independent_assessors"] = True
    elif mutation == "original":
        review["first_assessment_sha256"] = "changed"
    elif mutation == "running":
        evidence["status"] = "running"
    elif mutation == "pool":
        evidence["pool_file_sha256"] = "changed"
    elif mutation == "evidence":
        review["evidence_sha256"] = "changed"
    elif mutation == "source_id":
        evidence["calls"][0]["response"]["id"] = "flow:DF_OTHER+V1.0"
    elif mutation == "source_tool":
        evidence["calls"][0]["tool"] = "search_tuik"
    elif mutation == "source_index":
        review["source_checks"][doc_id]["call"] = True
    elif mutation == "missing_source":
        review["source_checks"].pop(doc_id)
    elif mutation == "missing_note":
        review["query_notes"].clear()
    elif mutation == "missing_change":
        review["changes"].clear()
    elif mutation == "old_grade":
        review["changes"][0]["before"] = 2
    elif mutation == "missing_replay":
        evidence["searches"].clear()
    elif mutation == "wrong_replay":
        evidence["calls"][1]["arguments"]["query"] = "different query"
    if mutation != "evidence":
        review["evidence_sha256"] = semantic_hash(evidence)
    with pytest.raises(ValueError):
        evaluation.score_ai(suite, [run], assessment, review, evidence)


def test_strict_ai_review_cli_requires_a_valid_second_pass(study, source_review, tmp_path):
    _, _, paths = study
    assessment, review, evidence = source_review
    for name, value in (("assessment", assessment), ("review", review), ("evidence", evidence)):
        evaluation.write_json(tmp_path / f"{name}.json", value)
    argv = [
        "score-ai",
        "--suite",
        str(paths["suite"]),
        "--runs",
        str(paths["run"]),
        "--assessment",
        str(tmp_path / "assessment.json"),
        "--output",
        str(paths["output"]),
        "--require-reviewed",
    ]
    assert evaluation.main(argv) == 1
    assert (
        evaluation.main(
            [
                *argv,
                "--review",
                str(tmp_path / "review.json"),
                "--evidence",
                str(tmp_path / "evidence.json"),
            ]
        )
        == 0
    )
    assert evaluation.read_json(paths["output"])["status"] == "ai_reviewed"


def test_committed_source_review_reproduces_without_human_labels_or_network():
    root = Path(__file__).parents[1]
    base = root / "docs/verification"
    assessment = evaluation.read_json(base / "search-ai-assessment-2026-11-04.json")
    review = evaluation.read_json(base / "search-ai-reviewed-assessment-2026-11-04.json")
    evidence = evaluation.read_json(base / "search-ai-live-evidence-2026-11-04.json.gz")
    assert (
        evidence["collector_sha256"]
        == hashlib.sha256((root / "scripts/collect_ai_review.py").read_bytes()).hexdigest()
    )
    runs = [evaluation.read_json(root / source["path"]) for source in assessment["runs"]]
    suite = evaluation.read_json(root / "tests/fixtures/search_eval.json")
    report = evaluation.score_ai(suite, runs, assessment, review, evidence)
    assert report == evaluation.read_json(base / "search-ai-reviewed-scores-2026-11-04.json")
    assert set(evidence["searches"]) == {query["id"] for query in suite["queries"]}
    assert all(
        evidence["calls"][index]["response"]["backend"] == "portal"
        for index in evidence["searches"].values()
    )
    auxiliary = review["supplemental_scope_checks"]
    path = root / auxiliary["path"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == auxiliary["sha256"]
    checks = evaluation.read_json(path)
    assert (
        hashlib.sha256(checks["collector_source"].encode()).hexdigest()
        == checks["collector_sha256"]
    )
    assert checks["status"] == "ok"
    for check in checks["checks"].values():
        assert check["result"]["total_rows"] > 0
        assert check["result"]["snapshot_id"] == check["profile"]["snapshot_id"]
    assert evaluation.score(suite, runs)["completed"] == 0
