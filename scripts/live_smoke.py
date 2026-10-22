"""Exercise the installed server through a real MCP stdio session (network required)."""

import argparse
import asyncio
import json
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

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
                    assert download["status"] == "ok" and download["rows"]
                    assert download["extraction"] in {"xls", "xlsx"}
                    assert download["download_links"][0]["verified"]
                    report["download"] = download
            datasets = await call(
                "search_tuik_datasets", {"query": "Altın Hariç Dış Ticaret Hadleri", "limit": 3}
            )
            assert datasets["results"], datasets
            dataset = datasets["results"][0]
            assert dataset["id"].startswith("flow:") and "csv" in dataset["available_formats"]
            report["datasets"] = datasets
            dataset_detail = await call("get_tuik_resource", {"resource_id": dataset["id"]})
            assert dataset_detail["available_formats"] == dataset["available_formats"]
            csv = await call("get_tuik_data", {"resource_id": dataset["id"], "limit": 2})
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write a JSON report on success or failure")
    args = parser.parse_args(argv)
    checked_at = datetime.now(UTC).isoformat()
    try:
        report = {"status": "ok", "checked_at": checked_at, **asyncio.run(run())}
        exit_code = 0
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        report = {
            "status": "error",
            "checked_at": checked_at,
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
        exit_code = 1
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output, end="")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
