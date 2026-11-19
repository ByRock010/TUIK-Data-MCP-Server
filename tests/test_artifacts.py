"""Immutable report artifacts with provenance manifests (#88)."""

import asyncio
import base64
import csv
import hashlib
import io
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tuik_mcp import artifacts
from tuik_mcp.artifacts import (
    MANIFEST,
    ArtifactError,
    ArtifactInput,
    ArtifactManifest,
    ArtifactStore,
    PendingFile,
)
from tuik_mcp.config import Settings

sys.path.insert(0, str(Path(__file__).parent))
from test_inspect_data import FILES, ID, service  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n"
SOURCE = b"Zaman;Gozlem\n2026-01;1,5\n2026-02;1,7\n"
STAMP = datetime(2026, 11, 19, 8, tzinfo=UTC)


def store(tmp_path, **settings):
    return ArtifactStore(Settings(**settings), root=tmp_path / "artifacts")


def pinned(content=SOURCE, retrieved_at=STAMP, resource_id="flow:DF_X+V1.0"):
    return ArtifactInput(
        resource_id=resource_id,
        source_url="https://veriportali.tuik.gov.tr/tr/dataset",
        file_url="https://veriportali.tuik.gov.tr/api/tr/data/downloads?p=x",
        format="csv",
        retrieved_at=retrieved_at,
        sha256=hashlib.sha256(content).hexdigest(),
        size=len(content),
        selection={"x": ["Zaman"]},
    )


def png(size=64):
    return PNG + b"\0" * size


async def make(target, *, files=None, inputs=None, sources=True, **extra):
    return await target.create(
        kind="chart",
        title="Test",
        citation="Kaynak: TÜİK",
        inputs=inputs or [pinned()],
        files=files
        or [PendingFile(name="chart.png", role="output", mime_type="image/png", content=png())],
        sources={0: ("source.csv", "text/csv", SOURCE)} if sources else None,
        **extra,
    )


async def test_bundle_pins_inputs_hashes_files_and_retains_the_source(tmp_path):
    target = store(tmp_path)
    manifest = await make(target, transformations=["t"], warnings=["w"])
    assert artifacts.ARTIFACT_ID.fullmatch(manifest.artifact_id)
    assert manifest.schema_version == 1 and manifest.generator["name"] == "tuik-mcp"
    assert manifest.expires_at - manifest.created_at == timedelta(hours=24)
    (item,) = manifest.inputs
    assert item.retained_as == "source.csv" and "TÜİK's terms" in item.retention_note
    assert not manifest.retrieval_times_differ and manifest.warnings == ["w"]
    by_name = {f.name: f for f in manifest.files}
    assert set(by_name) == {"chart.png", "source.csv"}
    assert by_name["source.csv"].role == "source"
    for name, entry in by_name.items():
        content, mime = target.read(manifest.artifact_id, name)
        assert mime == entry.mime_type and len(content) == entry.size
        assert hashlib.sha256(content).hexdigest() == entry.sha256
        assert entry.uri == f"tuik-artifact://{manifest.artifact_id}/{name}"
    assert target.read(manifest.artifact_id, "source.csv")[0] == SOURCE
    raw, mime = target.read(manifest.artifact_id, MANIFEST)
    assert mime == "application/json"
    assert ArtifactManifest.model_validate_json(raw) == manifest
    # The model sees a summary and retrieval routes, never a filesystem path.
    summary = target.summary(manifest).model_dump(mode="json")
    assert str(tmp_path) not in json.dumps(summary)
    assert "resources/read" in summary["retrieval"]
    assert summary["manifest_uri"].endswith("/manifest.json")


async def test_mixed_retrieval_times_are_flagged_and_mismatched_sources_rejected(tmp_path):
    target = store(tmp_path)
    later = pinned(b"other", STAMP + timedelta(hours=2), "flow:DF_Y+V1.0")
    manifest = await make(target, inputs=[pinned(), later])
    assert manifest.retrieval_times_differ
    assert any("different times" in w for w in manifest.warnings)
    assert manifest.inputs[1].retained_as is None
    with pytest.raises(ValueError, match="do not match"):
        await target.create(
            kind="chart",
            title=None,
            citation="c",
            inputs=[pinned()],
            files=[],
            sources={0: ("source.csv", "text/csv", b"changed bytes")},
        )


