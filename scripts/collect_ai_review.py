"""Replay a frozen relevance pool through the real MCP server for an AI source review.

This collects evidence, never grades. It creates no human assessors or agreement
statistics. Live portal rankings are kept separate from historical evaluation runs.
"""

import argparse
import asyncio
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tuik_mcp.config import Settings

PROBES = (
    "flow:DF_ISGUCU_AYLIK_TEMEL_ISGUCU_C+V1.0",
    "flow:DF_ADNKS_T14+V1.1",
    "flow:DF_ADNKS_T17+V1.1",
    "flow:DF_SATIS_SEKLI_DURUMU_ILILCE_V3+V1.0",
    "flow:DF_SATIS_SEKLI_SATIS_DURUMU_V3+V1.0",
    "flow:DF_DOGUM_KDH_C+V1.0",
    "flow:DF_IHRACAT_MIKTAR_ENDEKS_V1+V1.0",
    "flow:DF_BD_DOGUM_OLUM+V1.1",
)


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


async def collect(pool_path, output):
    pool = json.loads(pool_path.read_text(encoding="utf-8"))
    settings = Settings.from_env()
    report = {
        "version": 1,
        "status": "running",
        "method": "single_ai_source_review_evidence",
        "started_at": datetime.now(UTC).isoformat(),
        "source_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "collector_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "pool_file_sha256": hashlib.sha256(pool_path.read_bytes()).hexdigest(),
        "package_version": version("tuik-mcp"),
        "python_version": platform.python_version(),
        "pacing": {
            "request_interval_seconds": settings.min_interval_seconds,
            "download_interval_seconds": settings.download_interval_seconds,
        },
        "scope": "Fresh portal search, resource metadata and eight bounded table probes; "
        "not a new OpenSearch run or an independent human judgment",
        "calls": [],
        "searches": {},
        "resources": {},
        "skipped": {},
        "probes": {},
    }
    failures = 0
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "tuik_mcp.server"],
        env={key: value for key, value in os.environ.items() if key.startswith("TUIK_")},
    )
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                report["tools"] = [tool.name for tool in (await session.list_tools()).tools]

                async def call(name, arguments):
                    nonlocal failures
                    index = len(report["calls"])
                    record = {
                        "tool": name,
                        "arguments": arguments,
                        "at": datetime.now(UTC).isoformat(),
                    }
                    try:
                        response = await session.call_tool(name, arguments)
                        data = response.structuredContent
                        if not isinstance(data, dict):
                            raise ValueError("Missing structured MCP response")
                        record["response"] = data
                        record["outcome"] = (
                            "error" if response.isError or data.get("status") == "error" else "ok"
                        )
                    except Exception as exc:
                        record.update(outcome="error", error=f"{type(exc).__name__}: {exc}")
                    report["calls"].append(record)
                    failures = failures + 1 if record["outcome"] == "error" else 0
                    write_report(output, report)
                    if failures >= 3:
                        raise RuntimeError("Stopped after three consecutive MCP failures")
                    return index

                for query in pool["queries"]:
                    report["searches"][query["id"]] = await call(
                        "search_tuik", {"query": query["text"], "limit": 10, "live": True}
                    )
                    print(f"search {query['id']}: {query['text']}", flush=True)
                for number, resource_id in enumerate(sorted(pool["documents"]), 1):
                    if resource_id.startswith("title:"):
                        report["skipped"][resource_id] = (
                            "Evaluation-only identity, not a tool input"
                        )
                        continue
                    report["resources"][resource_id] = await call(
                        "get_tuik_resource", {"resource_id": resource_id}
                    )
                    if number % 10 == 0:
                        print(f"resource {number}/{len(pool['documents'])}", flush=True)
                for resource_id in PROBES:
                    inspection = await call(
                        "inspect_tuik_data", {"resource_id": resource_id, "max_values": 100}
                    )
                    rows = await call("get_tuik_data", {"resource_id": resource_id, "limit": 2})
                    report["probes"][resource_id] = {"inspection": inspection, "rows": rows}
                    print(f"probe {resource_id}", flush=True)
                errors = sum(record["outcome"] == "error" for record in report["calls"])
                report["status"] = "partial" if errors or report["skipped"] else "ok"
    except Exception as exc:
        report["status"] = "error"
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        write_report(output, report)
    return 1 if report["status"] == "error" else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.resolve() == args.pool.resolve():
        parser.error("Choose a new output path; existing evidence must not be overwritten")
    return asyncio.run(collect(args.pool, args.output))


if __name__ == "__main__":
    raise SystemExit(main())
