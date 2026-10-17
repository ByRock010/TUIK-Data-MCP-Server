import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def test_stdio_discovery_and_input_validation():
    params = StdioServerParameters(command=sys.executable, args=["-m", "tuik_mcp.server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "search_tuik",
                "search_tuik_datasets",
                "get_tuik_resource",
                "get_tuik_data",
                "list_tuik_themes",
                "search_tuik_classifications",
            }
            assert all("ctx" not in t.inputSchema["properties"] for t in tools.tools)
            dataset_tool = next(t for t in tools.tools if t.name == "search_tuik_datasets")
            assert dataset_tool.inputSchema["properties"]["downloadable_only"]["default"] is True
            result = await session.call_tool("get_tuik_resource", {"resource_id": "invalid"})
            assert result.structuredContent["code"] == "invalid_input"
            assert result.structuredContent["source_url"]
            result = await session.call_tool("search_tuik", {"query": "", "limit": 0})
            assert result.isError
            result = await session.call_tool("search_tuik_datasets", {"query": " "})
            assert result.structuredContent["code"] == "invalid_input"
