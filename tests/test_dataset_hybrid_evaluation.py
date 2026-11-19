"""Dataset-filtered hybrid evaluation collector (#80)."""

import importlib.util
import os
from pathlib import Path
from statistics import fmean

import pytest

from tuik_mcp.search.documents import IndexDocument
from tuik_mcp.search.embeddings import DIMENSION, MODEL
from tuik_mcp.search.indexer import IndexerError, OpenSearchStore

spec = importlib.util.spec_from_file_location(
    "evaluate_search", Path(__file__).parents[1] / "scripts" / "evaluate_search.py"
)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)
URL = os.getenv("TUIK_OPENSEARCH_URL")


class VectorEmbedder:
    model = MODEL
    dimension = DIMENSION

    @staticmethod
    def vector(text):
        seed = sum(map(ord, text)) % 97 + 1
        return [((seed * (i + 1)) % 13) / 13 + 0.01 for i in range(DIMENSION)]

    def documents(self, texts):
        return [self.vector(text) for text in texts]

    def query(self, text):
        return self.vector(text)


def documents():
    flows = [
        ("DF_ISSIZ", "İşsizlik Oranı"),
        ("DF_TUFE", "Tüketici Fiyat Endeksi (TÜFE)"),
        ("DF_NUFUS", "Nüfus ve İşgücü Göstergeleri"),
    ]
    return [
        *(
            IndexDocument(
                id=f"flow:{flow}+V1.0",
                kind="dataset",
                title=title,
                source_url="https://databrowser2.tuik.gov.tr/",
            )
            for flow, title in flows
        ),
        IndexDocument(
            id="press:57987",
            kind="bulletin",
            title="İşgücü İstatistikleri, Ağustos 2026",
            body="İşsizlik oranı açıklandı.",
            source_url="https://veriportali.tuik.gov.tr/tr/press/57987",
        ),
    ]


@pytest.mark.opensearch
@pytest.mark.skipif(not URL, reason="Set TUIK_OPENSEARCH_URL")
async def test_dataset_collector_filters_every_system_in_one_index(tmp_path):
    suite = {
        "version": 1,
        "queries": [
            {"id": "q1", "text": "işsizlik oranı", "category": "exact"},
            {"id": "q2", "text": "TÜFE", "category": "abbreviation"},
        ],
        "judgments": {},
    }
    output = tmp_path / "datasets.json"
    store = OpenSearchStore(URL)
    try:
        code = await evaluation.collect_datasets(
            suite, documents(), output, URL, embedder=VectorEmbedder(), store=store
        )
        report = evaluation.read_json(output)
        assert code == 0, report["errors"]
        index = report["systems"]["dataset_lexical"]["index"]
        assert report["cleanup"] == {index: "deleted"} and not await store.exists(index)
    finally:
        await store.close()
    systems = report["systems"]
    assert set(systems) == {
        "dataset_lexical",
        "dataset_hybrid_rrf_lexical_weighted",
        "dataset_hybrid_rrf_equal",
        "dataset_hybrid_routed",
    }
    assert {s["index"] for s in systems.values()} == {index}
    for system in systems.values():
        for hits in system["results"].values():
            assert all(hit["resource_type"] == "dataset" for hit in hits)
    assert systems["dataset_lexical"]["routing"]["q1"]["ranking"] == "lexical"
    assert systems["dataset_hybrid_rrf_equal"]["routing"]["q1"]["ranking"] == "hybrid"
    assert systems["dataset_lexical"]["results"]["q1"][0]["id"] == "flow:DF_ISSIZ+V1.0"
    routed = systems["dataset_hybrid_routed"]["routing"]
    assert routed["q1"]["source_system"] == "dataset_hybrid_rrf_equal"
    assert routed["q2"]["source_system"] == "dataset_lexical"
    assert report["settings"]["filters"]["kinds"] == ["dataset"]
    datasets = sorted(d.id for d in documents() if d.kind == "dataset")
    assert all(system["corpus_ids"] == datasets for system in systems.values())


class FailingDeleteStore(OpenSearchStore):
    """A real store whose disposable-index DELETE fails, as on a timeout."""

    def __init__(self, url):
        super().__init__(url)
        self.left = []

    async def delete(self, name):
        self.left.append(name)
        raise IndexerError("Injected DELETE failure: index remains on cluster")


