"""Collect search baselines, pool top ten candidates, and score human judgments.

Run with --help. Collection is opt-in; pool and score never access the network.
"""

import argparse
import asyncio
import csv
import gzip
import hashlib
import json
import math
import re
import subprocess
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from tuik_mcp.clients.base import HttpClient
from tuik_mcp.config import Settings
from tuik_mcp.models.dataset import TuikResource
from tuik_mcp.search.backend import OpenSearchBackend, SearchFilters, build_query
from tuik_mcp.search.crawl import atomic_write
from tuik_mcp.search.documents import IndexDocument
from tuik_mcp.search.indexer import IndexerError, OpenSearchStore, prepare
from tuik_mcp.search.schema import index_definition
from tuik_mcp.services.catalog import CatalogService
from tuik_mcp.utils.normalization import turkish_casefold, validate_id


def read_json(path):
    path = Path(path)
    if path.suffix == ".gz":
        return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, data):
    path = Path(path)
    payload = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if path.suffix == ".gz":
        payload = gzip.compress(payload, mtime=0)
    atomic_write(path, payload)


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
    if "status" in run and run["status"] != "ok":
        raise ValueError("Incomplete run: collection status is not ok")
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


def validate_pool_documents(suite):
    documents = suite.get("documents", {})
    for qid, judgments in suite["judgments"].items():
        for doc_id in judgments:
            if doc_id not in documents:
                raise ValueError(f"Missing document metadata for {qid}: {doc_id}")
    for doc_id, document in documents.items():
        if (
            not isinstance(document, dict)
            or document.get("id") != doc_id
            or not all(
                isinstance(document.get(key), str) and document[key].strip()
                for key in ("title", "resource_type", "source_url")
            )
        ):
            raise ValueError(f"Invalid document metadata for {doc_id}")
        stable_id(doc_id)
    return documents


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
    # New runs may restore missing metadata; validate only after pooling all hits.
    validate_pool_documents(suite)
    # IDs order candidates consistently without disclosing system or rank.
    suite["documents"] = dict(sorted(documents.items()))
    suite["judgments"] = {
        qid: dict(sorted(docs.items())) for qid, docs in suite["judgments"].items()
    }
    return suite


def judge_sheet(suite, judge):
    validate_suite(suite)
    return {
        "judge": judge,
        "queries": suite["queries"],
        "documents": validate_pool_documents(suite),
        "grades": {qid: dict.fromkeys(sorted(docs)) for qid, docs in suite["judgments"].items()},
    }


JUDGE_CSV_FIELDS = (
    "judge",
    "query_id",
    "query",
    "category",
    "document_id",
    "title",
    "description",
    "resource_type",
    "source_url",
    "grade",
)


def spreadsheet_text(value):
    """Keep source strings as text when opened in a spreadsheet application."""
    text = str(value or "")
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def judge_csv_rows(suite, judge):
    sheet = judge_sheet(suite, judge)
    for query in sheet["queries"]:
        for doc_id in sheet["grades"].get(query["id"], {}):
            document = sheet["documents"][doc_id]
            yield {
                "judge": judge,
                "query_id": query["id"],
                "query": spreadsheet_text(query["text"]),
                "category": query["category"],
                "document_id": doc_id,
                **{
                    key: spreadsheet_text(document.get(key))
                    for key in ("title", "description", "resource_type", "source_url")
                },
                "grade": "",
            }


def write_judge_csv(path, suite, judge):
    # BOM allows Excel to recognize Turkish text; newline="" preserves CSV quoting.
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=JUDGE_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(judge_csv_rows(suite, judge))


