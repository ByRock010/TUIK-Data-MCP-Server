"""Usage scenarios with latency, payload and upstream-request metrics over real MCP stdio.

Every step is one tool call a client would make, with a check of the answer. The
cold pass runs each step once on a fresh server (downloads, parsing, TÜİK pacing
included); the warm pass repeats the repeatable steps on the same server, so the
caches are in play. Per call the report keeps wall time, response size and the
upstream requests the server made (counted from its httpx log lines).

    python scripts/usage_perf.py --output /tmp/usage-perf.json
    python scripts/usage_perf.py --compare before.json --output after.json

Needs network access to TÜİK. Indexed steps need TUIK_OPENSEARCH_URL and are
reported as skipped without it.
"""

import argparse
import asyncio
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
BULLETIN = "press:57988"  # İşgücü İstatistikleri, Eylül 2026
FLOW = "flow:DF_ISGUCU_AYLIK_TEMEL_ISGUCU_C+V1.0"  # 21,600 rows x 15 columns
RATE = {
    "İşgücü Göstergeleri": "İşsizlik Oranı (%)",
    "Temel Yaş": "15+",
    "Mevsimsel Düzeltme": "Mevsim etkilerinden arındırılmış",
}
QUERY = {
    "columns": ["Zaman", "Cinsiyet", "Gözlem Değeri"],
    "predicates": [
        *({"op": "eq", "column": column, "value": value} for column, value in RATE.items()),
        {"op": "range", "column": "Zaman", "value_type": "period", "period_format": "month",
         "minimum": "2026-01"},
    ],
    "sort": [{"column": "Zaman", "value_type": "period", "period_format": "month",
              "direction": "desc"}],
}  # fmt: skip


@dataclass
class Step:
    id: str
    group: str
    tool: str
    arguments: object  # dict, or a function of the earlier results
    check: object  # raises AssertionError on a wrong answer
    repeat: bool = True
    indexed: bool = False  # needs the OpenSearch index


def link(results, fmt):
    return next(
        item["resource_id"]
        for item in results["bulletin"]["download_links"]
        if item["format"] == fmt and item.get("resource_id")
    )


def titles(result):
    return " | ".join(item["title"] for item in result["results"])


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


def error(code):
    return lambda r, _: expect(
        r.get("status") == "error" and r.get("code") == code,
        f"expected error {code}, got {r.get('status')}/{r.get('code')}",
    )


# Steps whose arguments come from another group's result (link() reads the bulletin).
PREREQUISITES = {"excel": "bulletin", "pdf": "bulletin"}


def select(plan: list[Step], only: list[str] | None) -> list[Step]:
    """The steps of the chosen groups plus the prerequisite steps they read results from."""
    if not only:
        return plan
    needed = {PREREQUISITES[group] for group in only if group in PREREQUISITES}
    return [step for step in plan if step.group in only or step.id in needed]


def passed(entry: dict) -> bool:
    """A step passes only if its cold call and every warm repeat passed."""
    return entry["cold"]["ok"] and entry.get("warm", {}).get("ok", True)


def within_budget(result, _):
    """A call with default arguments stays inside the 64 KiB output budget."""
    expect(result["status"] == "ok" and result["rows"], "no rows")
    expect(result["response_bytes"] <= 64 * 1024, f"{result['response_bytes']} bytes")