async def test_budgets_reject_oversized_files_bundles_and_unsafe_names(tmp_path):
    target = store(tmp_path, artifact_max_file_bytes=64 * 1024, artifact_max_files=3)
    big = PendingFile(name="big.bin", role="data", mime_type="x/y", content=b"0" * 70_000)
    with pytest.raises(ValueError, match="limit"):
        await make(target, files=[big])
    many = [
        PendingFile(name=f"f{i}.txt", role="notes", mime_type="text/plain", content=b"x")
        for i in range(3)
    ]
    with pytest.raises(ValueError, match="at most 3 files"):
        await make(target, files=many)
    for name in ["../escape.txt", "a/b.txt", ".hidden", "manifest.json", "x..y.txt", "s p.txt"]:
        unsafe = PendingFile(name=name, role="notes", mime_type="text/plain", content=b"x")
        with pytest.raises(ValueError):
            await make(target, files=[unsafe], sources=False)
    # A source above the file limit is recorded, not retained, and does not fail.
    large = pinned(b"y" * 70_000)
    manifest = await target.create(
        kind="chart",
        title=None,
        citation="c",
        inputs=[large],
        files=[PendingFile(name="chart.png", role="output", mime_type="image/png", content=png())],
        sources={0: ("source.csv", "text/csv", b"y" * 70_000)},
    )
    assert manifest.inputs[0].retained_as is None
    assert "official page" in manifest.inputs[0].retention_note
    disabled = store(tmp_path, artifact_retain_sources=False)
    kept = await make(disabled)
    assert kept.inputs[0].retained_as is None and "disabled" in kept.inputs[0].retention_note


async def test_total_budget_evicts_the_oldest_and_expiry_is_actionable(tmp_path):
    target = store(tmp_path, artifact_max_total_bytes=1024 * 1024)
    chunk = PendingFile(name="data.bin", role="data", mime_type="x/y", content=b"1" * 400_000)
    first = await make(target, files=[chunk], sources=False)
    second = await make(target, files=[chunk], sources=False)
    third = await make(target, files=[chunk], sources=False)
    with pytest.raises(ArtifactError) as gone:
        target.manifest(first.artifact_id)
    assert gone.value.code == "not_found" and "Save again" in str(gone.value)
    assert target.manifest(second.artifact_id) and target.manifest(third.artifact_id)
    with pytest.raises(ValueError, match="whole artifact storage budget"):
        await make(target, files=[chunk.model_copy(update={"content": b"1" * 1_100_000})])
    # Expired bundles answer with when and what to do, then disappear on the next sweep.
    path = target.bundle(third.artifact_id) / MANIFEST
    stale = third.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)})
    path.write_text(stale.model_dump_json())
    with pytest.raises(ArtifactError) as expired:
        target.read(third.artifact_id, "data.bin")
    assert expired.value.code == "expired" and "new ID" in str(expired.value)
    assert third.artifact_id in target.sweep()
    assert not target.bundle(third.artifact_id).exists()


async def test_restart_reads_the_same_bundle_and_tampering_is_detected(tmp_path):
    manifest = await make(store(tmp_path))
    restarted = store(tmp_path)
    assert restarted.read(manifest.artifact_id, "chart.png")[0] == png()
    (restarted.bundle(manifest.artifact_id) / "chart.png").write_bytes(png(65))
    with pytest.raises(ArtifactError) as corrupted:
        restarted.read(manifest.artifact_id, "chart.png")
    assert corrupted.value.code == "corrupted"
    with pytest.raises(ArtifactError) as unknown:
        restarted.read(manifest.artifact_id, "other.png")
    assert unknown.value.code == "not_found"


