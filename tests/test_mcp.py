import json
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
                "inspect_tuik_data",
                "chart_tuik_data",
                "get_tuik_observations",
                "list_tuik_themes",
                "read_tuik_document",
                "search_tuik_classifications",
            }
            document_tool = next(t for t in tools.tools if t.name == "read_tuik_document")
            assert document_tool.inputSchema["properties"]["max_chars"]["maximum"] == 20000
            assert "offset" in document_tool.inputSchema["properties"]
            assert document_tool.inputSchema["properties"]["tables"]["default"] is False
            assert all("ctx" not in t.inputSchema["properties"] for t in tools.tools)
            dataset_tool = next(t for t in tools.tools if t.name == "search_tuik_datasets")
            assert dataset_tool.inputSchema["properties"]["downloadable_only"]["default"] is True
            result = await session.call_tool("get_tuik_resource", {"resource_id": "invalid"})
            assert result.structuredContent["code"] == "invalid_input"
            assert result.structuredContent["source_url"]
            # The text is the same payload as compact JSON: indentation only costs tokens.
            (text,) = result.content
            assert json.loads(text.text) == result.structuredContent
            assert "\n" not in text.text and '": ' not in text.text
            result = await session.call_tool("search_tuik", {"query": "", "limit": 0})
            assert result.isError
            result = await session.call_tool("search_tuik_datasets", {"query": " "})
            assert result.structuredContent["code"] == "invalid_input"
            inspect_tool = next(t for t in tools.tools if t.name == "inspect_tuik_data")
            properties = inspect_tool.inputSchema["properties"]
            assert properties["max_values"]["maximum"] == 200
            assert {"resource_id", "sheet", "columns", "max_values"} <= set(properties)
            result = await session.call_tool(
                "inspect_tuik_data", {"resource_id": "flow:x", "max_values": 0}
            )
            assert result.isError
            result = await session.call_tool("inspect_tuik_data", {"resource_id": "invalid"})
            assert result.structuredContent["code"] == "invalid_input"


async def test_stdio_workflow_prompts_and_text_only_catalog():
    params = StdioServerParameters(command=sys.executable, args=["-m", "tuik_mcp.server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            assert initialized.capabilities.prompts is not None
            prompts = await session.list_prompts()
            examples = {
                "latest_indicator_brief": "F01",
                "regional_comparison": "L02",
                "trend_analysis": "F05",
                "source_methodology_audit": "A01",
            }
            assert {prompt.name for prompt in prompts.prompts} == examples.keys()
            for name, task in examples.items():
                result = await session.get_prompt(name)
                (message,) = result.messages
                assert message.role == "user" and message.content.type == "text"
                assert f"benchmark {task}" in message.content.text
                assert "adjustment, source cutoff or release date" in message.content.text
                assert "output format" in message.content.text
            regional = await session.get_prompt("regional_comparison")
            regional_text = regional.messages[0].content.text
            assert "Monthly provincial unemployment is unsupported" in regional_text
            assert "national monthly series" in regional_text

            # A client can follow the documented recipe using only text tool results.
            trend = await session.get_prompt("trend_analysis")
            assert "`get_tuik_observations`" in trend.messages[0].content.text
            catalog = await session.call_tool("get_tuik_observations", {})
            (content,) = catalog.content
            assert content.type == "text"
            payload = json.loads(content.text)
            assert payload["status"] == "catalog"
            assert any(recipe["domain"] == "foreign_trade" for recipe in payload["recipes"])


async def test_trend_prompt_reflects_chart_dependency(monkeypatch):
    from mcp.server.fastmcp import FastMCP

    from tuik_mcp import prompts

    mcp = FastMCP()
    prompts.register_prompts(mcp)
    monkeypatch.setattr(prompts, "find_spec", lambda _: None)
    result = await mcp.get_prompt("trend_analysis")
    prompt = result.messages[0].content.text
    assert "sourced text table" in prompt
    assert "`chart_tuik_data`" not in prompt

    monkeypatch.setattr(prompts, "find_spec", lambda _: object())
    result = await mcp.get_prompt("trend_analysis")
    prompt = result.messages[0].content.text
    assert "`chart_tuik_data`" in prompt
    assert "sourced text table" in prompt
