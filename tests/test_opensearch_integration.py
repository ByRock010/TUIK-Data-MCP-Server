"""Opt-in real-cluster contracts; synthetic metadata and recorded TÜİK adapters only."""

import os
import uuid
from datetime import UTC, date, datetime

import pytest

from tuik_mcp.search.backend import OpenSearchBackend, SearchFilters
from tuik_mcp.search.documents import IndexDocument
from tuik_mcp.search.indexer import OpenSearchStore, prepare, rebuild

URL = os.getenv("TUIK_OPENSEARCH_URL")
pytestmark = [
    pytest.mark.opensearch,
    pytest.mark.skipif(not URL, reason="Set TUIK_OPENSEARCH_URL"),
]
THEME = "İstihdam, İşsizlik ve Ücret"
STAMP = datetime(2026, 11, 2, 9, tzinfo=UTC)


@pytest.fixture
async def indexed():
    alias = "tuik-integration-test-" + uuid.uuid4().hex
    store = OpenSearchStore(URL)
    documents = [
        IndexDocument(
            id="flow:DF_ISSIZ+V1.0",
            kind="dataset",
            title="İşsizlik Oranı",
            source_url="https://databrowser2.tuik.gov.tr/",
            theme=[THEME],
            downloadable=True,
            formats=["csv"],
        ),
        IndexDocument(
            id="flow:DF_KAPALI+V1.0",
            kind="dataset",
            title="İşsizlik Kayıtları",
            source_url="https://databrowser2.tuik.gov.tr/",
            theme=[THEME],
        ),
        IndexDocument(
            id="press:57987",
            kind="bulletin",
            title="İşgücü İstatistikleri Ağustos 2026",
            body="İşsizlik oranı yüzde 8,5 oldu.",
            theme=[THEME],
            downloadable=True,
            publication_date="2026-09-30T23:59:59Z",
            formats=["xls"],
            source_url="https://veriportali.tuik.gov.tr/tr/press/57987",
        ),
        IndexDocument(
            id="press:57987:metadata",
            kind="metadata",
            parent_id="press:57987",
            title="İşgücü İstatistikleri Metaveri",
            body="İşsizliğin tanımı ve kapsamı.",
            theme=[THEME],
            source_url="https://veriportali.tuik.gov.tr/tr/press/57987",
        ),
        IndexDocument(
            id="press:900000001",
            kind="bulletin",
            title="İstanbul Nüfusu 2026",
            body="İstanbul’un nüfusu ve ilçeler.",
            theme=["Nüfus ve Demografi"],
            publication_date="2026-02-01",
            source_url="https://veriportali.tuik.gov.tr/tr/press/900000001",
        ),
        IndexDocument(
            id="press:900000002",
            kind="bulletin",
            title="Tüketici Fiyat Endeksi",
            theme=["Enflasyon ve Fiyat"],
            publication_date="2026-10-03",
            downloadable=True,
            source_url="https://veriportali.tuik.gov.tr/tr/press/900000002",
        ),
        IndexDocument(
            id="press:900000003",
            kind="bulletin",
            title="Yurt İçi Üretici Fiyat Endeksi",
            theme=["Enflasyon ve Fiyat"],
            publication_date="2026-10-03",
            downloadable=True,
            source_url="https://veriportali.tuik.gov.tr/tr/press/900000003",
        ),
    ]
    try:
        payloads, _ = prepare(documents, STAMP)
        await rebuild(store, alias, payloads)
        yield OpenSearchBackend(store, alias)
    finally:
        try:
            # Exact UUID namespace only; never touch the configured application alias.
            for name in await store.versions(alias):
                await store.delete(name)
        finally:
            await store.close()


@pytest.mark.parametrize("query", ["issizlik", "İŞSİZLİK", "işsizliğin", "issizlk"])
async def test_ascii_case_inflection_and_typo(indexed, query):
    hits = await indexed.search(query, SearchFilters(kinds=["dataset"], downloadable=True), 10)
    assert hits.backend == "opensearch" and [h.id for h in hits.hits] == ["flow:DF_ISSIZ+V1.0"]
    assert hits.total == 1 and hits.hits[0].score > 0
    assert datetime.fromisoformat(hits.hits[0].indexed_at.replace("Z", "+00:00")) == STAMP


@pytest.mark.parametrize("query", ["İstanbul'un", "İstanbul’un", "istanbul", "nufus"])
async def test_straight_and_curly_apostrophes_and_ascii(indexed, query):
    hits = await indexed.search(query, SearchFilters(kinds=["bulletin"]), 10)
    assert [h.id for h in hits.hits] == ["press:900000001"]
    assert any(
        "<em>" in fragment
        for fragments in hits.hits[0].highlights.values()
        for fragment in fragments
    )


@pytest.mark.parametrize("query", ["TÜFE", "TUFE", "tufe"])
async def test_abbreviation_synonyms_do_not_cross_to_producer_prices(indexed, query):
    hits = await indexed.search(query, SearchFilters(), 10)
    assert [h.id for h in hits.hits] == ["press:900000002"]


async def test_combined_filters_metadata_and_inclusive_date_bounds(indexed):
    filters = SearchFilters(
        kinds=["bulletin"],
        themes=[THEME],
        downloadable=True,
        published_after=date(2026, 9, 30),
        published_before=date(2026, 9, 30),
    )
    hits = await indexed.search("işsizlik", filters, 1)
    assert hits.total == 1 and [h.id for h in hits.hits] == ["press:57987"]
    assert hits.hits[0].formats == ["xls"] and hits.hits[0].source_url.endswith("/57987")
    metadata = await indexed.search("işsizliğin", SearchFilters(kinds=["metadata"]), 10)
    assert [h.id for h in metadata.hits] == ["press:57987:metadata"]
    closed = await indexed.search(
        "işsizlik", SearchFilters(kinds=["dataset"], downloadable=False), 10
    )
    assert [h.id for h in closed.hits] == ["flow:DF_KAPALI+V1.0"]
    # Exact keyword theme mismatches and impossible publication windows return no hits.
    for filters in [
        SearchFilters(themes=["İstihdam"]),
        SearchFilters(published_before=date(2025, 12, 31)),
        SearchFilters(kinds=["dataset"], published_after=date(2026, 1, 1)),
    ]:
        empty = await indexed.search("işsizlik", filters, 10)
        assert empty.total == 0 and empty.hits == []


@pytest.mark.parametrize("configured", [False, True])
async def test_unset_or_real_missing_alias_uses_both_fallback_paths(catalog, configured):
    service, calls = catalog
    if configured:
        # Reach the actual cluster: a missing alias must not look like zero matches.
        service.index = OpenSearchBackend(
            OpenSearchStore(URL), "tuik-absent-test-" + uuid.uuid4().hex
        )
    try:
        datasets = await service.search_datasets("Altın Hariç Dış Ticaret Hadleri")
        assert datasets.backend == "literal" and datasets.results
        assert "literal catalog matching" in datasets.warnings[0]
        general = await service.search_index(
            "işsizlik", SearchFilters(kinds=["bulletin"], themes=[THEME]), 5
        )
        assert general.backend == "portal" and general.hits
        assert all(h.kind == "bulletin" for h in general.hits)
        assert general.total is None
        assert "live portal search" in general.warnings[0]
        assert "cannot apply these filters: theme" in general.warnings[1]
        reason = "unavailable" if configured else "not configured"
        assert reason in general.warnings[0]
        assert any(c.url.path.endswith("/search") for c in calls)
    finally:
        await service.close()