async def test_ids_paths_and_symlinks_cannot_escape_the_root(tmp_path):
    target = store(tmp_path)
    manifest = await make(target)
    for value in [
        "tuik-artifact://art_x/chart.png",
        f"tuik-artifact://{manifest.artifact_id}/../secret",
        f"tuik-artifact://{manifest.artifact_id}/a/b",
        "file:///etc/passwd",
    ]:
        with pytest.raises(ArtifactError):
            artifacts.parse_uri(value)
    with pytest.raises(ArtifactError):
        target.manifest("../etc")
    outside = tmp_path / "outside.png"
    outside.write_bytes(png())
    linked = target.bundle(manifest.artifact_id) / "chart.png"
    linked.unlink()
    linked.symlink_to(outside)
    with pytest.raises(ArtifactError):
        target.read(manifest.artifact_id, "chart.png")
    # A symlinked bundle directory is not served either.
    fake = target.base() / ("art_" + "0" * 32)
    fake.symlink_to(target.bundle(manifest.artifact_id))
    with pytest.raises(ArtifactError):
        target.manifest(fake.name)
    root_link = tmp_path / "linked-root"
    root_link.symlink_to(tmp_path / "artifacts")
    with pytest.raises(ArtifactError) as refused:
        ArtifactStore(Settings(), root=root_link).base()
    assert refused.value.code == "storage_unavailable"


async def test_concurrent_jobs_are_bounded_and_staging_is_never_swept(tmp_path, monkeypatch):
    target = store(tmp_path, artifact_max_jobs=1)
    running, peak = 0, 0
    original = target._create

    def counted(*args):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        try:
            return original(*args)
        finally:
            running -= 1

    monkeypatch.setattr(target, "_create", counted)
    results = await asyncio.gather(*(make(target) for _ in range(4)))
    assert peak == 1 and len({m.artifact_id for m in results}) == 4
    staging = target.base() / ".staging-art_inprogress"
    staging.mkdir()
    target.sweep()
    assert staging.exists()  # Another job may still be writing it.
    old = datetime.now(UTC).timestamp() - artifacts.STAGING_GRACE_SECONDS - 1
    os.utime(staging, (old, old))
    target.sweep()
    assert not staging.exists()


async def test_cli_lists_shows_and_exports_without_overwriting(tmp_path, monkeypatch, capsys):
    root = tmp_path / "artifacts"
    monkeypatch.setenv("TUIK_ARTIFACT_DIR", str(root))
    manifest = await make(ArtifactStore(Settings.from_env()))
    assert artifacts.main(["list"]) == 0
    assert manifest.artifact_id in capsys.readouterr().out
    assert artifacts.main(["show", manifest.artifact_id]) == 0
    assert json.loads(capsys.readouterr().out)["artifact_id"] == manifest.artifact_id
    out = tmp_path / "out"
    out.mkdir()
    assert artifacts.main(["export", manifest.artifact_id, "--to", str(out)]) == 0
    exported = out / manifest.artifact_id
    assert sorted(p.name for p in exported.iterdir()) == ["chart.png", MANIFEST, "source.csv"]
    assert (exported / "source.csv").read_bytes() == SOURCE
    assert artifacts.main(["export", manifest.artifact_id, "--to", str(out)]) == 1
    assert artifacts.main(["export", "art_" + "f" * 32, "--to", str(out)]) == 1
    assert "not_found" in capsys.readouterr().err


async def test_saved_chart_bundles_png_plotted_values_notes_and_source(tmp_path):
    pytest.importorskip("matplotlib")
    body = (FILES / "labour-adjusted.xls").read_bytes()
    http, catalog, _ = service(body, "xls", artifact_dir=str(tmp_path / "artifacts"))
    x = ["Yıllar - Years [column 1]", "column_2"]
    y = ["İşsizlik oranı Unemployment rate (%) [column 10]"]
    try:
        plain, _ = await catalog.chart(ID, x, y, last=12)
        assert plain.artifact is None
        result, image = await catalog.chart(ID, x, y, last=12, save=True)
    finally:
        await http.close()
    artifact = result.artifact
    names = {f.name: f for f in artifact.files}
    assert set(names) == {"chart.png", "chart-data.csv", "README.txt", "source.xls"}
    store_ = catalog.artifacts
    assert store_.read(artifact.artifact_id, "chart.png")[0] == image
    assert store_.read(artifact.artifact_id, "source.xls") == (body, "application/vnd.ms-excel")
    rows = list(
        csv.reader(io.StringIO(store_.read(artifact.artifact_id, "chart-data.csv")[0].decode()))
    )
    assert rows[0] == [" | ".join(x), result.series[0].name] and len(rows) == 13
    (line,) = result.series
    assert rows[-1] == [line.last.x, "7.8"]
    notes = store_.read(artifact.artifact_id, "README.txt")[0].decode()
    assert "Kaynak: TÜİK" in notes and "manifest.json" in notes
    manifest = store_.manifest(artifact.artifact_id)
    (item,) = manifest.inputs
    assert item.sha256 == result.snapshot_id.removeprefix("sha256:")
    assert item.retrieved_at == result.retrieved_at and item.format == "xls"
    assert item.selection["last"] == 12 and item.selection["y"] == y
    assert manifest.citation.startswith("Kaynak: TÜİK") and manifest.title == result.title
    with pytest.raises(ValueError, match="save must be a boolean"):
        await catalog.chart(ID, x, y, save="yes")


