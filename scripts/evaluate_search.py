"""Collect search baselines, pool top ten candidates, and score human judgments.

Run with --help. Collection is opt-in; pool and score never access the network.
"""

import argparse
import asyncio
import hashlib
import json
import math
import re
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from tuik_mcp.clients.base import HttpClient
from tuik_mcp.config import Settings
from tuik_mcp.models.dataset import TuikResource
from tuik_mcp.services.catalog import CatalogService
from tuik_mcp.utils.normalization import turkish_casefold, validate_id


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def stable_id(value):
    if re.fullmatch(r"title:[a-z]+:[0-9a-f]{64}", value):
        return value
    validate_id(value)
    if value.startswith(("resource:", "classification:")):
        raise ValueError(f"Session-dependent ID cannot enter the relevance pool: {value}")
    return value


def evaluation_hit(result):
    hit = result.model_dump(
        mode="json",
        include={
            "id",
            "title",
            "description",
            "resource_type",
            "theme",
            "publication_date",
            "source_url",
            "retrieved_at",
        },
    )
    if hit["id"].startswith(("resource:", "classification:")):
        # Non-file portal pages have no reusable tool ID. Use an evaluation-only
        # title/type key, never a hash of a short-lived download URL.
        title = " ".join(turkish_casefold(hit["title"]).split())
        digest = hashlib.sha256(title.encode()).hexdigest()
        hit["id"] = f"title:{hit['resource_type']}:{digest}"
        hit["identity_method"] = "normalized_title_and_type; evaluation_only"
    stable_id(hit["id"])
    return hit


def validate_suite(suite):
    if suite.get("version") != 1:
        raise ValueError("Expected evaluation schema version 1")
    queries = suite["queries"]
    ids = [q["id"] for q in queries]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("Query IDs must be nonempty and unique")
    for query in queries:
        if not all(
            isinstance(query[k], str) and query[k].strip() for k in ("id", "text", "category")
        ):
            raise ValueError("Each query needs id, text and category")
    for qid, docs in suite["judgments"].items():
        if qid not in ids:
            raise ValueError(f"Judgment for unknown query: {qid}")
        for doc_id, judgment in docs.items():
            stable_id(doc_id)
            for field in ("judge_a", "judge_b", "resolved"):
                grade = judgment.get(field)
                if grade is not None and (type(grade) is not int or grade not in (0, 1, 2)):
                    raise ValueError(f"{field} must be null or an integer from 0 to 2")
            if judgment.get("resolved") is not None:
                if judgment.get("judge_a") is None or judgment.get("judge_b") is None:
                    raise ValueError("Resolution requires both independent judgments")
                if not judgment.get("reason", "").strip():
                    raise ValueError("Resolved judgments require a documented reason")
    return suite


def validate_run(run, suite):
    expected = {q["id"]: q["text"] for q in suite["queries"]}
    if run.get("queries") != expected:
        raise ValueError("Run query IDs/text must match the evaluation suite exactly")
    if not run.get("systems"):
        raise ValueError("Run must contain at least one system")
    for system in run["systems"].values():
        if set(system["results"]) != set(expected):
            raise ValueError("Incomplete run: every query must have a successful result list")
        corpus_ids = system.get("corpus_ids")
        if corpus_ids is not None:
            for doc_id in corpus_ids:
                stable_id(doc_id)
        for hits in system["results"].values():
            ids = [stable_id(hit["id"]) for hit in hits]
            if len(ids) != len(set(ids)):
                raise ValueError("Duplicate IDs in a ranking")
            if corpus_ids is not None and not set(ids) <= set(corpus_ids):
                raise ValueError("Ranking includes IDs outside the declared corpus")
            for hit in hits:
                if not all(hit.get(k) for k in ("title", "source_url", "resource_type")):
                    raise ValueError("Hits require title, source_url and resource_type")


def pool(suite, runs):
    suite = validate_suite(json.loads(json.dumps(suite)))
    documents = suite.setdefault("documents", {})
    for run in runs:
        validate_run(run, suite)
        for system in run["systems"].values():
            for qid, hits in system["results"].items():
                judgments = suite["judgments"].setdefault(qid, {})
                for hit in hits[:10]:
                    judgments.setdefault(
                        hit["id"], {"judge_a": None, "judge_b": None, "resolved": None}
                    )
                    documents.setdefault(hit["id"], hit)
    # IDs order candidates consistently without disclosing system or rank.
    suite["documents"] = dict(sorted(documents.items()))
    suite["judgments"] = {
        qid: dict(sorted(docs.items())) for qid, docs in suite["judgments"].items()
    }
    return suite


