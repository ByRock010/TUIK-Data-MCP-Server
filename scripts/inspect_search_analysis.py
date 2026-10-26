"""Record real OpenSearch token graphs and small controlled retrieval probes (#13).

Creates a uniquely named temporary index and deletes only that index in finally.
Does not fetch TÜİK, crawl resources or change the production search alias.
"""

import argparse
import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

from tuik_mcp.search import analysis_settings

CASES = [
    "İ",
    "I",
    "i",
    "ı",
    "İSTANBUL",
    "IĞDIR",
    "İstanbul'un",
    "İstanbul’un",
    "Ankara'da",
    "Ankara’da",
    "TÜİK'in",
    "TÜİK’in",
    "işsizlik",
    "işsizliğin",
    "issizlik",
    "issizligin",
    "nüfus",
    "nüfusun",
    "nufus",
    "nufusun",
    "TÜFE",
    "TUFE",
    "ÜFE",
    "Yİ-ÜFE",
    "YI-UFE",
    "GSYH",
    "HİA",
    "ADNKS",
    "NACE",
    "enflasyon",
    "hayat pahalılığı",
    "milli gelir",
    "dış ticaret",
    "ihracat",
    "büyüme",
    "enflsyon",
    "Tüketici Fiyat Endeksi",
    "Yurt İçi Üretici Fiyat Endeksi",
]

# Controlled linguistic examples, not human-judged thesis relevance data.
TITLES = {
    "unemployment": "İşsizlik Oranı",
    "population": "Adrese Dayalı Nüfus Kayıt Sistemi",
    "cpi": "Tüketici Fiyat Endeksi",
    "ppi": "Yurt İçi Üretici Fiyat Endeksi",
    "gdp": "Gayrisafi Yurt İçi Hasıla",
    "labour": "Hanehalkı İşgücü Araştırması",
    "export": "İhracat",
    "import": "İthalat",
    "istanbul": "İstanbul Nüfusu",
}

PROBE_QUERIES = [
    "işsizliğin",
    "issizlik",
    "issizligin",
    "nufus",
    "nufusun",
    "nüfusun",
    "TÜFE",
    "TUFE",
    "Yİ-ÜFE",
    "enflasyon",
    "enflsyon",
    "İstanbul'un",
    "İstanbul’un",
    "dış ticaret",
]


def probe_definition():
    settings = analysis_settings()
    analysis = settings["analysis"]
    analysis["filter"]["probe_edge"] = {
        "type": "edge_ngram",
        "min_gram": 3,
        "max_gram": 20,
        "preserve_original": True,
    }
    analysis["analyzer"]["probe_edge"] = {
        "type": "custom",
        "char_filter": ["tuik_apostrophes"],
        "tokenizer": "standard",
        "filter": ["apostrophe", "tuik_lowercase", "asciifolding", "probe_edge"],
    }
    analysis["analyzer"]["probe_fold_then_stem"] = {
        "type": "custom",
        "tokenizer": "standard",
        "filter": ["apostrophe", "tuik_lowercase", "asciifolding", "tuik_stemmer"],
    }
    settings.update(number_of_shards=1, number_of_replicas=0)
    return {
        "settings": settings,
        "mappings": {
            "properties": {
                "title": {
                    "type": "text",
                    "analyzer": "tuik_tr_index",
                    "fields": {
                        "ascii": {"type": "text", "analyzer": "tuik_ascii_index"},
                        "prefix": {
                            "type": "text",
                            "analyzer": "probe_edge",
                            "search_analyzer": "tuik_ascii_index",
                        },
                    },
                },
                "builtin": {"type": "text", "analyzer": "turkish"},
            }
        },
    }


def queries(text):
    return {
        "builtin_turkish": {"match": {"builtin": text}},
        "fold_only": {"match": {"title.ascii": text}},
        "fold_fuzzy": {
            "match": {
                "title.ascii": {
                    "query": text,
                    "fuzziness": "AUTO",
                    "prefix_length": 1,
                    "max_expansions": 50,
                }
            }
        },
        "ascii_edge": {"match": {"title.prefix": text}},
        "multi_field": {
            "multi_match": {
                "query": text,
                "type": "best_fields",
                "fields": ["title^4", "title.ascii"],
            }
        },
        "multi_field_synonyms": {
            "dis_max": {
                "queries": [
                    {"match": {"title": {"query": text, "analyzer": "tuik_tr_search", "boost": 4}}},
                    {"match": {"title.ascii": {"query": text, "analyzer": "tuik_ascii_search"}}},
                ]
            }
        },
    }


def inspect(url, output):
    name = "tuik-analysis-probe-" + uuid.uuid4().hex
    report = {
        "status": "error",
        "checked_at": datetime.now(UTC).isoformat(),
        "index": name,
        "tokens": {},
        "retrieval_probes": {},
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "definition": probe_definition(),
    }
    created = False
    with httpx.Client(base_url=url.rstrip("/") + "/", timeout=30, trust_env=False) as client:

        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        try:
            report["engine"] = request("GET", "")["version"]
            request("PUT", name, json=report["definition"])
            created = True
            analyzers = [
                "turkish",
                "tuik_tr_index",
                "tuik_tr_search",
                "tuik_ascii_index",
                "tuik_ascii_search",
                "probe_edge",
                "probe_fold_then_stem",
            ]
            for text in CASES:
                report["tokens"][text] = {
                    analyzer: request(
                        "POST",
                        f"{name}/_analyze",
                        json={
                            "analyzer": analyzer,
                            "text": text,
                        },
                    )["tokens"]
                    for analyzer in analyzers
                }
            for doc_id, title in TITLES.items():
                request("PUT", f"{name}/_doc/{doc_id}", json={"title": title, "builtin": title})
            request("POST", f"{name}/_refresh")
            report["controlled_documents"] = TITLES
            for text in PROBE_QUERIES:
                report["retrieval_probes"][text] = {}
                for variant, query in queries(text).items():
                    response = request(
                        "POST",
                        f"{name}/_search",
                        json={
                            "query": query,
                            "size": 10,
                        },
                    )
                    report["retrieval_probes"][text][variant] = {
                        "query": query,
                        "hits": [hit["_id"] for hit in response["hits"]["hits"]],
                    }
            report["status"] = "ok"
        except Exception as exc:
            report["error"] = {"type": type(exc).__name__, "message": str(exc)}
            raise
        finally:
            try:
                if created:
                    report["cleanup"] = request("DELETE", name)
            except Exception as exc:
                report["status"] = "error"
                report["cleanup_error"] = {"type": type(exc).__name__, "message": str(exc)}
                raise
            finally:
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:9200")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = inspect(args.url, args.output)
    print(f"{report['engine']['number']}: {len(report['tokens'])} cases; {args.output}")


if __name__ == "__main__":
    main()