async def test_stdio_client_reads_binary_artifacts_including_a_large_file(tmp_path):
    pytest.importorskip("matplotlib")
    root = tmp_path / "artifacts"
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).with_name("search_stdio_server.py")), "portal"],
        env={"TUIK_ARTIFACT_DIR": str(root)},
    )
    # A large bundle made by an earlier server process must survive the restart.
    big = os.urandom(6 * 1024 * 1024)
    large = await make(
        ArtifactStore(Settings(artifact_dir=str(root))),
        files=[
            PendingFile(name="data.bin", role="data", mime_type="application/x-test", content=big)
        ],
    )
    legacy = "Yıl;Değer\n2026;1,5\n".encode("cp1254")
    encoded = await make(
        ArtifactStore(Settings(artifact_dir=str(root))),
        inputs=[pinned(legacy)],
        sources=False,
        files=[PendingFile(name="source.csv", role="source", mime_type="text/csv", content=legacy)],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            templates = (await session.list_resource_templates()).resourceTemplates
            assert [t.uriTemplate for t in templates] == ["tuik-artifact://{artifact_id}/{name}"]
            tool = next(
                t for t in (await session.list_tools()).tools if t.name == "chart_tuik_data"
            )
            assert tool.inputSchema["properties"]["save"]["default"] is False
            result = await session.call_tool(
                "chart_tuik_data",
                {
                    "resource_id": "flow:DF_ALTINSIZ_DTH+V1.0",
                    "x": ["Zaman (2015-01)"],
                    "y": ["Gözlem Değeri"],
                    "save": True,
                },
            )
            assert not result.isError
            image, text, *links = result.content
            assert [link.type for link in links] == ["resource_link"] * 4
            summary = result.structuredContent["artifact"]
            assert len(text.text) < 6000 and str(root) not in text.text
            for link, entry in zip(links, summary["files"], strict=True):
                assert (str(link.uri), link.mimeType, link.size) == (
                    entry["uri"],
                    entry["mime_type"],
                    entry["size"],
                )
                (contents,) = (await session.read_resource(link.uri)).contents
                assert contents.mimeType == entry["mime_type"]
                data = (
                    base64.b64decode(contents.blob)
                    if hasattr(contents, "blob")
                    else contents.text.encode()
                )
                assert hashlib.sha256(data).hexdigest() == entry["sha256"]
            png_link = next(link for link in links if link.name == "chart.png")
            (blob,) = (await session.read_resource(png_link.uri)).contents
            assert base64.b64decode(blob.blob) == base64.b64decode(image.data)
            manifest = (await session.read_resource(summary["manifest_uri"])).contents[0]
            assert json.loads(manifest.text)["artifact_id"] == summary["artifact_id"]
            (contents,) = (
                await session.read_resource(f"tuik-artifact://{large.artifact_id}/data.bin")
            ).contents
            assert base64.b64decode(contents.blob) == big
            # A source in another encoding stays byte-exact as a blob.
            legacy = "Yıl;Değer\n2026;1,5\n".encode("cp1254")
            (contents,) = (
                await session.read_resource(f"tuik-artifact://{encoded.artifact_id}/source.csv")
            ).contents
            assert contents.mimeType == "text/csv" and base64.b64decode(contents.blob) == legacy
            with pytest.raises(Exception, match="not_found"):
                await session.read_resource(f"tuik-artifact://art_{'0' * 32}/data.bin")
