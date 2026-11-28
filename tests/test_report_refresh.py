import asyncio
import json
from pathlib import Path

import httpx
from test_observations import labour
from test_reports import RATE, RESOURCE, host, observation, read, report_spec, rows, summary

from tuik_mcp.artifacts import parse_uri
from tuik_mcp.clients.base import HttpClient
from tuik_mcp.config import Settings
from tuik_mcp.server import respond
from tuik_mcp.services.catalog import CatalogService
from tuik_mcp.utils.rendering import rendered

ORIGINAL = {"2026-01": "8,4", "2026-03": "8,0"}
CHANGED = {"2026-01": "8,2", "2026-02": "8,1", "2026-04": "7,9"}


class Source:
    def __init__(self, directory, **settings):
        self.body = labour(*rows(RATE, ORIGINAL))
        self.downloads = 0
        self.http = HttpClient(
            Settings(artifact_dir=str(directory), min_interval_seconds=0, attempts=1, **settings),
            httpx.MockTransport(self.serve),
        )
        self.catalog = CatalogService(self.http)

    def serve(self, request):
        if request.url.path == "/api/tr/dataflows":
            return httpx.Response(
                200,
                json=[
                    {"id": RESOURCE.removeprefix("flow:"), "name": "İşgücü", "downloadable": True}
                ],
            )
        if request.url.path.endswith("/file/csv"):
            self.downloads += 1
            if self.body is None:
                return httpx.Response(503)
            return httpx.Response(200, content=self.body, headers={"content-type": "text/csv"})
        raise AssertionError(request.url)

    async def close(self):
        await self.catalog.close()
        await self.http.close()


def spec():
    return report_spec(
        {"kind": "chart", "chart": {"series": ["trend"]}},
        evidence=[
            {
                "kind": "series",
                "key": "trend",
                "resource_id": RESOURCE,
                "filters": RATE,
                "period_from": "2026-01",
                "period_to": "2026-03",
            }
        ],
    )


async def test_refresh_distinguishes_new_periods_backfills_deletions_and_revisions(tmp_path):
    source = Source(tmp_path)
    try:
        before = await respond(source.catalog.report(spec()))
        old_manifest = before["artifact"]["manifest_uri"]
        old_report = read(before, "report.json", tmp_path)
        assert json.loads(read(before, "report-recipe.json", tmp_path))["title"] == spec()["title"]

        source.body = labour(*rows(RATE, CHANGED))
        first, retry = await asyncio.gather(
            *[
                respond(source.catalog.report_refresh.refresh(old_manifest, {"trend": "2026-04"}))
                for _ in range(2)
            ]
        )
        assert source.downloads == 3
        assert first["status"] == "refreshed", first
        assert first["artifact"]["artifact_id"] == retry["artifact"]["artifact_id"]
        assert first["artifact"]["artifact_id"] != before["artifact"]["artifact_id"]
        assert read(before, "report.json", tmp_path) == old_report
        assert len(list(tmp_path.glob("art_*"))) == 2

        diff = json.loads(read(first, "refresh.json", tmp_path))
        assert diff["previous_manifest_uri"] == old_manifest
        assert diff["counts"] == {"new_period": 1, "added": 1, "removed": 1, "revised": 1}
        assert [(change["period"], change["kind"]) for change in diff["changes"]] == [
            ("2026-01", "revised"),
            ("2026-02", "added"),
            ("2026-03", "removed"),
            ("2026-04", "new_period"),
        ]
        before_digest, after_digest = [
            diff[name][0]["sha256"] for name in ("previous_inputs", "current_inputs")
        ]
        assert before_digest != after_digest
        assert diff["previous_inputs"][0]["recipes"] == diff["current_inputs"][0]["recipes"]
        assert diff["previous_inputs"][0]["retained_as"] == "source-1.csv"
    finally:
        await source.close()


async def test_unchanged_and_failed_refresh_have_no_new_artifact(tmp_path):
    source = Source(tmp_path)
    try:
        before = await respond(source.catalog.report(spec()))
        old_manifest = before["artifact"]["manifest_uri"]
        result = await respond(source.catalog.report_refresh.refresh(old_manifest))
        assert result["status"] == "unchanged", result
        assert result["artifact"]["artifact_id"] == before["artifact"]["artifact_id"]
        assert source.downloads == 2
        source.body = None
        failed = await respond(source.catalog.report_refresh.refresh(old_manifest))
        assert failed["status"] == "error"
        assert failed["code"] == "upstream_unavailable"
        assert source.downloads == 3
        assert len(list(tmp_path.glob("art_*"))) == 1
    finally:
        await source.close()


async def test_drift_and_changed_host_prose_never_create_a_report(tmp_path):
    source = Source(tmp_path)
    try:
        before = await respond(
            source.catalog.report(
                report_spec(
                    summary(host("Ocak 2026'da işsizlik oranı %8,4 oldu.", ("rate", "%8,4"))),
                    evidence=[observation("rate", "2026-01")],
                )
            )
        )
        old_manifest = before["artifact"]["manifest_uri"]
        source.body = labour(*rows(RATE, CHANGED))
        rejected = await respond(source.catalog.report_refresh.refresh(old_manifest))
        assert rejected["status"] == "rejected" and rejected["artifact"] is None

        source.body = source.body.replace(b"Zaman", b"Bilinemeyen kolon", 1)
        blocked = await respond(source.catalog.report_refresh.refresh(old_manifest))
        assert blocked["code"] == "recipe_update_required"
        assert len(list(tmp_path.glob("art_*"))) == 1
    finally:
        await source.close()


async def test_expired_or_missing_baseline_does_not_contact_upstream(tmp_path):
    source = Source(tmp_path)
    try:
        uri = f"tuik-artifact://art_{'0' * 32}/manifest.json"
        result = await respond(source.catalog.report_refresh.refresh(uri))
        assert result["code"] == "unreproducible"
        assert source.downloads == 0
    finally:
        await source.close()


async def test_compact_refresh_receipt_keeps_manifest_discoverable(tmp_path):
    source = Source(tmp_path, query_max_output_bytes=1024)
    try:
        before = await respond(source.catalog.report(spec()))
        previous_uri = before["artifact"]["manifest_uri"]
        source.body = labour(*rows(RATE, CHANGED))
        result = await respond(source.catalog.report_refresh.refresh(previous_uri))
        assert result["status"] == "refreshed"
        assert len(rendered(result)) <= 1024
        artifact_id, _ = parse_uri(result["artifact"]["manifest_uri"])
        payload = json.loads(Path(tmp_path, artifact_id, "refresh.json").read_bytes())
        assert payload["previous_manifest_uri"] == previous_uri
    finally:
        await source.close()