def read_judge_sheet(path, suite, judge):
    if Path(path).suffix.lower() != ".csv":
        return read_json(path)
    sheet = judge_sheet(suite, judge)
    expected = {(row["query_id"], row["document_id"]): row for row in judge_csv_rows(suite, judge)}
    seen = set()
    with Path(path).open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != list(JUDGE_CSV_FIELDS):
            raise ValueError("Judge CSV columns must match the exported sheet")
        for row in reader:
            pair = row["query_id"], row["document_id"]
            if pair in seen or pair not in expected:
                raise ValueError(f"Duplicate or unknown judge CSV pair: {pair}")
            if set(row) != set(JUDGE_CSV_FIELDS) or any(
                row[key] != expected[pair][key] for key in JUDGE_CSV_FIELDS if key != "grade"
            ):
                raise ValueError(f"Judge CSV context changed for {pair}; edit only grade")
            grade = (row["grade"] or "").strip()
            if grade not in ("", "0", "1", "2"):
                raise ValueError(f"Judge CSV grade must be blank, 0, 1 or 2 for {pair}")
            sheet["grades"][pair[0]][pair[1]] = int(grade) if grade else None
            seen.add(pair)
    if seen != set(expected):
        raise ValueError("Judge CSV document IDs must match the entire pool; rows are missing")
    return sheet


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
    return graded_metrics(hits, {key: final_grade(value) for key, value in judgments.items()})


def graded_metrics(hits, grades):
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
    grades = {
        qid: {doc_id: final_grade(judgment) for doc_id, judgment in judgments.items()}
        for qid, judgments in pooled["judgments"].items()
    }
    report["systems"] = system_metrics(pooled, runs, grades)
    return report


def system_metrics(pooled, runs, grades):
    systems = {}
    documents = pooled["documents"]
    for run in runs:
        for name, system in run["systems"].items():
            if name in systems:
                raise ValueError(f"Duplicate system name: {name}; use distinct names for variants")
            eligible = set(system["corpus_ids"]) if system.get("corpus_ids") is not None else None
            rows, categories, kinds = [], {}, {}
            for query in pooled["queries"]:
                qid = query["id"]
                judgments = {
                    key: value
                    for key, value in grades[qid].items()
                    if eligible is None or key in eligible
                }
                hits = system["results"][qid][:10]
                row = graded_metrics(hits, judgments)
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
                        graded_metrics(
                            [hit for hit in hits if hit["resource_type"] == kind], kind_judgments
                        )
                    )
            systems[name] = {
                "corpus": system["corpus"],
                "overall": aggregate(rows),
                "by_category": {key: aggregate(value) for key, value in categories.items()},
                "by_resource_type": {key: aggregate(value) for key, value in kinds.items()},
            }
    return systems


