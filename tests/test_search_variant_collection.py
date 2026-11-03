"""Frozen-corpus ablations, error propagation and disposable-index lifecycle (#20)."""

import importlib.util
import json
import os
from pathlib import Path

import httpx
import pytest

from tuik_mcp.search.documents import IndexDocument
from tuik_mcp.search.indexer import OpenSearchStore

SPEC = importlib.util.spec_from_file_location(
    "evaluate_variants", Path(__file__).parents[1] / "scripts/evaluate_search.py"
)
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


@pytest.fixture
def suite():
    return {
        "version": 1,
        "queries": [{"id": "q1", "text": "TÜFE", "category": "abbreviation"}],
        "judgments": {},
    }


@pytest.fixture
def documents():
    return [
        IndexDocument(
            id="flow:DF_CPI+V1.0",
            kind="dataset",
            title="Tüketici Fiyat Endeksi",
            source_url="https://veriportali.tuik.gov.tr/",
            retrieved_at="2026-10-26T10:00:00Z",
            indexed_at="2026-10-26T10:00:00Z",
        )
    ]


@pytest.mark.parametrize("failure", [None, "bulk", "count", "timeout", "shards", "cleanup"])
async def test_collection_is_isolated_and_rejects_incomplete_runs(
    suite, documents, tmp_path, failure
):
    calls, indices = [], []

    def handler(request):
        path = request.url.path
        calls.append((request.method, path))
        if path == "/":
            return httpx.Response(200, json={"version": {"number": "3.3.2"}})
        if path == "/_bulk":
            lines = [json.loads(line) for line in request.content.splitlines()]
            assert lines[1]["id"] == documents[0].id
            assert lines[0]["index"]["_index"] in indices
            return httpx.Response(
                200,
                json={"items": [{"index": {"status": 400 if failure == "bulk" else 201}}]},
            )
        index = path.split("/")[1]
        assert index.startswith("tuik-evaluation-")
        if request.method == "PUT":
            indices.append(index)
        if path.endswith("/_count"):
            return httpx.Response(200, json={"count": 0 if failure == "count" else 1})
        if path.endswith("/_search"):
            assert request.url.params["allow_partial_search_results"] == "false"
            assert json.loads(request.content)["sort"][-1] == {"id": "asc"}
            return httpx.Response(
                200,
                json={
                    "timed_out": failure == "timeout",
                    "_shards": {"failed": int(failure == "shards")},
                    "hits": {
                        "total": {"value": 1, "relation": "eq"},
                        "hits": [{"_score": 2.5, "_source": documents[0].model_dump(mode="json")}],
                    },
                },
            )
        if request.method == "DELETE" and failure == "cleanup":
            return httpx.Response(500, json={"error": "cleanup failed"})
        return httpx.Response(200, json={"acknowledged": True})

    store = OpenSearchStore("http://cluster", transport=httpx.MockTransport(handler))
    output = tmp_path / "run.json"
    try:
        assert await evaluation.collect_index(suite, documents, output, "unused", store=store) == (
            1 if failure else 0
        )
    finally:
        await store.close()
    report = evaluation.read_json(output)
    assert indices and calls[-1] == ("DELETE", "/" + indices[-1])
    assert not any("alias" in path for _, path in calls)
    if failure:
        assert report["status"] == "error" and report["errors"]
        with pytest.raises(ValueError, match="Incomplete run"):
            evaluation.pool(suite, [report])
    else:
        assert set(report["systems"]) == set(evaluation.INDEX_VARIANTS)
        assert len(report["cleanup"]) == 3 and set(report["cleanup"].values()) == {"deleted"}
        for system in report["systems"].values():
            assert system["corpus_ids"] == [documents[0].id]
            assert system["scores"]["q1"] == [2.5]
        pooled = evaluation.pool(suite, [report])
        sheet = evaluation.judge_sheet(pooled, "a")
        assert "score" not in sheet["documents"][documents[0].id]
        assert evaluation.score(pooled, [report])["status"] == "pending_judgments"


async def test_create_failure_never_deletes_an_index_it_does_not_own(suite, documents, tmp_path):
    calls = []

    def handler(request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"version": {"number": "3.3.2"}})
        return httpx.Response(400, json={"error": "resource_already_exists_exception"})

    store = OpenSearchStore("http://cluster", transport=httpx.MockTransport(handler))
    try:
        assert (
            await evaluation.collect_index(
                suite, documents, tmp_path / "run.json", "unused", store=store
            )
            == 1
        )
        assert calls == ["GET", "PUT"]
    finally:
        await store.close()


def test_variant_ablation_keeps_synonyms_and_fuzziness_out_of_the_wrong_comparison():
    definitions = {v: evaluation.variant_definition(v) for v in evaluation.INDEX_VARIANTS}
    for name in ("title", "description", "body"):
        fields = {v: d["mappings"]["properties"][name] for v, d in definitions.items()}
        assert fields["builtin_turkish"]["analyzer"] == "turkish"
        assert fields["multi_field"]["search_analyzer"] == "tuik_tr_index"
        assert fields["multi_field_synonyms"]["search_analyzer"] == "tuik_tr_search"
    assert (
        "ascii" not in definitions["builtin_turkish"]["mappings"]["properties"]["title"]["fields"]
    )
    # The production variant is byte-for-byte the packaged definition.
    assert definitions["multi_field_synonyms"] == evaluation.index_definition()
    for variant in evaluation.INDEX_VARIANTS:
        body = evaluation.variant_query(variant, "işsizliğin")
        assert body["size"] == 10
        assert body["query"]["bool"]["should"][1]["multi_match"]["fuzziness"] == "AUTO"


def test_jsonl_validation_and_output_collision_fail_before_cluster_writes(
    suite, documents, tmp_path
):
    source = tmp_path / "documents.jsonl"
    source.write_text(documents[0].model_dump_json() + "\n")
    assert evaluation.load_documents(source) == documents
    suite_file = tmp_path / "suite.json"
    evaluation.write_json(suite_file, suite)
    with pytest.raises(SystemExit):
        evaluation.main(
            [
                "collect-index",
                "--suite",
                str(suite_file),
                "--documents",
                str(source),
                "--output",
                str(source),
                "--url",
                "http://unused",
            ]
        )
    assert evaluation.load_documents(source) == documents


@pytest.mark.opensearch
@pytest.mark.skipif(not os.getenv("TUIK_OPENSEARCH_URL"), reason="Set TUIK_OPENSEARCH_URL")
async def test_real_cluster_abbreviation_ablation_and_pooling(suite, documents, tmp_path):
    output = tmp_path / "variants.json"
    assert (
        await evaluation.collect_index(suite, documents, output, os.environ["TUIK_OPENSEARCH_URL"])
        == 0
    )
    report = evaluation.read_json(output)
    assert report["systems"]["builtin_turkish"]["results"]["q1"] == []
    assert report["systems"]["multi_field"]["results"]["q1"] == []
    assert report["systems"]["multi_field_synonyms"]["results"]["q1"][0]["id"] == documents[0].id
    assert evaluation.score(suite, [report])["status"] == "pending_judgments"
    assert set(report["cleanup"].values()) == {"deleted"}