def steps() -> list[Step]:
    def search(step_id, arguments, check, **kwargs):
        return Step(step_id, "discovery", "search_tuik", arguments, check, **kwargs)

    return [
        Step("themes", "discovery", "list_tuik_themes", {},
             lambda r, _: expect(len(r["themes"]) >= 15, "fewer than 15 themes")),
        search("search_ascii", {"query": "issizlik orani", "limit": 5},
               lambda r, _: expect(r["backend"] == "opensearch" and "İşgücü" in titles(r),
                                   f"{r['backend']}: {titles(r)}"), indexed=True),
        search("search_typo", {"query": "enflsyon", "limit": 5},
               lambda r, _: expect("Tüketici Fiyat" in titles(r), titles(r)), indexed=True),
        search("search_paraphrase", {"query": "kaç çift ayrıldı", "limit": 5},
               lambda r, _: expect("Boşanma" in titles(r), titles(r)), indexed=True),
        search("search_filtered",
               {"query": "işgücü", "kind": "bulletin", "theme": "İstihdam, İşsizlik ve Ücret",
                "published_after": "2026-09-01", "limit": 5},
               lambda r, _: expect(
                   r["results"] and all(
                       item["resource_type"] == "bulletin"
                       and item["publication_date"] >= "2026-09-01" for item in r["results"]),
                   "filters not applied"), indexed=True),
        search("search_passage",
               {"query": "CATI telefonla görüşme", "kind": "document_chunk", "limit": 3},
               lambda r, _: expect(r["results"] and isinstance(r["results"][0]["page"], int),
                                   "no PDF passage with a page"), indexed=True),
        search("search_table",
               {"query": "temel işgücü göstergeleri", "kind": "table", "limit": 3},
               lambda r, _: expect(r["results"] and r["results"][0]["sheet"],
                                   "no workbook table with a sheet"), indexed=True),
        search("search_gibberish", {"query": "xqzvkj", "limit": 5},
               lambda r, _: expect(r["total"] == 0 and not r["results"],
                                   f"{r['total']} results for gibberish"), indexed=True),
        search("search_wrong_theme", {"query": "nüfus", "theme": "nufus"},
               lambda r, _: expect(r["total"] == 0 and "list_tuik_themes" in str(r["warnings"]),
                                   "no hint for an unknown theme"), indexed=True),
        search("search_live", {"query": "dış ticaret", "live": True, "limit": 5},
               lambda r, _: expect(r["backend"] == "portal" and r["results"], "no live results")),
        search("search_bad_date", {"query": "nüfus", "published_after": "01.09.2026"},
               error("invalid_input")),
        Step("datasets_ascii", "discovery", "search_tuik_datasets", {"query": "tufe", "limit": 5},
             lambda r, _: expect("TÜFE" in titles(r), titles(r) or "no datasets"), indexed=True),
        Step("classifications", "discovery", "search_tuik_classifications",
             {"query": "NACE", "limit": 10},
             lambda r, _: expect(len(r["results"]) >= 5, "fewer than 5 NACE versions")),
        Step("bulletin", "bulletin", "get_tuik_resource", {"resource_id": BULLETIN},
             lambda r, _: expect(len(r["download_links"]) >= 5 and r["related_datasets"],
                                 "bulletin without files or related datasets")),
        Step("bulletin_missing", "bulletin", "get_tuik_resource",
             {"resource_id": "press:999999999"}, error("not_found")),
        Step("bulletin_choice", "bulletin", "get_tuik_data", {"resource_id": BULLETIN},
             lambda r, _: expect(r["status"] == "related_datasets", r["status"])),
        Step("csv_inspect", "csv", "inspect_tuik_data", {"resource_id": FLOW, "max_values": 10},
             lambda r, _: expect(r["status"] == "ok" and r["complete"]
                                 and r["total_rows"] > 20_000, "incomplete profile")),
        Step("csv_rows", "csv", "get_tuik_data", {"resource_id": FLOW, "limit": 5},
             lambda r, done: expect(
                 len(r["rows"]) == 5 and r["total_rows"] == done["csv_inspect"]["total_rows"],
                 "row count differs from the profile")),
        Step("csv_default", "csv", "get_tuik_data", {"resource_id": FLOW}, within_budget),
        Step("csv_filter", "csv", "get_tuik_data",
             {"resource_id": FLOW, "limit": 5, "filters": {"Cinsiyet": "Kadın"}},
             lambda r, _: expect(r["rows"] and all(row["Cinsiyet"] == "Kadın"
                                                   for row in r["rows"]), "filter not applied")),
        Step("csv_query", "csv", "get_tuik_data",
             {"resource_id": FLOW, "limit": 6, "query": QUERY},
             lambda r, _: expect(
                 r["has_more"] and [row["Zaman"] for row in r["rows"]]
                 == sorted((row["Zaman"] for row in r["rows"]), reverse=True),
                 "not sorted or not paginated")),
        Step("csv_cursor", "csv", "get_tuik_data",
             lambda done: {"resource_id": FLOW, "limit": 6,
                           "cursor": done["csv_query"]["next_cursor"]},
             lambda r, done: expect(
                 r["rows"] and r["snapshot_id"] == done["csv_query"]["snapshot_id"],
                 "second page left the snapshot")),
        Step("csv_bad_filter", "csv", "get_tuik_data",
             {"resource_id": FLOW, "filters": {"Olmayan Kolon": "x"}}, error("invalid_filters")),
        Step("csv_bad_cursor", "csv", "get_tuik_data",
             {"resource_id": FLOW, "cursor": "abc.def"}, error("invalid_cursor")),
        Step("chart", "csv", "chart_tuik_data",
             {"resource_id": FLOW, "x": ["Zaman"], "y": ["Gözlem Değeri"], "series": "Cinsiyet",
              "last": 24, "filters": RATE},
             lambda r, _: expect(r["status"] == "ok" and len(r["series"]) == 3
                                 and r["image_bytes"] > 10_000, "no three-series chart")),
        Step("xls_rows", "excel", "get_tuik_data",
             lambda done: {"resource_id": link(done, "xls"), "limit": 3},
             lambda r, _: expect(r["status"] == "ok" and r["sheet"] and len(r["rows"]) == 3,
                                 "no workbook rows")),
        Step("xls_default", "excel", "get_tuik_data",
             lambda done: {"resource_id": link(done, "xls")}, within_budget),
        Step("xls_inspect", "excel", "inspect_tuik_data",
             lambda done: {"resource_id": link(done, "xls")},
             lambda r, done: expect(
                 r["status"] == "ok" and r["snapshot_id"] == done["xls_rows"]["snapshot_id"],
                 "profile left the snapshot")),
        Step("xls_query", "excel", "get_tuik_data",
             lambda done: {"resource_id": link(done, "xls"), "limit": 3, "query": {}},
             lambda r, _: expect(r["status"] == "ok" and r["has_more"], "no pagination")),
        Step("pdf_page", "pdf", "read_tuik_document",
             lambda done: {"resource_id": link(done, "pdf"), "page": 1, "max_chars": 2000},
             lambda r, _: expect(r["status"] == "ok" and r["page_count"] >= 2, "no PDF text")),
        Step("pdf_tables", "pdf", "read_tuik_document",
             lambda done: {"resource_id": link(done, "pdf"), "page": 5, "max_chars": 2000,
                           "tables": True},
             lambda r, _: expect(r["status"] == "ok" and r["tables"] is not None, "no tables")),
        Step("pdf_from_bulletin", "pdf", "read_tuik_document",
             {"resource_id": BULLETIN, "page": 3, "max_chars": 2000},
             lambda r, done: expect(
                 r["status"] == "ok" and r["resolved_resource_id"] == link(done, "pdf"),
                 "bulletin did not select its PDF")),
    ]  # fmt: skip


