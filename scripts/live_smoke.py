"""Exercise the installed server through a real MCP stdio session (network required)."""

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run():
    params = StdioServerParameters(command=sys.executable, args=["-m", "tuik_mcp.server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            report = {"tools": [t.name for t in tools.tools], "searches": {}}

            async def call(name, arguments):
                result = await session.call_tool(name, arguments)
                assert not result.isError, result.content
                data = result.structuredContent
                assert data and data.get("status") != "error", data
                assert data["source_url"]
                return data

            for query in [
                "işsizlik",
                "nüfus",
                "enflasyon",
                "TÜFE",
                "dış ticaret",
                "İstanbul nüfusu",
                "genç işsizlik",
                "konut satışları",
            ]:
                data = await call("search_tuik", {"query": query})
                assert data["results"], query
                assert all(r["source_url"] for r in data["results"])
                report["searches"][query] = [
                    {"id": r["id"], "title": r["title"], "source_url": r["source_url"]}
                    for r in data["results"][:3]
                ]
                if query == "işsizlik":
                    table = next(r for r in data["results"] if r["resource_type"] == "table")
                    press = next(r for r in data["results"] if r["resource_type"] == "bulletin")
                    detail = await call("get_tuik_resource", {"resource_id": press["id"]})
                    report["bulletin"] = {
                        "id": detail["id"],
                        "title": detail["title"],
                        "download_count": len(detail["download_links"]),
                        "source_url": detail["source_url"],
                    }
                    download = await call("get_tuik_data", {"resource_id": table["id"]})
                    assert download["status"] == "download_available"
                    assert download["download_links"][0]["verified"]
                    report["download"] = download
            csv = await call(
                "get_tuik_data", {"resource_id": "flow:DF_ALTINSIZ_DTH+V1.0", "limit": 2}
            )
            assert csv["status"] == "ok" and csv["rows"]
            report["csv"] = csv
            themes = await call("list_tuik_themes", {})
            assert themes["themes"]
            report["themes"] = themes
            classifications = await call(
                "search_tuik_classifications", {"query": "NACE", "limit": 3}
            )
            assert classifications["results"]
            report["classifications"] = classifications
            return report


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), ensure_ascii=False, indent=2))
