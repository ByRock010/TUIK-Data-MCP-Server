"""Compare one model with the TÜİK MCP, with web tools only and with no tools.

`run` asks every prompt under each condition through headless Claude Code
(`claude -p`, stream-json) and keeps the full event stream per run. `summarize`
extracts tool calls, tool errors, cost, duration, the final answer and which of
the answer's numbers occur verbatim in that run's tool output. Grading against
ground truth is a separate, documented step (docs/verification/mcp-benchmark-*.md).

    python scripts/mcp_benchmark.py run --prompts prompts.json --out bench/
    python scripts/mcp_benchmark.py summarize --out bench/ --output summary.json
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = "claude-opus-5-5"
SYSTEM = (
    "Kullanıcı bir veri analisti. Türkçe yanıt ver. Sayısal değerleri dönemi ve "
    "kaynağıyla birlikte ver. Doğrulayamadığın değerleri açıkça belirt; değer uydurma. "
    "Bugünün tarihi {today}."
)
CONDITIONS = ("mcp", "web", "closed")


def condition_args(condition: str, out: Path) -> list[str]:
    empty = out / "empty-mcp.json"
    if condition == "mcp":
        return ["--mcp-config", str(out / "tuik-mcp.json"), "--tools", "",
                "--allowedTools", "mcp__tuik__*"]  # fmt: skip
    if condition == "web":
        return ["--mcp-config", str(empty), "--tools", "WebSearch,WebFetch",
                "--allowedTools", "WebSearch,WebFetch"]  # fmt: skip
    return ["--mcp-config", str(empty), "--tools", ""]


def write_configs(out: Path, server: str, opensearch_url: str | None) -> None:
    env = {"TUIK_OPENSEARCH_URL": opensearch_url} if opensearch_url else {}
    config = {"mcpServers": {"tuik": {"command": server, "args": [], "env": env}}}
    (out / "tuik-mcp.json").write_text(json.dumps(config, indent=2))
    (out / "empty-mcp.json").write_text(json.dumps({"mcpServers": {}}))


def run_one(args, condition: str, prompt: dict) -> dict:
    out = args.out
    target = out / "runs" / f"{condition}-{prompt['id']}.jsonl"
    command = ["claude", "-p", prompt["text"], "--model", args.model, "--strict-mcp-config",
               "--no-session-persistence", "--output-format", "stream-json", "--verbose",
               "--append-system-prompt", SYSTEM.format(today=args.today),
               *condition_args(condition, out)]  # fmt: skip
    work = out / "work"  # Empty: no project files, CLAUDE.md or memory to lean on.
    start = time.monotonic()
    try:
        with target.open("w") as stream:
            status = subprocess.run(
                command, cwd=work, stdin=subprocess.DEVNULL, stdout=stream,
                stderr=subprocess.DEVNULL, timeout=args.timeout, check=False,
            ).returncode  # fmt: skip
    except subprocess.TimeoutExpired:
        status = "timeout"
    meta = {"condition": condition, "id": prompt["id"], "status": status,
            "wall_seconds": round(time.monotonic() - start, 1)}  # fmt: skip
    print(json.dumps(meta), flush=True)
    return meta


def run(args) -> int:
    # Absolute: the child runs in out/work, where a relative config path would not resolve.
    out = args.out = Path(args.out).resolve()
    (out / "runs").mkdir(parents=True, exist_ok=True)
    (out / "work").mkdir(exist_ok=True)
    write_configs(out, args.server, args.opensearch_url)
    prompts = json.loads(Path(args.prompts).read_text())
    jobs = [(c, p) for c in args.conditions for p in prompts]
    # Each MCP run starts its own server with its own TÜİK pacing: keep them few.
    with ThreadPoolExecutor(args.mcp_workers) as mcp, ThreadPoolExecutor(args.workers) as other:
        futures = [(mcp if c == "mcp" else other).submit(run_one, args, c, p) for c, p in jobs]
        metas = [future.result() for future in futures]
    (out / "run-meta.json").write_text(json.dumps(metas, indent=2))
    failed = [f"{meta['condition']}-{meta['id']}" for meta in metas if meta["status"] != 0]
    if failed:
        print(f"{len(failed)} run(s) did not exit cleanly: {', '.join(failed)}", file=sys.stderr)
    return 1 if failed else 0


def number_forms(token: str) -> set[str]:
    """Spellings a source may use for one number: 8,4 / 8.4; 193.793 / 193 bin 793."""
    forms = {token, token.replace(",", "."), token.replace(".", ",")}
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", token):
        plain = token.replace(".", "")
        head, tail = plain[:-3], plain[-3:]
        forms |= {plain, f"{head} bin {tail}", f"{head} {tail}", f"{head} milyar {tail}",
                  f"{head},{tail}"}  # fmt: skip
    if match := re.fullmatch(r"(\d+),(\d{1,2})", token):
        forms |= {f"{match[1]} milyar {match[2]}", f"{match[1]} trilyon {match[2]}"}
    return forms


def occurs(form: str, evidence: str) -> bool:
    """Whether the evidence holds `form` as a whole number, not inside a longer one.

    "8,4" is not evidence in "18,45", nor "193.793" in "1.193.793".
    """
    pattern = rf"(?<!\d)(?<!\d[.,]){re.escape(form)}(?!\d)(?![.,]\d)"
    return re.search(pattern, evidence) is not None


DATE = re.compile(r"\b\d{1,2}[./]\d{1,2}[./]\d{4}\b")


def traceable(answer: str, evidence: str) -> dict:
    """Decimal or 4+ digit numbers in the answer and which occur in the tool output.

    Years and dates are not value claims. A number must occur whole: a match inside
    a longer number does not count. Numbers the model computed or rounded are reported
    as missing and need a manual look.
    """
    answer = DATE.sub(" ", answer)
    tokens = set(re.findall(r"\d+[.,]\d+(?:\.\d{3})*|\d{4,}", answer))
    tokens = {t for t in tokens if not re.fullmatch(r"(19|20)\d\d", t)}
    found = {t for t in tokens if any(occurs(form, evidence) for form in number_forms(t))}
    return {"total": len(tokens), "found": len(found), "missing": sorted(tokens - found)}


def summarize_run(path: Path) -> dict:
    calls, results, final = [], {}, {}
    for line in path.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "assistant":
            for block in event["message"].get("content", []):
                if block.get("type") in ("tool_use", "server_tool_use"):
                    calls.append({"id": block.get("id"), "name": block["name"],
                                  "input": block.get("input")})  # fmt: skip
        elif event.get("type") == "user":
            content = event["message"].get("content")
            for block in content if isinstance(content, list) else []:
                if block.get("type") == "tool_result":
                    text = block.get("content")
                    if isinstance(text, list):
                        text = " ".join(c.get("text", "") for c in text if isinstance(c, dict))
                    text = text or ""
                    results[block.get("tool_use_id")] = {
                        "error": bool(block.get("is_error")) or '"status":"error"' in text,
                        "text": text,
                    }
        elif event.get("type") == "result":
            final = event
    for call in calls:
        call["error"] = results.get(call["id"], {}).get("error", False)
    answer = final.get("result") or ""
    evidence = " ".join(r["text"] for r in results.values())
    condition, prompt_id = path.stem.split("-", 1)
    return {
        "condition": condition,
        "id": prompt_id,
        "num_turns": final.get("num_turns"),
        "duration_s": round((final.get("duration_ms") or 0) / 1000, 1),
        "cost_usd": final.get("total_cost_usd"),
        "stop_reason": final.get("stop_reason"),
        "is_error": final.get("is_error"),
        "tool_calls": calls,
        "tool_errors": sum(call["error"] for call in calls),
        "numbers": traceable(answer, evidence),
        "answer": answer,
    }


def summarize(args) -> int:
    runs = [summarize_run(path) for path in sorted((Path(args.out) / "runs").glob("*.jsonl"))]
    home = os.path.expanduser("~")
    text = json.dumps(runs, ensure_ascii=False, indent=1).replace(home, "~")
    Path(args.output).write_text(text)
    for r in runs:
        print(f"{r['condition']:6} {r['id']} turns={r['num_turns']} {r['duration_s']}s "
              f"${r['cost_usd'] or 0:.3f} errors={r['tool_errors']} calls={len(r['tool_calls'])} "
              f"traced={r['numbers']['found']}/{r['numbers']['total']}")  # fmt: skip
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="Ask every prompt under each condition")
    run_parser.add_argument("--prompts", required=True)
    run_parser.add_argument("--out", required=True)
    run_parser.add_argument("--model", default=MODEL)
    run_parser.add_argument("--today", default=time.strftime("%d.%m.%Y"))
    run_parser.add_argument("--server", default=str(ROOT / ".venv" / "bin" / "tuik-mcp"))
    run_parser.add_argument("--opensearch-url", default=os.environ.get("TUIK_OPENSEARCH_URL"))
    run_parser.add_argument("--conditions", type=lambda v: v.split(","), default=list(CONDITIONS))
    run_parser.add_argument("--workers", type=int, default=6)
    run_parser.add_argument("--mcp-workers", type=int, default=2)
    run_parser.add_argument("--timeout", type=int, default=1200)
    summary_parser = sub.add_parser("summarize", help="Extract tool calls, costs and answers")
    summary_parser.add_argument("--out", required=True)
    summary_parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.command == "run":
        unknown = set(args.conditions) - set(CONDITIONS)
        if unknown:
            parser.error(f"unknown condition(s): {sorted(unknown)}")
        return run(args)
    return summarize(args)


if __name__ == "__main__":
    sys.exit(main())
