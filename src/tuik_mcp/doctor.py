"""Read-only installation diagnostics; network probes are explicitly opt-in (#98)."""

import argparse
import json
import os
import platform
import sys
from importlib import metadata
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

CORE = ("mcp", "httpx", "pydantic", "beautifulsoup4", "xlrd", "openpyxl", "pdfplumber")
EXTRAS = {"viz": "matplotlib", "search": "opensearch-py", "semantic": "fastembed"}
REPOSITORY = "https://github.com/ByRock010/TUIK-Data-MCP-Server"


def version(name):
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


class NoRedirect(HTTPRedirectHandler):
    """Do not follow an optional probe to an unconfigured host or login page."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe(url):
    """One bounded HEAD request; never return remote text, URLs or exception strings."""
    parts = urlsplit(url)
    if parts.username or parts.password or parts.query or parts.fragment:
        return "warning", "Probe skipped: configured URL contains credentials/query/fragment."
    try:
        request = Request(url, method="HEAD", headers={"User-Agent": "tuik-mcp-doctor"})
        with build_opener(NoRedirect).open(request, timeout=3) as response:
            code = response.status
    except HTTPError as exc:
        code = exc.code
        exc.close()
    except (URLError, OSError, ValueError):
        return "warning", "Endpoint could not be reached; check network, TLS and configuration."
    if 200 <= code < 300:
        return "ok", f"HTTP {code}; reachability only, not a data/contract verification."
    return "warning", f"HTTP {code}; inspect access/redirect/HEAD support before retrying."


def storage_check(path):
    """Inspect permissions without mkdir, a write probe, or modifying existing files."""
    try:
        candidate = path.expanduser().absolute()
        while not candidate.exists() and candidate.parent != candidate:
            candidate = candidate.parent
        writable = candidate.is_dir() and os.access(candidate, os.W_OK | os.X_OK)
    except (OSError, RuntimeError, ValueError):
        writable = False
    return (
        "ok" if writable else "error",
        "Parent/directory permissions allow writes; no file was created."
        if writable
        else "Directory/parent is not writable; choose an existing writable directory.",
    )


def diagnose(*, online=False, storage_dir=None):
    checks = []

    def add(name, status, message, action=None):
        checks.append({"name": name, "status": status, "message": message, "action": action})

    add(
        "python",
        "ok" if sys.version_info >= (3, 12) else "error",
        f"{platform.python_implementation()} {platform.python_version()}",
        "Use Python 3.12 or newer." if sys.version_info < (3, 12) else None,
    )
    installed = version("tuik-mcp")
    add(
        "distribution",
        "ok" if installed else "error",
        f"tuik-mcp {installed}" if installed else "Distribution metadata is missing.",
        "Install this repository's built wheel; see docs/INSTALLATION.md."
        if not installed
        else None,
    )
    if installed:
        urls = metadata.metadata("tuik-mcp").get_all("Project-URL") or []
        identified = any(u == f"Repository, {REPOSITORY}" for u in urls)
        add(
            "distribution_origin",
            "ok" if identified else "warning",
            "Package metadata identifies this repository (not a signature/ownership proof)."
            if identified
            else "Package metadata does not identify this repository.",
            None if identified else "Check the wheel source before configuring a client.",
        )
    for name in CORE:
        found = version(name)
        compatible = found is not None and (name != "mcp" or found.split(".")[0] == "1")
        add(
            f"dependency:{name}",
            "ok" if compatible else "error",
            f"{name} {found}" if found else f"{name} is missing.",
            None if compatible else "Reinstall the wheel with its declared dependencies (SDK 1.x).",
        )
    for extra, dependency in EXTRAS.items():
        found = version(dependency)
        add(
            f"extra:{extra}",
            "ok" if found else "skipped",
            f"{dependency} {found}; installed, not loaded/tested."
            if found
            else f"Optional {extra} extra is absent; core tools remain available.",
            None if found else f"Install the wheel with [{extra}] only if needed.",
        )
    add(
        "storage_permissions",
        *storage_check(Path(storage_dir) if storage_dir is not None else Path.cwd()),
        action="Permission inspection only; quotas, actual writes and future artifact delivery "
        "are not verified. No artifact service is implemented by this command.",
    )

    settings = None
    try:
        from pydantic import ValidationError

        from tuik_mcp.config import Settings

        try:
            settings = Settings.from_env()
        except ValidationError as exc:
            # Pydantic messages/input values may include URLs, credentials and local paths.
            names = sorted({"TUIK_" + str(e["loc"][0]).upper() for e in exc.errors()})
            add("settings", "error", "Invalid settings: " + ", ".join(names), "Check .env.example.")
        else:
            add("settings", "ok", "Known TUIK_* settings validate; values are not displayed.")
    except ImportError:
        add("settings", "error", "Settings validation unavailable.", "Reinstall core dependencies.")

    if settings is not None:
        configured = settings.opensearch_url is not None
        add(
            "search_backend",
            "warning" if configured else "ok",
            "Index configured; readiness requires an explicit online/data check."
            if configured
            else "No index configured; literal dataset/live portal discovery remains available.",
        )
        if settings.semantic_search and configured:
            cache = (
                Path(settings.embedding_cache_dir).expanduser()
                if settings.embedding_cache_dir
                else Path.home() / ".cache" / "tuik-mcp" / "models"
            )
            try:
                present = cache.is_dir() and any(cache.iterdir())
            except OSError:
                present = False
            add(
                "embedding_cache",
                "warning",
                "Cache has entries; model completeness/loading is unverified."
                if present
                else "Model cache is absent/empty/unreadable; hybrid readiness is unverified.",
                "Use tuik-mcp-index --embeddings to provision explicitly. Doctor never imports "
                "fastembed, loads a model or downloads model files; searches fall back to lexical.",
            )
        else:
            add("embedding_cache", "skipped", "Hybrid search is not configured.")

    if online:
        # No download/data endpoints and no retries; at most three HEAD requests.
        endpoints = [
            ("portal", "https://veriportali.tuik.gov.tr/"),
            ("classifications", "https://siniflama.tuik.gov.tr/"),
        ]
        if settings is not None and settings.opensearch_url:
            endpoints.append(("opensearch", str(settings.opensearch_url)))
        for label, url in endpoints:
            add("network:" + label, *probe(url))
    else:
        add("network", "skipped", "No requests sent. Use --online for bounded reachability probes.")

    return {
        "schema_version": 1,
        "ok": not any(c["status"] == "error" for c in checks),
        "checks": checks,
        "notes": [
            "No configuration files, credentials, directories or artifacts are written.",
            "Installed extras, permission bits and endpoint reachability do not prove live data, "
            "index freshness, a usable embedding model or client compatibility.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print structured diagnostics.")
    parser.add_argument("--online", action="store_true", help="Opt in to bounded HEAD requests.")
    parser.add_argument("--storage-dir", type=Path, help="Inspect this directory/nearest parent.")
    args = parser.parse_args(argv)
    report = diagnose(online=args.online, storage_dir=args.storage_dir)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for check in report["checks"]:
            print(f"[{check['status']}] {check['name']}: {check['message']}")
            if check["action"]:
                print(f"  Next: {check['action']}")
        print("Ready for core startup." if report["ok"] else "Fix the errors above before startup.")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
