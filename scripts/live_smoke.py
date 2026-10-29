"""Exercise the installed server through a real MCP stdio session (network required)."""

import argparse
import asyncio
import json
import os
import platform
import sys
import traceback
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tuik_mcp.config import Settings


async def bulletin_to_csv(call, resource_id):
    """Follow an explicit bulletin dataflow choice, rather than an unrelated catalog hit."""
    detail = await call("get_tuik_resource", {"resource_id": resource_id})
    choices = await call("get_tuik_data", {"resource_id": resource_id})
    assert choices["status"] == "related_datasets", choices
    dataset = next(
        item
        for item in choices["related_datasets"]
        if item["downloadable"] and "csv" in item["available_formats"]
    )
    assert dataset["id"] in {item["id"] for item in detail["related_datasets"]}
    rows = await call("get_tuik_data", {"resource_id": dataset["id"], "limit": 2})
    assert rows["status"] == "ok" and rows["rows"] and len(rows["rows"]) <= 2, rows
    assert rows["snapshot_id"] and any(
        link["format"] == "csv" and link["verified"] and link["source_url"] == rows["source_url"]
        for link in rows["download_links"]
    ), rows
    assert rows["resource_id"] == dataset["id"]
    return {
        "bulletin_id": resource_id,
        "bulletin_source_url": detail["source_url"],
        "selection_status": choices["status"],
        "selected_dataset_id": dataset["id"],
        "format": "csv",
        "data": rows,
    }


async def bulletin_to_excel(call, detail):
    """Select a real attachment ID from the bulletin before downloading Excel rows."""
    link = next(
        link
        for link in detail["download_links"]
        if link["format"] in {"xls", "xlsx"} and link.get("resource_id")
    )
    assert link["resource_id"].startswith(detail["id"] + ":file:")
    rows = await call("get_tuik_data", {"resource_id": link["resource_id"], "limit": 2})
    assert rows["status"] == "ok" and rows["rows"] and len(rows["rows"]) <= 2, rows
    assert rows["extraction"] in {"xls", "xlsx"} and rows["snapshot_id"] and rows["sheet"]
    assert rows["resource_id"] == link["resource_id"]
    assert any(item["verified"] for item in rows["download_links"])
    return {
        "bulletin_id": detail["id"],
        "bulletin_source_url": detail["source_url"],
        "selected_file_id": link["resource_id"],
        "data": rows,
    }


async def inspect_then_query(call, resource_id):
    """Inspect a table, then filter rows by an observed value from the same snapshot."""
    profile = await call("inspect_tuik_data", {"resource_id": resource_id, "max_values": 5})
    assert profile["status"] == "ok" and profile["snapshot_id"], profile
    column = next(c for c in profile["columns"] if c["values"] and not c["values"][0]["truncated"])
    value = column["values"][0]
    arguments = {
        "resource_id": resource_id,
        "filters": {column["name"]: value["value"]},
        "limit": 5,
    }
    if profile["sheet"] is not None:
        arguments["sheet"] = profile["sheet"]
    rows = await call("get_tuik_data", arguments)
    assert rows["status"] == "ok" and rows["rows"], rows
    assert all(row[column["name"]] == value["value"] for row in rows["rows"])
    assert rows["snapshot_id"] == profile["snapshot_id"]
    assert (rows["source_url"], rows["retrieved_at"]) == (
        profile["source_url"],
        profile["retrieved_at"],
    )
    if profile["complete"]:
        assert rows["total_rows"] == value["count"], (rows["total_rows"], value)
    return {
        "resource_id": resource_id,
        "format": profile["format"],
        "sheet": profile["sheet"],
        "source_url": profile["source_url"],
        "retrieved_at": profile["retrieved_at"],
        "snapshot_id": profile["snapshot_id"],
        "total_rows": profile["total_rows"],
        "complete": profile["complete"],
        "filter": arguments["filters"],
        "matched_rows": rows["total_rows"],
        "query_snapshot_id": rows["snapshot_id"],
    }


async def run():
    # The SDK otherwise drops TUIK_* variables, including requested live-test pacing.
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "tuik_mcp.server"],
        env={key: value for key, value in os.environ.items() if key.startswith("TUIK_")},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            report = {
                "package_version": version("tuik-mcp"),
                "python_version": platform.python_version(),
                "pacing": {
                    "request_interval_seconds": Settings.from_env().min_interval_seconds,
                    "download_interval_seconds": Settings.from_env().download_interval_seconds,
                },
                "tools": [t.name for t in tools.tools],
                "searches": {},
            }

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
                    report["bulletin_excel"] = await bulletin_to_excel(call, detail)
                    report["inspect_excel"] = await inspect_then_query(call, table["id"])
                    download = await call("get_tuik_data", {"resource_id": table["id"]})
                    assert download["status"] == "ok" and download["rows"]
                    assert download["extraction"] in {"xls", "xlsx"}
                    assert download["download_links"][0]["verified"]
                    report["download"] = download
            report["bulletin_csv"] = await bulletin_to_csv(call, "press:58347")
            datasets = await call(
                "search_tuik_datasets", {"query": "Altın Hariç Dış Ticaret Hadleri", "limit": 3}
            )
            assert datasets["results"], datasets
            dataset = datasets["results"][0]
            assert dataset["id"].startswith("flow:") and "csv" in dataset["available_formats"]
            report["datasets"] = datasets
            dataset_detail = await call("get_tuik_resource", {"resource_id": dataset["id"]})
            assert dataset_detail["available_formats"] == dataset["available_formats"]
            report["inspect_csv"] = await inspect_then_query(call, dataset["id"])
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
            methodology = await call("get_tuik_resource", {"resource_id": "press:57987"})
            pdf = next(link for link in methodology["download_links"] if link["format"] == "pdf")
            page = await call(
                "read_tuik_document",
                {
                    "resource_id": pdf["resource_id"],
                    "page": 3,
                    "max_chars": 120,
                },
            )
            assert page["status"] == "ok" and page["text"] and page["page_count"] >= 3
            assert page["truncated"] and page["next_page"] == 3 and page["next_offset"] == 120
            continuation = await call(
                "read_tuik_document",
                {
                    "resource_id": pdf["resource_id"],
                    "page": page["next_page"],
                    "offset": page["next_offset"],
                    "max_chars": 120,
                },
            )
            assert continuation["offset"] == 120 and continuation["text"]
            report["methodology"] = page
            report["methodology_continuation"] = continuation
            publications = await call(
                "search_tuik",
                {
                    "query": (
                        "Hanehalkı Bilişim Teknolojileri Kullanım Araştırması Mikro Veri Seti, 2026"
                    ),
                },
            )
            publication = next(
                r for r in publications["results"] if r["resource_type"] == "publication"
            )
            document = await call(
                "read_tuik_document",
                {
                    "resource_id": publication["id"],
                    "page": 1,
                    "max_chars": 400,
                },
            )
            assert document["status"] == "ok" and document["text"] and document["page_count"]
            report["publication"] = document
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
