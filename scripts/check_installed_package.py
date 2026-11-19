"""Check a non-editable wheel/sdist installation outside the checkout (#98).

Run with the clean environment's Python. Uses the real MCP stdio transport,
invalid-input calls (no upstream requests) and an in-memory chart fixture.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from contextlib import chdir
from importlib import metadata, resources
from pathlib import Path

TOOLS = {
    "search_tuik",
    "search_tuik_datasets",
    "get_tuik_resource",
    "get_tuik_data",
    "inspect_tuik_data",
    "chart_tuik_data",
    "list_tuik_themes",
    "read_tuik_document",
    "search_tuik_classifications",
}


async def stdio_check():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    # Empty TUIK settings so the smoke does not use a developer's private cluster.
    env = {k: v for k, v in os.environ.items() if not k.startswith("TUIK_")}
    env.pop("PYTHONPATH", None)
    params = StdioServerParameters(command=sys.executable, args=["-m", "tuik_mcp.server"], env=env)
    async with asyncio.timeout(30):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {tool.name for tool in tools.tools} == TOOLS
                result = await session.call_tool("get_tuik_resource", {"resource_id": "invalid"})
                assert result.structuredContent["code"] == "invalid_input"
    return sorted(TOOLS)


async def literal_fallback_check():
    import httpx

    from tuik_mcp.clients.base import HttpClient
    from tuik_mcp.config import Settings
    from tuik_mcp.services.catalog import CatalogService

    def handler(request):
        assert request.url.path == "/api/tr/dataflows"
        return httpx.Response(
            200,
            json=[{"id": "DF_FIXTURE+V1.0", "name": "İşsizlik", "downloadable": True}],
        )

    http = HttpClient(Settings(min_interval_seconds=0), httpx.MockTransport(handler))
    catalog = CatalogService(http)
    try:
        assert catalog.index is None
        result = await catalog.search_datasets("işsizlik")
        assert result.backend == "literal"
        assert [r.id for r in result.results] == ["flow:DF_FIXTURE+V1.0"]
    finally:
        await catalog.close()
        await http.close()


def check(profile):
    import tuik_mcp
    from tuik_mcp.charts import Line, Plot, render
    from tuik_mcp.doctor import diagnose
    from tuik_mcp.parsers.query import QueryError

    package = Path(tuik_mcp.__file__).resolve()
    assert package.is_relative_to(Path(sys.prefix).resolve()), "Editable/source install detected"
    for name in ("analysis.json", "mapping.json", "synonyms.txt"):
        assert resources.files("tuik_mcp.search").joinpath(name).read_text().strip()
    env = {k: v for k, v in os.environ.items() if not k.startswith("TUIK_")}
    env.pop("PYTHONPATH", None)
    for command in (["-m", "tuik_mcp.doctor", "--json"],):
        result = subprocess.run(
            [sys.executable, *command],
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        report = json.loads(result.stdout)
        assert report["ok"]
        assert next(c for c in report["checks"] if c["name"] == "network")["status"] == "skipped"
    entries = {e.name for e in metadata.distribution("tuik-mcp").entry_points}
    assert {"tuik-mcp", "tuik-mcp-index", "tuik-mcp-doctor"} <= entries
    assert diagnose()["ok"]
    plot = Plot(["2024", "2025"], [Line("Example", "value", [1.0, None], ["ok", "missing"])])
    if profile == "viz":
        image, width, height = render(plot, "line", "Fixture", "unit", "synthetic; not TÜİK data")
        assert image.startswith(b"\x89PNG") and width > 0 and height > 0
    else:
        try:
            render(plot, "line", "Fixture", "unit", "synthetic; not TÜİK data")
        except QueryError as exc:
            assert exc.code == "visualization_unavailable"
        else:
            raise AssertionError("Core-only environment unexpectedly contains matplotlib")
    # Core discovery must not import/download embeddings, even if semantic_search defaults true.
    assert "fastembed" not in sys.modules
    asyncio.run(literal_fallback_check())
    tools = asyncio.run(stdio_check())
    print(json.dumps({"profile": profile, "version": metadata.version("tuik-mcp"), "tools": tools}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("core", "viz"), required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="tuik-installed-") as directory:
        # Windows cannot delete a directory while it is the process's cwd.
        # Restore cwd on success or failure before TemporaryDirectory cleans up.
        with chdir(directory):
            check(args.profile)


if __name__ == "__main__":
    main()