def judge_sheet(suite, judge):
    return {
        "judge": judge,
        "queries": suite["queries"],
        "documents": suite["documents"],
        "grades": {qid: dict.fromkeys(sorted(docs)) for qid, docs in suite["judgments"].items()},
    }


def merge_judges(suite, sheets):
    suite = json.loads(json.dumps(suite))
    for name, sheet in zip(("a", "b"), sheets, strict=True):
        if sheet["judge"] != name or sheet["queries"] != suite["queries"]:
            raise ValueError("Judge identity and query set must match")
        if set(sheet["grades"]) != set(suite["judgments"]):
            raise ValueError("Judge query IDs must match the pool")
        for qid, grades in sheet["grades"].items():
            if set(grades) != set(suite["judgments"][qid]):
                raise ValueError("Judge document IDs must match the pool")
            for doc_id, grade in grades.items():
                row = suite["judgments"][qid][doc_id]
                if row.get(f"judge_{name}") != grade:
                    row["resolved"] = None
                    row.pop("reason", None)
                row[f"judge_{name}"] = grade
    return validate_suite(suite)


def final_grade(judgment):
    if judgment.get("resolved") is not None:
        return judgment["resolved"]
    a, b = judgment.get("judge_a"), judgment.get("judge_b")
    return a if a is not None and a == b else None


def kappa(judgments):
    pairs = [(j.get("judge_a"), j.get("judge_b")) for j in judgments]
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    if not pairs:
        return {"paired": 0, "cohen_kappa": None, "agreement": None}
    n = len(pairs)
    agreement = sum(a == b for a, b in pairs) / n
    ac, bc = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    chance = sum(ac[g] * bc[g] for g in (0, 1, 2)) / n**2
    return {
        "paired": n,
        "agreement": agreement,
        "cohen_kappa": (agreement - chance) / (1 - chance) if chance < 1 else None,
    }


def query_metrics(hits, judgments):
    grades = {key: final_grade(value) for key, value in judgments.items()}
    top = hits[:10]
    gains = [grades.get(hit["id"]) or 0 for hit in top]
    ideal = sorted((g for g in grades.values() if g is not None), reverse=True)[:10]

    def dcg(values):
        return sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(values))

    relevant = sum(g > 0 for g in grades.values() if g is not None)
    return {
        "ndcg_at_10": dcg(gains) / dcg(ideal) if dcg(ideal) else 0.0,
        "mrr": next((1 / (i + 1) for i, g in enumerate(gains) if g > 0), 0.0),
        "recall_at_10": sum(g > 0 for g in gains) / relevant if relevant else 0.0,
        "returned": len(top),
        "unjudged": sum(grades.get(hit["id"]) is None for hit in top),
        "no_judged_relevant": relevant == 0,
    }


def aggregate(rows):
    returned = sum(r["returned"] for r in rows)
    return {
        "queries": len(rows),
        **{k: sum(r[k] for r in rows) / len(rows) for k in ("ndcg_at_10", "mrr", "recall_at_10")},
        "returned": returned,
        "unjudged_share": sum(r["unjudged"] for r in rows) / returned if returned else None,
        "queries_without_judged_relevant": sum(r["no_judged_relevant"] for r in rows),
    }


def score(suite, runs):
    validate_suite(suite)
    pooled = pool(suite, runs)
    all_judgments = [j for docs in pooled["judgments"].values() for j in docs.values()]
    completed = sum(final_grade(j) is not None for j in all_judgments)
    report = {
        "status": "complete"
        if all_judgments and completed == len(all_judgments)
        else "pending_judgments",
        "judgments": len(all_judgments),
        "completed": completed,
        "agreement": kappa(all_judgments),
        "systems": {},
    }
    documents = pooled["documents"]
    for run in runs:
        for name, system in run["systems"].items():
            if name in report["systems"]:
                raise ValueError(f"Duplicate system name: {name}; use distinct names for variants")
            eligible = set(system["corpus_ids"]) if system.get("corpus_ids") is not None else None
            rows, categories, kinds = [], {}, {}
            for query in suite["queries"]:
                qid = query["id"]
                judgments = {
                    key: value
                    for key, value in pooled["judgments"][qid].items()
                    if eligible is None or key in eligible
                }
                hits = system["results"][qid][:10]
                row = query_metrics(hits, judgments)
                rows.append(row)
                categories.setdefault(query["category"], []).append(row)
                # Filter the original top ten; do not backfill ranks from lower results.
                for kind in sorted({doc["resource_type"] for doc in documents.values()}):
                    kind_judgments = {
                        key: value
                        for key, value in judgments.items()
                        if documents[key]["resource_type"] == kind
                    }
                    kinds.setdefault(kind, []).append(
                        query_metrics(
                            [hit for hit in hits if hit["resource_type"] == kind], kind_judgments
                        )
                    )
            report["systems"][name] = {
                "corpus": system["corpus"],
                "overall": aggregate(rows),
                "by_category": {key: aggregate(value) for key, value in categories.items()},
                "by_resource_type": {key: aggregate(value) for key, value in kinds.items()},
            }
    return report