def count_requests(lines: list[str], index_host: str | None) -> dict:
    """Upstream requests in the server's httpx log lines, split by TÜİK and the index."""
    counts = {"tuik": 0, "index": 0}
    for line in lines:
        _, marker, rest = line.partition("HTTP Request: ")
        if marker:
            host = urlsplit(rest.split()[1]).netloc if len(rest.split()) > 1 else ""
            counts["index" if index_host and host == index_host else "tuik"] += 1
    return counts


def server_rss_mb() -> float | None:
    """Resident memory of this process's tuik-mcp child, in MiB."""
    listing = subprocess.run(
        ["ps", "-axo", "ppid=,rss=,command="], capture_output=True, text=True, check=False
    ).stdout
    for line in listing.splitlines():
        ppid, rss, command = line.split(None, 2)
        if int(ppid) == os.getpid() and "tuik" in command:
            return round(int(rss) / 1024, 1)
    return None


def spread(samples: list[float]) -> dict:
    return {
        "n": len(samples),
        "median_ms": round(statistics.median(samples), 1),
        "min_ms": round(min(samples), 1),
        "max_ms": round(max(samples), 1),
    }


@dataclass
class Recorder:
    session: ClientSession
    log: Path
    index_host: str | None
    position: int = 0
    results: dict = field(default_factory=dict)

    def new_log_lines(self) -> list[str]:
        text = self.log.read_text(errors="replace")
        lines, self.position = text[self.position :].splitlines(), len(text)
        return lines

    async def call(self, step: Step) -> dict:
        arguments = step.arguments(self.results) if callable(step.arguments) else step.arguments
        self.new_log_lines()
        started = time.perf_counter()
        response = await asyncio.wait_for(self.session.call_tool(step.tool, arguments), 300)
        elapsed = (time.perf_counter() - started) * 1000
        text = next((c.text for c in response.content if c.type == "text"), "{}")
        image = sum(len(c.data) for c in response.content if c.type == "image")
        payload = json.loads(text)
        payload["image_bytes"] = image
        payload["response_bytes"] = len(text.encode())
        sample = {
            "ms": round(elapsed, 1),
            "bytes": len(text.encode()) + image,
            "requests": count_requests(self.new_log_lines(), self.index_host),
        }
        try:
            step.check(payload, self.results)
            sample["ok"] = True
        except (AssertionError, KeyError, IndexError, TypeError, StopIteration) as exc:
            sample.update(ok=False, failure=f"{type(exc).__name__}: {exc}"[:300])
        self.results.setdefault(step.id, payload)
        return sample