@pytest.mark.opensearch
@pytest.mark.skipif(not URL, reason="Set TUIK_OPENSEARCH_URL")
async def test_dataset_collector_fails_when_the_disposable_index_is_not_deleted(tmp_path):
    suite = {
        "version": 1,
        "queries": [{"id": "q1", "text": "işsizlik oranı", "category": "exact"}],
        "judgments": {},
    }
    output = tmp_path / "datasets.json"
    store = FailingDeleteStore(URL)
    try:
        code = await evaluation.collect_datasets(
            suite, documents(), output, URL, embedder=VectorEmbedder(), store=store
        )
        report = evaluation.read_json(output)
        (index,) = store.left
        assert await store.exists(index)
    finally:
        for name in store.left:
            await OpenSearchStore.delete(store, name)
        await store.close()
    assert code == 1 and report["status"] == "error"
    assert "Injected DELETE failure" in report["cleanup"][index]
    assert index in report["errors"]["cleanup"]["message"]
    # Rankings stay for diagnosis, but the report cannot be scored as a complete run.
    assert report["systems"]["dataset_lexical"]["results"]["q1"]
    with pytest.raises(ValueError, match="Incomplete run"):
        evaluation.validate_run(report, suite)


def filtered_case(settings):
    """One dataset-filtered system; the pool also holds a highly graded bulletin."""
    pooled = {
        "queries": [{"id": "q1", "text": "işsizlik", "category": "exact"}],
        "documents": {
            "flow:DF_A+V1.0": {"resource_type": "dataset"},
            "flow:DF_B+V1.0": {"resource_type": "dataset"},
            "press:1": {"resource_type": "bulletin"},
        },
    }
    grades = {"q1": {"flow:DF_A+V1.0": 1, "flow:DF_B+V1.0": 2, "press:1": 2}}
    hits = [{"id": "flow:DF_B+V1.0", "resource_type": "dataset"}]
    system = {
        "corpus": "frozen",
        "corpus_ids": sorted(pooled["documents"]),
        "results": {"q1": hits},
    }
    run = {"systems": {"datasets": system}, **settings}
    return evaluation.system_metrics(pooled, [run], grades)["datasets"]


def test_kind_filtered_run_scores_against_an_ideal_of_that_kind_only():
    filtered = filtered_case({"settings": {"filters": {"kinds": ["dataset"]}}})
    assert filtered["overall"] == filtered["by_resource_type"]["dataset"]
    assert filtered["by_category"]["exact"] == filtered["overall"]
    assert filtered["overall"]["recall_at_10"] == 0.5  # Not 1/3: the bulletin is ineligible.
    unfiltered = filtered_case({})
    assert unfiltered["overall"]["ndcg_at_10"] < filtered["overall"]["ndcg_at_10"]


def test_archived_dataset_categories_use_the_dataset_only_ideal():
    """The #80 report's category figures must average to its dataset overall."""
    root = Path(__file__).parents[1]
    base = root / "docs/verification"
    assessment = evaluation.read_json(base / "search-dataset-hybrid-ai-assessment-2026-11-19.json")
    review = evaluation.read_json(
        base / "search-dataset-hybrid-ai-reviewed-assessment-2026-11-19.json"
    )
    evidence = evaluation.read_json(
        base / "search-dataset-hybrid-ai-live-evidence-2026-11-19.json.gz"
    )
    runs = [evaluation.read_json(root / source["path"]) for source in assessment["runs"]]
    suite = evaluation.read_json(base / "search-hybrid-pool-2026-11-05.json.gz")
    report = evaluation.score_ai(suite, runs, assessment, review, evidence)
    assert report == evaluation.read_json(
        base / "search-dataset-hybrid-ai-reviewed-scores-2026-11-19.json"
    )
    dataset_systems = set(runs[-1]["systems"])
    assert dataset_systems == {*evaluation.DATASET_SYSTEMS, "dataset_hybrid_routed"}
    for name in dataset_systems:
        system = report["systems"][name]
        overall = system["overall"]
        assert overall == system["by_resource_type"]["dataset"]
        categories = system["by_category"].values()
        assert {category["queries"] for category in categories} == {6}
        assert fmean(c["ndcg_at_10"] for c in categories) == pytest.approx(overall["ndcg_at_10"])
    lexical, served = (report["systems"][n] for n in ("dataset_lexical", "dataset_hybrid_routed"))
    assert lexical["by_category"]["exact"]["ndcg_at_10"] == pytest.approx(0.739937, abs=1e-6)
    assert served["by_category"]["exact"]["ndcg_at_10"] == pytest.approx(0.846676, abs=1e-6)
    assert served["overall"]["ndcg_at_10"] == pytest.approx(0.689815, abs=1e-6)
