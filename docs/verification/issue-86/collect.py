"""Collect the fixed, paced official source sample for the #86 audit."""

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

import httpx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PORTAL = "https://veriportali.tuik.gov.tr"
CAP = 16 * 1024 * 1024
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; tuik-mcp/0.3; public statistical research)",
    "X-Requested-With": "XMLHttpRequest",
}


class DownloadError(Exception):
    pass


class Collector:
    def __init__(self, client):
        self.client = client
        self.next_request = 0.0
        self.next_file = 0.0

    def get(self, url, cap):
        parts = urlsplit(url)
        if parts.netloc != "veriportali.tuik.gov.tr" or parts.scheme != "https":
            raise DownloadError("unexpected source host")
        listing = parts.path.endswith("/downloads")
        time.sleep(
            max(0, max(self.next_request, self.next_file if listing else 0) - time.monotonic())
        )
        self.next_request = time.monotonic() + 0.65
        if listing:
            self.next_file = time.monotonic() + 6.25
        with self.client.stream("GET", url) as response:
            if response.status_code != 200:
                raise DownloadError(f"HTTP {response.status_code}")
            known_size = response.headers.get("content-length")
            if known_size and "content-encoding" not in response.headers and int(known_size) > cap:
                raise DownloadError(f"Content-Length {known_size} exceeds {cap}")
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > cap:
                    raise DownloadError(f"decoded response exceeds {cap}")
            return bytes(body), response.headers.get("content-type", "")

    def cached_json(self, folder, name, url):
        path = folder / name
        if not path.exists():
            data, _ = self.get(url, 2 * 1024 * 1024)
            path.write_bytes(data)
        return json.loads(path.read_bytes())


def file_candidates(catalogue, domain, spec, releases):
    for prefix in spec["flow_prefixes"]:
        matches = [row for row in catalogue if row["id"].startswith(prefix) and row["downloadable"]]
        if not matches:
            continue
        row = max(matches, key=lambda item: tuple(map(int, item["id"].split("+V")[1].split("."))))
        code = quote(row["id"], safe="")
        ident, ver = row["id"].split("+V")
        yield {
            "domain": domain,
            "kind": "csv",
            "id": row["id"],
            "title": row["name"],
            "source_period": row.get("period"),
            "source_date": row.get("updatedAt"),
            "url": f"{PORTAL}/api/tr/dataflows/{code}/file/csv",
            "source_page": f"{PORTAL}/tr/databrowser/tuik/TR,{ident},{ver}",
        }

    for number, data in releases.items():
        choices = [
            entry
            for entry in list(data.get("tables") or []) + list(data.get("statisticalTables") or [])
            if entry.get("type") in {"xls", "xlsx"} and entry.get("url")
        ]
        preferred = spec["preferred_titles"]

        def rank(entry, preferred=preferred):
            return next(
                (
                    i
                    for i, pattern in enumerate(preferred)
                    if pattern.casefold() in entry["title"].casefold()
                ),
                len(preferred),
            )

        for entry in sorted(choices, key=rank):
            yield {
                "domain": domain,
                "kind": "spreadsheet",
                "id": f"press:{number}",
                "title": entry["title"],
                "source_period": data.get("period"),
                "source_date": data.get("date"),
                "url": urljoin(PORTAL, entry["url"]),
                "source_page": f"{PORTAL}/tr/press/{number}",
            }


def actual_format(body, item, media_type):
    if body.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
        return "xls"
    if body.startswith(b"PK\x03\x04"):
        return "xlsx"
    if item["kind"] == "csv" and "csv" in media_type.casefold():
        return "csv"
    raise DownloadError(f"unexpected media type {media_type}")


def collect(folder):
    selection = json.loads((HERE / "selection.json").read_bytes())
    prior = json.loads((ROOT / "tests" / "fixtures" / "files" / "manifest.json").read_bytes())
    used_hashes = {entry["sha256"] for entry in prior}
    used_hashes.add(
        hashlib.sha256((ROOT / "tests" / "fixtures" / "data.csv").read_bytes()).hexdigest()
    )
    manifest = []
    rejected = []
    with httpx.Client(headers=HEADERS, timeout=24, follow_redirects=False) as client:
        downloader = Collector(client)
        catalogue = downloader.cached_json(folder, "catalogue.json", f"{PORTAL}/api/tr/dataflows")
        for domain, spec in selection["domains"].items():
            releases = {
                number: downloader.cached_json(
                    folder, f"press-{number}.json", f"{PORTAL}/api/tr/press/{number}"
                )["data"]
                for number in spec["releases"]
            }
            accepted = 0
            csv_count = 0
            seen = set()
            for item in file_candidates(catalogue, domain, spec, releases):
                if accepted == selection["per_domain"]:
                    break
                csv_candidate = item["kind"] == "csv"
                key = (item["id"], item["title"])
                if key in seen or (csv_candidate and accepted >= spec["csv_target"]):
                    continue
                seen.add(key)
                token = hashlib.sha256(item["url"].encode()).hexdigest()[:16]
                path = folder / f"{token}.bytes"
                try:
                    media_type = "text/csv" if csv_candidate else "cached spreadsheet"
                    if not path.exists():
                        body, media_type = downloader.get(item["url"], CAP)
                        fmt = actual_format(body, item, media_type)
                        path.write_bytes(body)
                    else:
                        body = path.read_bytes()
                        fmt = actual_format(body, item, media_type)
                    digest = hashlib.sha256(body).hexdigest()
                    if digest in used_hashes:
                        raise DownloadError("identical to an earlier sample or repository fixture")
                except (httpx.HTTPError, DownloadError) as exc:
                    rejected.append(
                        {k: item[k] for k in ("domain", "id", "title", "source_page")}
                        | {
                            "reason": str(exc).splitlines()[0][:160],
                        }
                    )
                    print("skip", item["id"], item["title"], type(exc).__name__, flush=True)
                    continue
                used_hashes.add(digest)
                accepted += 1
                csv_count += csv_candidate
                manifest.append(
                    {k: item[k] for k in item if k not in {"url", "kind"}}
                    | {
                        "format": fmt,
                        "local_file": path.name,
                        "sha256": digest,
                        "size_bytes": len(body),
                        "retrieved_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(
                            timespec="seconds"
                        ),
                        "split": "held_out"
                        if accepted in selection["held_out_positions"]
                        else "development",
                    }
                )
                print(f"{domain} {accepted:02} {fmt} {len(body):>8} {item['id']}", flush=True)
            print(f"Domain {domain} retained {accepted} ({csv_count} CSV)", flush=True)
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    (folder / "excluded.json").write_text(json.dumps(rejected, ensure_ascii=False, indent=2) + "\n")
    assert len(manifest) == len(selection["domains"]) * selection["per_domain"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_directory", type=Path)
    args = parser.parse_args()
    args.input_directory.mkdir(parents=True, exist_ok=True)
    collect(args.input_directory)


if __name__ == "__main__":
    main()
