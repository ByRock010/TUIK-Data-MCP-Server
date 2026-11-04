"""Human CSV round trips and explicit separation of single-AI pilot evidence."""

import csv
import hashlib
import importlib.util
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
