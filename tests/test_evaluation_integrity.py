"""Regressions for malformed pools and resumed baseline provenance from PR #49."""

import hashlib
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "evaluation_integrity", Path(__file__).parents[1] / "scripts/evaluate_search.py"
)
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


def hit(number):
    return {
        "id": f"flow:DF_TEST_{number}+V1.0",
        "title": f"Test {number}",
        "source_url": "https://veriportali.tuik.gov.tr/",
        "resource_type": "dataset",
    }


@pytest.fixture
def suite():
    return {
        "version": 1,
        "queries": [{"id": "q1", "text": "işsizlik", "category": "exact"}],
        "judgments": {"q1": {hit(2)["id"]: {"judge_a": 2, "judge_b": 2}}},
    }


def run():
    return {
        "queries": {"q1": "işsizlik"},
        "systems": {"portal": {"corpus": "portal", "results": {"q1": [hit(1)]}}},
    }


@pytest.mark.parametrize("operation", [evaluation.pool, evaluation.score])
def test_orphan_judgments_raise_actionable_validation_error(suite, operation):
    with pytest.raises(ValueError, match="Missing document metadata.*flow:DF_TEST_2"):
        operation(suite, [run()])


def test_new_run_can_supply_previously_missing_document_metadata(suite):
    collected = run()
    collected["systems"]["portal"]["results"]["q1"].append(hit(2))
    result = evaluation.score(suite, [collected])
    assert result["judgments"] == 2 and result["completed"] == 1
    assert result["systems"]["portal"]["overall"]["recall_at_10"] == 1


@pytest.mark.parametrize("field", ["id", "title", "resource_type", "source_url"])
def test_invalid_retained_document_metadata_is_rejected(suite, field):
    suite["documents"] = {hit(2)["id"]: hit(2)}
    suite["documents"][hit(2)["id"]].pop(field)
    with pytest.raises(ValueError, match="Invalid document metadata"):
        evaluation.score(suite, [run()])


@pytest.mark.parametrize("field", ["revision", "collector_sha256"])
async def test_resume_rejects_changed_code_before_network_or_checkpoint_write(
    suite, monkeypatch, tmp_path, field
):
    output = tmp_path / "run.json"
    previous = {
        **run(),
        "revision": "recorded-revision" if field != "revision" else "different-revision",
        "collector_sha256": hashlib.sha256(Path(evaluation.__file__).read_bytes()).hexdigest()
        if field != "collector_sha256"
        else "different-collector",
    }
    evaluation.write_json(output, previous)
    original = output.read_bytes()
    monkeypatch.setattr(evaluation.subprocess, "check_output", lambda *a, **kw: "recorded-revision")

    def unexpected_http(settings):
        raise AssertionError("Mismatched provenance contacted upstream")

    monkeypatch.setattr(evaluation, "HttpClient", unexpected_http)
    with pytest.raises(ValueError, match="Cannot resume.*" + field):
        await evaluation.collect(suite, output, resume=True)
    assert output.read_bytes() == original


@pytest.mark.parametrize("field", ["revision", "collector_sha256"])
async def test_resume_rejects_missing_provenance(suite, monkeypatch, tmp_path, field):
    output = tmp_path / "run.json"
    previous = {
        **run(),
        "revision": "revision",
        "collector_sha256": hashlib.sha256(Path(evaluation.__file__).read_bytes()).hexdigest(),
    }
    previous.pop(field)
    evaluation.write_json(output, previous)
    original = output.read_bytes()
    monkeypatch.setattr(evaluation.subprocess, "check_output", lambda *a, **kw: "revision")
    with pytest.raises(ValueError, match="Cannot resume.*" + field):
        await evaluation.collect(suite, output, resume=True)
    assert output.read_bytes() == original
