"""Inspect the current MCP capability baseline or export prompts to mcp_benchmark.py."""

import argparse
import asyncio
import base64
import csv
import hashlib
import io
import json
import sys
import tempfile
from collections import Counter
from importlib.metadata import version
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import BlobResourceContents

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
FLOW = "flow:DF_ALTINSIZ_DTH+V1.0"


def tasks():
    matrix = json.loads((HERE / "tasks.json").read_text())
    entries = matrix["tasks"]
    assert len(entries) == len({task["id"] for task in entries}) == 24
    assert Counter(task["persona"] for task in entries) == {
        "finance_research": 6,
        "manufacturing_export": 6,
        "local_government": 6,
        "academic_journalism": 6,
    }
    assert Counter(task["expected_support"] for task in entries) == {
        "supported": 20,
        "unsupported": 4,
    }
    held_out = [task for task in entries if task["split"] == "held_out"]
    assert len(held_out) == len({task["persona"] for task in held_out}) == 4
    for task in entries:
        assert all(
            task["rubric"][field] for field in ("expected", "period", "unit", "official_sources")
        )
        if "artifact" in task:
            assert all(task["artifact"][field] for field in ("format", "editability", "layout"))
    return entries


async def chart_probe(session, output_png):
    inspected = await session.call_tool("inspect_tuik_data", {"resource_id": FLOW})
    assert inspected.structuredContent["status"] == "ok"
    result = await session.call_tool(
        "chart_tuik_data",
        {
            "resource_id": FLOW,
            "x": ["Zaman (2015-01)"],
            "y": ["Gözlem Değeri"],
            "decimal_separator": ",",
            "title": "Altın hariç dış ticaret hadleri · Gıda ve canlı hayvanlar (2015 bazlı oran)",
            "save": True,
        },
    )
    content = result.structuredContent
    assert content and content["status"] == "ok", content
    assert content["x_order"] == "chronological"
    artifact = content["artifact"]
    files = {item["name"]: item for item in artifact["files"]}

    async def read(item):
        (resource,) = (await session.read_resource(item["uri"])).contents
        data = (
            base64.b64decode(resource.blob)
            if isinstance(resource, BlobResourceContents)
            else resource.text.encode()
        )
        assert hashlib.sha256(data).hexdigest() == item["sha256"]
        return data

    png = await read(files["chart.png"])
    assert png.startswith(bytes.fromhex("89504e470d0a1a0a"))
    chart_csv = await read(files["chart-data.csv"])
    source = await read(files["source.csv"])
    assert source == (ROOT / "tests" / "fixtures" / "data.csv").read_bytes()
    rows = list(csv.reader(io.StringIO(chart_csv.decode())))
    originals = list(csv.reader(io.StringIO(source.decode("utf-8-sig")), delimiter=";"))
    assert [row[0] for row in rows[1:]] == [row[9] for row in originals[1:]]
    assert [float(row[1]) for row in rows[1:]] == [
        float(row[10].replace(",", ".")) for row in originals[1:]
    ]
    (manifest,) = (await session.read_resource(artifact["manifest_uri"])).contents
    assert json.loads(manifest.text)["inputs"][0]["sha256"] == hashlib.sha256(source).hexdigest()
    if output_png:
        output_png.write_bytes(png)
    return {
        "id": "F05",
        "points": rows[1:],
        "sha256_source": hashlib.sha256(source).hexdigest(),
        "x_order": content["x_order"],
        "artifacts_read": ["chart.png", "chart-data.csv", "source.csv", "manifest.json"],
    }


async def run(entries, output_png):
    # The fixture server runs the production entry point with recorded public input
    # from tests/fixtures. Inherited TUIK variables cannot select a live backend.
    with tempfile.TemporaryDirectory() as folder:
        environment = {"TUIK_ARTIFACT_DIR": folder, "MPLCONFIGDIR": folder}
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(ROOT / "tests" / "search_stdio_server.py"), "fixture"],
            env=environment,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {tool.name for tool in (await session.list_tools()).tools}
                assert len(tools) == 9
                assert all(set(task["current_tools"]) <= tools for task in entries)
                chart = await chart_probe(session, output_png)
    return {
        "mcp_sdk": version("mcp"),
        "python": sys.version.split()[0],
        "tools": sorted(tools),
        "provider": None,
        "model": None,
        "search_index": None,
        "server_state": "fresh; F05 chart after source inspection",
        "permissions": "local fixture stdio; no model tools, no live TÜİK calls",
        "outcomes": {task["id"]: task["expected_support"] for task in entries},
        "chart_probe": chart,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompts", choices=("development", "held_out"))
    parser.add_argument("--png", type=Path)
    args = parser.parse_args()
    entries = tasks()
    if args.prompts:
        output = [
            {"id": item["id"], "text": item["text"]}
            for item in entries
            if item["split"] == args.prompts
        ]
    else:
        output = asyncio.run(run(entries, args.png))
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