async def collect(suite, output, resume=False):
    validate_suite(suite)
    report = {
        "version": 1,
        "collected_at": datetime.now(UTC).isoformat(),
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "queries": {q["id"]: q["text"] for q in suite["queries"]},
        "systems": {
            "portal": {"corpus": "portal_all_types_non_archive", "results": {}},
            "literal": {"corpus": "downloadable_dataflows", "results": {}},
        },
        "errors": {},
    }
    if resume:
        previous = read_json(output)
        if previous["queries"] != report["queries"]:
            raise ValueError("Cannot resume a different query set")
        report = previous
    http = HttpClient(Settings.from_env())
    try:
        service = CatalogService(http)
        # Freeze the exact catalog used by all literal queries for reproducibility.
        if resume:
            datasets = [TuikResource.model_validate(d) for d in report["catalog"]]
            service.cache.set(
                "datasets",
                (datetime.fromisoformat(report["catalog_retrieved_at"]), datasets),
                3600,
            )
        retrieved_at, datasets = await service._datasets()
        report["catalog_retrieved_at"] = retrieved_at.isoformat()
        catalog = [
            d.model_dump(
                mode="json",
                include={
                    "id",
                    "title",
                    "description",
                    "theme",
                    "resource_type",
                    "source_url",
                    "available_formats",
                    "retrieved_at",
                },
            )
            for d in datasets
        ]
        report["catalog_sha256"] = hashlib.sha256(
            json.dumps(catalog, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        report["catalog"] = catalog
        report["systems"]["literal"]["corpus_ids"] = [
            d.id for d in datasets if "csv" in d.available_formats
        ]
        for query in suite["queries"]:
            for name, search in (("portal", service.search), ("literal", service.search_datasets)):
                if query["id"] in report["systems"][name]["results"]:
                    continue
                try:
                    response = await search(query["text"], limit=10)
                    hits, seen = [], set()
                    for result in response.results:
                        hit = evaluation_hit(result)
                        if hit["id"] not in seen:
                            hits.append(hit)
                            seen.add(hit["id"])
                    report["systems"][name]["results"][query["id"]] = hits
                    report["errors"].pop(f"{name}:{query['id']}", None)
                except Exception as exc:
                    # Never silently turn an outage or unstable ID into an empty ranking.
                    report["errors"][f"{name}:{query['id']}"] = {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    }
            write_json(output, report)
    finally:
        await http.close()
        report["last_attempt_at"] = datetime.now(UTC).isoformat()
        report["collector_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        write_json(output, report)
    return 1 if report["errors"] else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("collect", "pool", "judge", "merge", "score"))
    parser.add_argument("--suite", type=Path, default=Path("tests/fixtures/search_eval.json"))
    parser.add_argument("--runs", type=Path, nargs="*", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--judge", choices=("a", "b"))
    parser.add_argument("--judge-a", type=Path)
    parser.add_argument("--judge-b", type=Path)
    parser.add_argument(
        "--resume", action="store_true", help="Retry only missing collection results"
    )
    args = parser.parse_args(argv)
    suite = validate_suite(read_json(args.suite))
    if args.command == "collect":
        return asyncio.run(collect(suite, args.output, args.resume))
    if args.command == "judge":
        if not args.judge:
            parser.error("judge requires --judge a or b")
        write_json(args.output, judge_sheet(suite, args.judge))
        return 0
    if args.command == "merge":
        if not args.judge_a or not args.judge_b:
            parser.error("merge requires --judge-a and --judge-b")
        write_json(
            args.output, merge_judges(suite, [read_json(args.judge_a), read_json(args.judge_b)])
        )
        return 0
    if not args.runs:
        parser.error("pool and score require --runs")
    runs = [read_json(path) for path in args.runs]
    write_json(args.output, pool(suite, runs) if args.command == "pool" else score(suite, runs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
