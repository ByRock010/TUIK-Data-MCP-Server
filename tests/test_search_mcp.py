"""Exercise search discovery, validation and response data over real stdio MCP."""

import os
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tuik_mcp.search.documents import IndexDocument
from tuik_mcp.search.indexer import OpenSearchStore, prepare, rebuild


@asynccontextmanager
async def session(mode, env=None):
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).with_name("search_stdio_server.py")), mode],
        env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            yield client


async def test_indexed_search_stdio_schema_results_and_fresh_session_ids():
    async with session("mock-index") as client:
        tools = (await client.list_tools()).tools
        assert len(tools) == 9
        search = next(t for t in tools if t.name == "search_tuik")
        properties = search.inputSchema["properties"]
        assert {
            "query",
            "limit",
            "kind",
            "theme",
            "published_after",
            "published_before",
            "downloadable_only",
            "live",
        } == set(properties)
        assert properties["live"]["default"] is False
        for arguments in [
            {"kind": "publication"},
            {"limit": 51},
            {"theme": ""},
        ]:
            invalid = await client.call_tool("search_tuik", {"query": "işsizlik", **arguments})
            assert invalid.isError
        for arguments in [
            {"query": " "},
            {"published_after": "2026-02-30"},
            {"published_after": "2026-11-02", "published_before": "2025-01-01"},
        ]:
            invalid = await client.call_tool("search_tuik", {"query": "işsizlik", **arguments})
            assert invalid.structuredContent["code"] == "invalid_input"
        found = await client.call_tool("search_tuik", {"query": "issizlik", "limit": 3})
        data = found.structuredContent
        assert not found.isError and data["source"] == "index" and data["backend"] == "opensearch"
        assert data["total"] == 3 and data["truncated"] is False
        assert data["facets"]["kind"]["counts"] == {"dataset": 1, "bulletin": 1, "metadata": 1}
        assert data["indexed_at"]
        for hit in data["results"]:
            assert hit["score"] == 2.5 and hit["highlights"]["title"]
            assert hit["source_url"] and hit["retrieved_at"] and hit["indexed_at"]
        ids = [hit["id"] for hit in data["results"]]
    # No search/cache in this server session: each ID must resolve from upstream fixtures.
    async with session("no-index") as fresh:
        for resource_id in ids:
            resource = await fresh.call_tool("get_tuik_resource", {"resource_id": resource_id})
            assert not resource.isError
            assert resource.structuredContent["id"] == resource_id
            assert resource.structuredContent["source_url"]


@pytest.mark.parametrize("mode", ["no-index", "missing-index"])
async def test_stdio_both_fallbacks_and_explicit_portal(mode):
    async with session(mode) as client:
        general = (
            await client.call_tool("search_tuik", {"query": "işsizlik", "limit": 2})
        ).structuredContent
        assert general["source"] == "portal" and len(general["results"]) == 2
        assert general["facets"] is None and general["indexed_at"] is None
        assert "used live portal search" in general["warnings"][0]
        datasets = (
            await client.call_tool(
                "search_tuik",
                {
                    "query": "Altın Hariç Dış Ticaret Hadleri",
                    "kind": "dataset",
                },
            )
        ).structuredContent
        assert datasets["source"] == "catalog" and datasets["results"]
        assert "used literal catalog matching" in datasets["warnings"][0]
        live = (
            await client.call_tool(
                "search_tuik",
                {
                    "query": "işsizlik",
                    "live": True,
                    "theme": "İstihdam",
                },
            )
        ).structuredContent
        assert live["source"] == "portal" and live["results"]
        assert "cannot apply these filters: theme" in live["warnings"][0]
        assert not any("used live portal search" in w for w in live["warnings"])


URL = os.getenv("TUIK_OPENSEARCH_URL")


@pytest.mark.opensearch
@pytest.mark.skipif(not URL, reason="Set TUIK_OPENSEARCH_URL")
async def test_real_index_through_stdio_filters_facets_and_empty_matches():
    alias = "tuik-mcp-test-" + uuid.uuid4().hex
    store = OpenSearchStore(URL)
    theme = "İstihdam, İşsizlik ve Ücret"
    stamp = datetime(2026, 11, 2, 9, tzinfo=UTC)
    docs = [
        IndexDocument(
            id="flow:DF_ALTINSIZ_DTH+V1.0",
            kind="dataset",
            title="İşsizlik Oranı",
            theme=[theme],
            downloadable=True,
            formats=["csv"],
            source_url="https://databrowser2.tuik.gov.tr/",
        ),
        IndexDocument(
            id="press:57987",
            kind="bulletin",
            title="İşsizlik Oranı Ekim 2026",
            theme=[theme],
            downloadable=True,
            formats=["xls"],
            publication_date="2026-11-02T23:59:59Z",
            source_url="https://veriportali.tuik.gov.tr/tr/press/57987",
        ),
        IndexDocument(
            id="press:57987:metadata",
            kind="metadata",
            title="İşsizlik Metaveri",
            parent_id="press:57987",
            theme=[theme],
            source_url="https://veriportali.tuik.gov.tr/tr/press/57987",
        ),
    ]
    try:
        payloads, _ = prepare(docs, stamp)
        await rebuild(store, alias, payloads)
        async with session(
            "cluster",
            {
                "TUIK_OPENSEARCH_URL": URL,
                "TUIK_OPENSEARCH_INDEX": alias,
            },
        ) as client:
            found = (
                await client.call_tool(
                    "search_tuik",
                    {
                        "query": "issizlik",
                        "limit": 1,
                    },
                )
            ).structuredContent
            assert found["backend"] == "opensearch" and found["total"] == 3
            assert found["truncated"] and len(found["results"]) == 1
            assert found["facets"]["kind"]["counts"] == {
                "dataset": 1,
                "bulletin": 1,
                "metadata": 1,
            }
            assert found["facets"]["theme"]["counts"] == {theme: 3}
            assert found["indexed_at"] == "2026-11-02T09:00:00.000Z"
            assert found["results"][0]["highlights"] and found["results"][0]["score"] > 0
            bulletin = (
                await client.call_tool(
                    "search_tuik",
                    {
                        "query": "işsizliğin",
                        "kind": "bulletin",
                        "theme": theme,
                        "downloadable_only": True,
                        "published_after": "2026-11-02",
                        "published_before": "2026-11-02",
                    },
                )
            ).structuredContent
            # The upper ISO date includes the entire day, including 23:59:59 UTC.
            assert bulletin["total"] == 1 and bulletin["results"][0]["id"] == "press:57987"
            assert bulletin["facets"]["kind"]["counts"] == {"bulletin": 1}
            empty = (
                await client.call_tool(
                    "search_tuik",
                    {
                        "query": "işsizlik",
                        "kind": "bulletin",
                        "published_before": "2025-01-01",
                    },
                )
            ).structuredContent
            assert empty["total"] == 0 and empty["results"] == [] and empty["truncated"] is False
            assert empty["facets"]["kind"]["counts"] == {} and empty["indexed_at"] is None
    finally:
        try:
            for name in await store.versions(alias):
                await store.delete(name)
        finally:
            await store.close()
