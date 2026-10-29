import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def test_real_stdio_query_schema_continuation_and_errors():
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).with_name("query_stdio_server.py"))],
        env={"TUIK_MIN_INTERVAL_SECONDS": "0"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            tool = next(t for t in tools.tools if t.name == "get_tuik_data")
            assert {"query", "cursor"} <= set(tool.inputSchema["properties"])
            assert (
                tool.inputSchema["$defs"]["TableQuery"]["properties"]["predicates"]["maxItems"]
                == 20
            )
            resource = "flow:DF_QUERY+V1.0"
            query = {"columns": ["n"], "sort": [{"column": "n", "value_type": "number"}]}
            first = await session.call_tool(
                "get_tuik_data",
                {
                    "resource_id": resource,
                    "query": query,
                    "limit": 1,
                },
            )
            first = first.structuredContent
            assert first["rows"] == [{"n": "1"}] and first["total_rows"] == 3
            assert first["has_more"] and first["next_cursor"] and first["cursor_expires_at"]
            second = await session.call_tool(
                "get_tuik_data",
                {
                    "resource_id": resource,
                    "cursor": first["next_cursor"],
                    "limit": 1,
                },
            )
            second = second.structuredContent
            assert second["rows"] == [{"n": "2"}]
            assert (second["snapshot_id"], second["source_url"], second["retrieved_at"]) == (
                first["snapshot_id"],
                first["source_url"],
                first["retrieved_at"],
            )
            for arguments, code in [
                ({"cursor": "tampered"}, "invalid_cursor"),
                (
                    {"cursor": first["next_cursor"], "query": {"columns": ["city"]}},
                    "cursor_mismatch",
                ),
                ({"query": {"columns": ["absent"]}}, "invalid_columns"),
            ]:
                result = await session.call_tool(
                    "get_tuik_data", {"resource_id": resource, **arguments}
                )
                assert result.structuredContent["code"] == code
            invalid = await session.call_tool(
                "get_tuik_data",
                {
                    "resource_id": resource,
                    "query": {"predicates": [{"op": "sql", "column": "n"}]},
                },
            )
            assert invalid.isError
