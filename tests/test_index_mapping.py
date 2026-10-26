"""Opt-in real cluster check; each test owns and deletes its own UUID index."""

import os
import uuid

import httpx
import pytest

from tuik_mcp.search.documents import IndexDocument
from tuik_mcp.search.schema import index_definition, physical_index_name


@pytest.mark.opensearch
@pytest.mark.skipif(not os.getenv("TUIK_OPENSEARCH_URL"), reason="Set TUIK_OPENSEARCH_URL")
def test_mapping_accepts_all_document_kinds_and_rejects_schema_drift():
    name = physical_index_name("tuik-schema-test-" + uuid.uuid4().hex, 1)
    with httpx.Client(
        base_url=os.environ["TUIK_OPENSEARCH_URL"].rstrip("/") + "/", timeout=30, trust_env=False
    ) as client:
        created = client.put(name, json=index_definition())
        created.raise_for_status()
        try:
            for doc_id, kind, parent in [
                ("flow:DF_TEST+V1.0", "dataset", None),
                ("press:1", "bulletin", None),
                ("press:1:metadata", "metadata", "press:1"),
            ]:
                doc = IndexDocument(
                    id=doc_id,
                    kind=kind,
                    title="Tüketici Fiyat Endeksi",
                    theme=["Fiyat İstatistikleri"],
                    parent_id=parent,
                    source_url="https://veriportali.tuik.gov.tr/tr/press/1",
                    publication_date="2026-10-01",
                )
                client.put(
                    f"{name}/_doc/{doc_id}", json=doc.model_dump(mode="json")
                ).raise_for_status()
            client.post(f"{name}/_refresh").raise_for_status()
            response = client.post(
                f"{name}/_search",
                json={
                    "query": {
                        "bool": {
                            "must": [{"match": {"title": "TÜFE"}}],
                            "filter": [
                                {"term": {"theme": "Fiyat İstatistikleri"}},
                                {"range": {"publication_date": {"gte": "2026-01-01"}}},
                            ],
                        }
                    }
                },
            )
            response.raise_for_status()
            assert response.json()["hits"]["total"]["value"] == 3
            assert client.put(f"{name}/_doc/bad", json={"unmapped": 1}).status_code == 400
        finally:
            client.delete(name).raise_for_status()