def summarize(entries: list[dict]) -> dict:
    ran = [entry for entry in entries if "cold" in entry]
    return {
        "steps": len(entries),
        "passed": sum(passed(entry) for entry in ran),
        "failed": [entry["id"] for entry in ran if not passed(entry)],
        "skipped": len(entries) - len(ran),
        "cold_total_s": round(sum(entry["cold"]["ms"] for entry in ran) / 1000, 1),
        "warm_median_sum_ms": round(
            sum(entry["warm"]["median_ms"] for entry in ran if "warm" in entry), 1
        ),
        "response_bytes": sum(entry["cold"]["bytes"] for entry in ran),
    }


async def run(args) -> dict:
    opensearch = os.environ.get("TUIK_OPENSEARCH_URL")
    index_host = urlsplit(opensearch).netloc if opensearch else None
    report = {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "version": version("tuik-mcp"),
        "python": sys.version.split()[0],
        "indexed": bool(opensearch),
        "repeats": args.repeats,
        "steps": [],
    }
    with tempfile.TemporaryDirectory() as folder:
        log = Path(folder) / "server.log"
        params = StdioServerParameters(command=args.server, args=[], env=dict(os.environ))
        started = time.perf_counter()
        with log.open("w") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = (await session.list_tools()).tools
                    schema = json.dumps([tool.model_dump(mode="json") for tool in tools])
                    report["startup"] = {
                        "ms": round((time.perf_counter() - started) * 1000, 1),
                        "tools": len(tools),
                        "tool_schema_bytes": len(schema.encode()),
                        "rss_mb": server_rss_mb(),
                    }
                    recorder = Recorder(session, log, index_host)
                    plan = select(steps(), args.only)
                    entries = {}
                    for step in plan:
                        entry = {"id": step.id, "group": step.group, "tool": step.tool}
                        if step.indexed and not opensearch:
                            entry["skipped"] = "needs TUIK_OPENSEARCH_URL"
                        else:
                            entry["cold"] = await recorder.call(step)
                            print(f"cold {step.id:18} {entry['cold']['ms']:9.1f} ms  "
                                  f"{'ok' if entry['cold']['ok'] else 'FAIL'}",
                                  file=sys.stderr, flush=True)  # fmt: skip
                        entries[step.id] = entry
                        report["steps"].append(entry)
                    report["rss_after_cold_mb"] = server_rss_mb()
                    for step in plan:
                        entry = entries[step.id]
                        if not step.repeat or "cold" not in entry:
                            continue
                        samples = [await recorder.call(step) for _ in range(args.repeats)]
                        entry["warm"] = spread([s["ms"] for s in samples]) | {
                            "ok": all(s["ok"] for s in samples),
                            "failure": next((s["failure"] for s in samples if not s["ok"]), None),
                            "requests": {
                                kind: sum(s["requests"][kind] for s in samples)
                                for kind in ("tuik", "index")
                            },
                        }
                    report["rss_after_warm_mb"] = server_rss_mb()
    report["summary"] = summarize(report["steps"])
    return report


def table(report: dict, before: dict | None = None) -> str:
    """Markdown table of the steps; with `before`, warm medians side by side."""
    earlier = {entry["id"]: entry for entry in (before or {}).get("steps", [])}
    head = (
        "| Step | Tool | Check | Cold ms | Warm median ms | Warm max ms | Bytes | Upstream cold |"
    )
    rows = [head + (" Warm before |" if before else ""), "|" + " --- |" * head.count("|")]
    for entry in report["steps"]:
        if "cold" not in entry:
            rows.append(f"| {entry['id']} | {entry['tool']} | skipped | | | | | |")
            continue
        cold, warm = entry["cold"], entry.get("warm", {})
        requests = cold["requests"]
        row = (
            f"| {entry['id']} | {entry['tool']} | {'ok' if passed(entry) else 'FAIL'} | "
            f"{cold['ms']:.0f} | {warm.get('median_ms', '')} | {warm.get('max_ms', '')} | "
            f"{cold['bytes']} | {requests['tuik']} TÜİK, {requests['index']} index |"
        )
        if before:
            old = earlier.get(entry["id"], {}).get("warm", {}).get("median_ms", "")
            row += f" {old} |"
        rows.append(row)
    return "\n".join(rows)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--server", default=str(ROOT / ".venv" / "bin" / "tuik-mcp"))
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument(
        "--only",
        type=lambda value: value.split(","),
        help="Comma-separated groups: discovery,bulletin,csv,excel,pdf",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compare", type=Path, help="An earlier report to show beside this one")
    args = parser.parse_args(argv)
    report = asyncio.run(run(args))
    before = json.loads(args.compare.read_text()) if args.compare else None
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
    print(table(report, before))
    totals = ("startup", "rss_after_cold_mb", "rss_after_warm_mb", "summary")
    print(json.dumps({key: report[key] for key in totals}, ensure_ascii=False, indent=1))
    return 0 if not report["summary"]["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