def pool_fingerprint(suite):
    """Bind assessments to the exact queries, candidates and stored source evidence."""
    evidence = {
        "queries": suite["queries"],
        "documents": validate_pool_documents(suite),
        "pairs": {qid: sorted(docs) for qid, docs in suite["judgments"].items()},
    }
    return hashlib.sha256(
        json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def validate_ai_review(pooled, assessment, review, evidence):
    """Validate one assessor's second pass, preserving the original pass and sources."""
    if (
        review.get("review_type") != "single_ai_sequential_source_review"
        or review.get("human_assessors") != 0
        or review.get("independent_assessors") is not False
    ):
        raise ValueError("Review must explicitly identify one non-independent AI assessor")
    digest = hashlib.sha256(
        json.dumps(assessment, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    if review.get("first_assessment_sha256") != digest:
        raise ValueError("Review does not match the original AI assessment")
    if evidence.get("status") not in ("ok", "partial"):
        raise ValueError("Source collection must have finished before review")
    pool_file_hash = hashlib.sha256(
        (json.dumps(pooled, ensure_ascii=False, indent=2) + "\n").encode()
    ).hexdigest()
    if evidence.get("pool_file_sha256") != pool_file_hash:
        raise ValueError("Source collection must use the exact frozen pool")
    if set(evidence.get("searches", {})) != {query["id"] for query in pooled["queries"]}:
        raise ValueError("Every frozen query must have a live MCP replay")
    for query in pooled["queries"]:
        index = evidence["searches"][query["id"]]
        calls = evidence.get("calls", [])
        if type(index) is not int or not 0 <= index < len(calls):
            raise ValueError("Invalid live query reference")
        call = calls[index]
        if (
            call.get("tool") != "search_tuik"
            or call.get("arguments") != {"query": query["text"], "limit": 10, "live": True}
            or call.get("outcome") != "ok"
            or call.get("response", {}).get("backend") != "portal"
        ):
            raise ValueError("Live query reference must replay the exact query through the portal")
    digest = hashlib.sha256(
        json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    if review.get("evidence_sha256") != digest:
        raise ValueError("Review does not match the recorded live source evidence")
    if set(review.get("source_checks", {})) != set(pooled["documents"]):
        raise ValueError("Every pooled document requires a documented source check")
    if set(review.get("query_notes", {})) != set(pooled["judgments"]):
        raise ValueError("Every query requires a second-pass rationale")
    for note in review["query_notes"].values():
        if not isinstance(note, str) or not note.strip():
            raise ValueError("Second-pass query rationales must be nonempty")
    counts = Counter()
    for doc_id, check in review["source_checks"].items():
        if not isinstance(check.get("reason"), str) or not check["reason"].strip():
            raise ValueError(f"Source check requires a reason: {doc_id}")
        method = check.get("method")
        if method == "live_metadata":
            index = check.get("call")
            calls = evidence.get("calls", [])
            if type(index) is not int or not 0 <= index < len(calls):
                raise ValueError(f"Invalid live source reference: {doc_id}")
            call = calls[index]
            if (
                call.get("tool") != "get_tuik_resource"
                or call.get("arguments", {}).get("resource_id") != doc_id
                or call.get("outcome") != "ok"
                or call.get("response", {}).get("id") != doc_id
            ):
                raise ValueError(f"Live source reference must resolve the exact resource: {doc_id}")
        elif method != "archived_metadata":
            raise ValueError(f"Unknown source-review method: {method}")
        counts[method] += 1
    changed = {
        (qid, doc_id)
        for qid, values in review["grades"].items()
        for doc_id, grade in values.items()
        if grade != assessment["grades"][qid][doc_id]
    }
    documented = set()
    for change in review.get("changes", []):
        qid, doc_id = change["query_id"], change["document_id"]
        pair = qid, doc_id
        if (
            pair not in changed
            or pair in documented
            or change.get("before") != assessment["grades"][qid][doc_id]
            or change.get("after") != review["grades"][qid][doc_id]
            or not isinstance(change.get("reason"), str)
            or not change["reason"].strip()
        ):
            raise ValueError("Every revised grade requires its original value and a unique reason")
        documented.add(pair)
    if documented != changed:
        raise ValueError("Every revised grade must be documented")
    return dict(counts), len(changed)


def score_ai(suite, runs, assessment, review=None, evidence=None):
    """Score one explicitly identified AI assessor without manufacturing judge pairs."""
    pooled = pool(suite, runs)
    if assessment.get("assessment_type") != "single_model_ai_preliminary":
        raise ValueError("Expected a single_model_ai_preliminary assessment")
    if assessment.get("pool_sha256") != pool_fingerprint(pooled):
        raise ValueError("AI assessment does not match the pooled source evidence")
    for field in ("assessor", "assessed_at", "evidence_scope", "limitations"):
        if not isinstance(assessment.get(field), str) or not assessment[field].strip():
            raise ValueError(f"AI assessment requires {field}")
    grades = assessment.get("grades", {})
    if set(grades) != set(pooled["judgments"]):
        raise ValueError("AI assessment query IDs must match the pool")
    for qid, judgments in pooled["judgments"].items():
        if set(grades[qid]) != set(judgments):
            raise ValueError(f"AI assessment document IDs must match the pool for {qid}")
        if any(type(grade) is not int or grade not in (0, 1, 2) for grade in grades[qid].values()):
            raise ValueError("AI grades must all be integers from 0 to 2")
    report = {
        "status": "ai_preliminary",
        "assessment_type": "single_model_ai_preliminary",
        "release_gate_satisfied": False,
        "pool_sha256": assessment["pool_sha256"],
        **{
            field: assessment[field]
            for field in ("assessor", "assessed_at", "evidence_scope", "limitations")
        },
        "judgments": sum(map(len, grades.values())),
        "agreement": {
            "paired": 0,
            "cohen_kappa": None,
            "agreement": None,
            "reason": "Single AI assessor; no independent human judge pairs",
        },
        "systems": system_metrics(pooled, runs, grades),
    }
    if review is not None:
        # Apply the same completeness, scope and grade checks to the second pass.
        second = score_ai(suite, runs, review)
        counts, changed = validate_ai_review(pooled, assessment, review, evidence or {})
        report.update(
            status="ai_reviewed",
            review_complete=True,
            review_type=review["review_type"],
            source_checks=counts,
            revised_pairs=changed,
            systems=second["systems"],
            reviewed_assessor=review["assessor"],
            reviewed_at=review["assessed_at"],
            reviewed_evidence_scope=review["evidence_scope"],
            reviewed_limitations=review["limitations"],
        )
    return report


def corpus_fingerprint(documents):
    return hashlib.sha256(
        json.dumps(
            sorted((d.model_dump(mode="json") for d in documents), key=lambda d: d["id"]),
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()


def frozen_catalog(documents):
    """Retain the literal backend's fields and CSV eligibility from the shared crawl."""
    prepare(documents, datetime.now(UTC))  # Reject empty/duplicate corpora before network I/O.
    if any(d.kind not in ("dataset", "bulletin", "metadata") for d in documents):
        raise ValueError("Relevance comparison requires a metadata-only corpus")
    datasets = [
        TuikResource(
            id=d.id,
            title=d.title,
            description=d.description,
            resource_type="dataset",
            source_url=d.source_url,
            theme=d.theme[0] if d.theme else None,
            available_formats=d.formats,
            retrieved_at=d.retrieved_at,
        )
        for d in documents
        if d.kind == "dataset"
    ]
    if not datasets:
        raise ValueError("Frozen baseline requires dataflow documents")
    return max(d.retrieved_at for d in datasets), datasets


async def collect(suite, output, resume=False, *, documents=None):
    validate_suite(suite)
    snapshot = frozen_catalog(documents) if documents is not None else None
    report = {
        "version": 1,
        "collected_at": datetime.now(UTC).isoformat(),
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "collector_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "queries": {q["id"]: q["text"] for q in suite["queries"]},
        "systems": {
            "portal": {"corpus": "portal_all_types_non_archive", "results": {}},
            "literal": {"corpus": "downloadable_dataflows", "results": {}},
        },
        "errors": {},
    }
    if documents is not None:
        report["corpus_sha256"] = corpus_fingerprint(documents)
        report["systems"]["literal"]["corpus"] = "downloadable_dataflows_from_frozen_metadata"
    if resume:
        previous = read_json(output)
        if previous["queries"] != report["queries"]:
            raise ValueError("Cannot resume a different query set")
        for field in ("revision", "collector_sha256"):
            if previous.get(field) != report[field]:
                raise ValueError(
                    f"Cannot resume: {field} differs or is missing; "
                    "use the original code or collect a new run in a separate output file"
                )
        if previous.get("corpus_sha256") != report.get("corpus_sha256"):
            raise ValueError("Cannot resume: frozen corpus differs or is missing")
        report = previous
    # A baseline must remain literal even in an index-enabled developer shell.
    http = HttpClient(Settings.from_env().model_copy(update={"opensearch_url": None}))
    try:
        service = CatalogService(http)
        # Freeze the exact catalog used by all literal queries for reproducibility.
        if resume:
            datasets = [TuikResource.model_validate(d) for d in report["catalog"]]
            snapshot = (datetime.fromisoformat(report["catalog_retrieved_at"]), datasets)
        if snapshot is not None:
            service.cache.set("datasets", snapshot, 3600)
        retrieved_at, datasets = await service._datasets()
        snapshot = (retrieved_at, datasets)
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
                    # Refresh TTL without refetching, including after slow portal queries.
                    service.cache.set("datasets", snapshot, 3600)
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
        write_json(output, report)
    return 1 if report["errors"] else 0


INDEX_VARIANTS = ("builtin_turkish", "multi_field", "multi_field_synonyms")


def variant_definition(variant):
    """Keep the corpus and mapping fixed; ablate only the text analysis."""
    if variant not in INDEX_VARIANTS:
        raise ValueError(f"Unknown evaluation variant: {variant}")
    definition = index_definition()
    properties = definition["mappings"]["properties"]
    for name in ("title", "description", "body"):
        field = properties[name]
        if variant == "builtin_turkish":
            field.update(analyzer="turkish", search_analyzer="turkish")
            if name == "title":
                field["fields"].pop("ascii")
        elif variant == "multi_field":
            field["search_analyzer"] = field["analyzer"]
            if name == "title":
                ascii_field = field["fields"]["ascii"]
                ascii_field["search_analyzer"] = ascii_field["analyzer"]
    return definition


def variant_query(variant, text):
    if variant not in INDEX_VARIANTS:
        raise ValueError(f"Unknown evaluation variant: {variant}")
    body = deepcopy(build_query(text, SearchFilters(), 10))
    if variant == "builtin_turkish":
        exact, fuzzy = body["query"]["bool"]["should"]
        exact["multi_match"]["fields"].remove("title.ascii")
        fuzzy["multi_match"]["fields"] = ["title", "description", "body"]
        body["highlight"]["fields"].pop("title.ascii")
    # Match the production ordering, with an explicit final key for tied scores.
    body["sort"] = [{"_score": "desc"}, {"id": "asc"}]
    return body


def load_documents(path):
    """Read the crawler's full JSONL, not its redacted diagnostic summary."""
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as source:
        return [IndexDocument.model_validate_json(line) for line in source if line.strip()]


async def collect_index(suite, documents, output, url, *, store=None):
    """Collect comparable top tens without a live crawl or production alias writes."""
    validate_suite(suite)
    stamp = datetime.now(UTC)
    documents = list(documents)
    payloads, kinds = prepare(documents, stamp)
    originals = {document.id: document.model_dump(mode="json") for document in documents}
    corpus_ids = sorted(document["id"] for document in payloads)
    report = {
        "version": 1,
        "status": "running",
        "collected_at": stamp.isoformat(),
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "collector_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "corpus_sha256": corpus_fingerprint(documents),
        "counts": dict(kinds),
        "queries": {q["id"]: q["text"] for q in suite["queries"]},
        "systems": {},
        "cleanup": {},
        "errors": {},
    }
    own_store = store is None
    store = store or OpenSearchStore(url)
    try:
        report["engine"] = (await store._request("GET", "")).json()["version"]
        for variant in INDEX_VARIANTS:
            name = "tuik-evaluation-" + uuid4().hex
            definition = variant_definition(variant)
            system = report["systems"][variant] = {
                "corpus": "frozen_metadata_snapshot",
                "corpus_ids": corpus_ids,
                "index": name,
                "definition": definition,
                "query_template": variant_query(variant, "__QUERY__"),
                "results": {},
                "scores": {},
            }
            created = False
            try:
                await store._request("PUT", name, json=definition)
                created = True
                if failures := await store.bulk(name, payloads):
                    raise IndexerError("Evaluation documents were rejected", failures)
                await store.refresh(name)
                if await store.count(name) != len(payloads):
                    raise IndexerError("Evaluation index count does not match the frozen corpus")
                for query in suite["queries"]:
                    response = await store.query(name, variant_query(variant, query["text"]))
                    if response.get("timed_out") or response.get("_shards", {}).get("failed"):
                        raise IndexerError("Incomplete evaluation search response")
                    hits = response["hits"]
                    if hits["total"].get("relation", "eq") != "eq":
                        raise IndexerError("Evaluation search returned only a lower-bound total")
                    system["results"][query["id"]] = [
                        {
                            "id": stable_id(hit.id),
                            "title": hit.title,
                            "resource_type": hit.kind,
                            "source_url": hit.source_url,
                            "description": originals[hit.id].get("description"),
                            "theme": hit.theme,
                            "publication_date": hit.publication_date,
                        }
                        for hit in map(OpenSearchBackend.hit, hits["hits"])
                    ]
                    system["scores"][query["id"]] = [hit.get("_score") for hit in hits["hits"]]
                    write_json(output, report)
            finally:
                if created:
                    try:
                        await store.delete(name)
                        report["cleanup"][name] = "deleted"
                    except Exception as exc:
                        report["cleanup"][name] = str(exc)
                        raise
        report["status"] = "ok"
        validate_run(report, suite)
    except Exception as exc:
        report["status"] = "error"
        report["errors"]["collection"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        try:
            if own_store:
                await store.close()
        finally:
            write_json(output, report)
    return 0 if report["status"] == "ok" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("collect", "collect-index", "pool", "judge", "merge", "score", "score-ai"),
    )
    parser.add_argument("--suite", type=Path, default=Path("tests/fixtures/search_eval.json"))
    parser.add_argument("--runs", type=Path, nargs="*", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--judge", choices=("a", "b"))
    parser.add_argument("--judge-a", type=Path)
    parser.add_argument("--judge-b", type=Path)
    parser.add_argument(
        "--assessment", type=Path, help="Explicitly identified single-AI assessment"
    )
    parser.add_argument("--review", type=Path, help="Sequential review by the same AI assessor")
    parser.add_argument("--evidence", type=Path, help="Live MCP source-review collection")
    parser.add_argument(
        "--require-reviewed",
        action="store_true",
        help="Fail AI scoring until source review is complete",
    )
    parser.add_argument(
        "--format", choices=("json", "csv"), default="json", help="Judge export format"
    )
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Fail scoring if human judgments are pending",
    )
    parser.add_argument("--documents", type=Path, help="Frozen crawler IndexDocument JSONL")
    parser.add_argument("--url", help="Disposable OpenSearch cluster for collect-index")
    parser.add_argument(
        "--resume", action="store_true", help="Retry only missing collection results"
    )
    args = parser.parse_args(argv)
    if args.format != "json" and args.command != "judge":
        parser.error("--format is only supported by judge")
    if args.require_complete and args.command != "score":
        parser.error("--require-complete is only supported by score")
    if (args.review or args.evidence or args.require_reviewed) and args.command != "score-ai":
        parser.error("AI review options are only supported by score-ai")
    if bool(args.review) != bool(args.evidence):
        parser.error("--review and --evidence must be supplied together")
    inputs = [
        args.suite,
        *args.runs,
        args.judge_a,
        args.judge_b,
        args.assessment,
        args.documents,
        args.review,
        args.evidence,
    ]
    if args.output.resolve() in {path.resolve() for path in inputs if path is not None}:
        parser.error("Output must not overwrite input evidence or judgment files")
    suite = validate_suite(read_json(args.suite))
    if args.command == "collect-index":
        if not args.documents or not args.url:
            parser.error("collect-index requires --documents and --url")
        if args.output.resolve() in {args.suite.resolve(), args.documents.resolve()}:
            parser.error("Output must not overwrite the suite or frozen documents")
        if args.resume:
            parser.error("collect-index rebuilds disposable indices; --resume is unsupported")
        return asyncio.run(
            collect_index(suite, load_documents(args.documents), args.output, args.url)
        )
    if args.command == "collect":
        return asyncio.run(
            collect(
                suite,
                args.output,
                args.resume,
                documents=load_documents(args.documents) if args.documents else None,
            )
        )
    if args.command == "judge":
        if not args.judge:
            parser.error("judge requires --judge a or b")
        if args.format == "csv":
            if args.output.suffix.lower() != ".csv":
                parser.error("CSV judge output must use a .csv extension for merge detection")
            write_judge_csv(args.output, suite, args.judge)
        else:
            if args.output.suffix.lower() == ".csv":
                parser.error("Use --format csv for a .csv judge sheet")
            write_json(args.output, judge_sheet(suite, args.judge))
        return 0
    if args.command == "merge":
        if not args.judge_a or not args.judge_b:
            parser.error("merge requires --judge-a and --judge-b")
        write_json(
            args.output,
            merge_judges(
                suite,
                [
                    read_judge_sheet(args.judge_a, suite, "a"),
                    read_judge_sheet(args.judge_b, suite, "b"),
                ],
            ),
        )
        return 0
    if not args.runs:
        parser.error("pool, score and score-ai require --runs")
    runs = [read_json(path) for path in args.runs]
    if args.command == "score-ai":
        if not args.assessment:
            parser.error("score-ai requires --assessment")
        report = score_ai(
            suite,
            runs,
            read_json(args.assessment),
            read_json(args.review) if args.review else None,
            read_json(args.evidence) if args.evidence else None,
        )
    else:
        report = pool(suite, runs) if args.command == "pool" else score(suite, runs)
    write_json(args.output, report)
    if args.require_complete and report["status"] != "complete":
        return 1
    if args.require_reviewed and report["status"] != "ai_reviewed":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
