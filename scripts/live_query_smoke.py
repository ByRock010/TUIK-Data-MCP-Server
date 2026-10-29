"""Paced MCP inspect -> typed/projected query -> two snapshot-bound pages (#52)."""

import argparse
import asyncio
import json
import os
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def query_two_pages(call, resource_id):
    profile = await call("inspect_tuik_data", {"resource_id": resource_id, "max_values": 5})
    assert profile["status"] == "ok" and profile["complete"], profile
    filter_column = next(
        column
        for column in profile["columns"]
        if column["values"]
        and column["values"][0]["count"] >= 2
        and not column["values"][0]["truncated"]
    )
    value = filter_column["values"][0]
    sort_column = next(
        column
        for column in profile["columns"]
        if column["name"] != filter_column["name"]
        and column["type_hint"] == "number"
        and column["decimal_separator"] != "ambiguous"
    )
    sort = {"column": sort_column["name"], "value_type": "number"}
    if sort_column["decimal_separator"]:
        sort["decimal_separator"] = sort_column["decimal_separator"]
    query = {
        "columns": [filter_column["name"], sort_column["name"]],
        "predicates": [{"op": "eq", "column": filter_column["name"], "value": value["value"]}],
        "sort": [sort],
    }
    first = await call("get_tuik_data", {"resource_id": resource_id, "query": query, "limit": 1})
    assert first["has_more"] and first["next_cursor"] and first["total_rows"] == value["count"]
    second = await call(
        "get_tuik_data",
        {
            "resource_id": resource_id,
            "cursor": first["next_cursor"],
            "limit": 1,
        },
    )
    together = await call("get_tuik_data", {"resource_id": resource_id, "query": query, "limit": 2})
    assert first["rows"] + second["rows"] == together["rows"]
    for page in (first, second, together):
        assert page["status"] == "ok" and len(page["rows"]) >= 1
        assert (page["snapshot_id"], page["source_url"], page["retrieved_at"], page["sheet"]) == (
            profile["snapshot_id"],
            profile["source_url"],
            profile["retrieved_at"],
            profile["sheet"],
        )
        assert all(row[filter_column["name"]] == value["value"] for row in page["rows"])
        assert page["columns"] == query["columns"]
    return {
        "resource_id": resource_id,
        "format": profile["format"],
        "query": query,
        "source_url": first["source_url"],
        "retrieved_at": first["retrieved_at"],
        "snapshot_id": first["snapshot_id"],
        "sheet": first["sheet"],
        "total_rows": first["total_rows"],
        "cursor_expires_at": first["cursor_expires_at"],
        "pages": [
            {
                key: page[key]
                for key in (
                    "rows",
                    "snapshot_id",
                    "source_url",
                    "retrieved_at",
                    "sheet",
                    "total_rows",
                    "has_more",
                )
            }
            for page in (first, second)
        ],
    }


async def run():
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "tuik_mcp.server"],
        env={key: value for key, value in os.environ.items() if key.startswith("TUIK_")},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            async def call(name, arguments):
                result = await session.call_tool(name, arguments)
                assert not result.isError, result.content
                data = result.structuredContent
                assert data and data.get("status") != "error", data
                return data

            csv = await query_two_pages(call, "flow:DF_ALTINSIZ_DTH+V1.0")
            bulletin = await call("get_tuik_resource", {"resource_id": "press:57987"})
            file = next(
                link
                for link in bulletin["download_links"]
                if link["format"] in {"xls", "xlsx"} and "Temel işgücü" in link["title"]
            )
            excel = await query_two_pages(call, file["resource_id"])
            return {"csv": csv, "excel": excel}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    checked_at = datetime.now(UTC).isoformat()
    try:
        report = {"status": "ok", "checked_at": checked_at, **asyncio.run(run())}
        result = 0
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        report = {
            "status": "error",
            "checked_at": checked_at,
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
        result = 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return result


if __name__ == "__main__":
    sys.exit(main())
