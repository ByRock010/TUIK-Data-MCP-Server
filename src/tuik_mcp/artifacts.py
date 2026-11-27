"""Immutable report artifacts with provenance manifests (#88).

An artifact is a server-owned bundle: generated files, optionally the exact source
files they were made from, and a versioned manifest naming every input (official
page, retrieval time, SHA-256, selection) and every file (MIME type, size, SHA-256).
Bundles are never modified or regenerated: new data always means a new artifact ID.

Storage is separate from the parse/query caches, survives restarts and is bounded
by total bytes, files per bundle, file size, lifetime and concurrent jobs. Callers
never choose paths: IDs are random, file names are validated, files are created
exclusively without following symlinks, and every path must stay inside the root.
Clients read files through MCP resources (`tuik-artifact://<id>/<name>`); a local
user can also export a bundle with `tuik-mcp-artifacts`. A server-local path is
never presented as a download.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from tuik_mcp.config import Settings
from tuik_mcp.services.files import finish_worker

SCHEMA_VERSION = 1
SCHEME = "tuik-artifact"
MANIFEST = "manifest.json"
ARTIFACT_ID = re.compile(r"art_[0-9a-f]{32}")
FILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}(?:\.[A-Za-z0-9]{1,10})?")
DEFAULT_DIR = Path.home() / ".cache" / "tuik-mcp" / "artifacts"
STAGING_GRACE_SECONDS = 3600


def generator_version() -> str:
    from importlib import metadata

    try:
        return metadata.version("tuik-mcp")
    except metadata.PackageNotFoundError:
        return "dev"


class ArtifactError(Exception):
    """An artifact that cannot be served; `code` says why and what to do."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ArtifactInput(BaseModel):
    """One pinned source of a bundle: the exact bytes it was made from."""

    resource_id: str
    source_url: str
    # The verified download URL when it was fetched; TÜİK rotates these within hours.
    file_url: str | None = None
    format: str | None = None
    retrieved_at: datetime
    publication_date: str | None = None
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    selection: dict = Field(default_factory=dict)
    # Bundle file holding the unchanged source bytes, or why they were not kept.
    retained_as: str | None = None
    retention_note: str | None = None


class ArtifactFile(BaseModel):
    name: str
    role: Literal["output", "data", "source", "notes"]
    mime_type: str
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    uri: str


class ArtifactManifest(BaseModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    artifact_id: str = Field(pattern=ARTIFACT_ID.pattern)
    kind: str
    title: str | None = None
    created_at: datetime
    expires_at: datetime
    generator: dict[str, str]
    citation: str
    inputs: list[ArtifactInput]
    # True when inputs were downloaded at different times: a mixed-vintage bundle.
    retrieval_times_differ: bool
    transformations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    files: list[ArtifactFile]


class ArtifactSummary(BaseModel):
    """What a tool result shows: small, with the retrieval routes spelled out."""

    artifact_id: str
    expires_at: datetime
    manifest_uri: str
    files: list[ArtifactFile]
    retrieval: str


class PendingFile(BaseModel):
    name: str
    role: Literal["output", "data", "source", "notes"]
    mime_type: str
    content: bytes


def uri(artifact_id: str, name: str) -> str:
    return f"{SCHEME}://{artifact_id}/{name}"


def parse_uri(value: str) -> tuple[str, str]:
    match = re.fullmatch(rf"{SCHEME}://({ARTIFACT_ID.pattern})/(.+)", str(value))
    if match is None or not valid_name(match[2]):
        raise ArtifactError("invalid_artifact_uri", f"Not a {SCHEME}:// file URI")
    return match[1], match[2]


def valid_name(name: str) -> bool:
    return bool(FILE_NAME.fullmatch(name)) and ".." not in name


class ArtifactStore:
    def __init__(self, settings: Settings, *, root: Path | None = None):
        self.root = Path(root or settings.artifact_dir or DEFAULT_DIR).expanduser()
        self.ttl = timedelta(seconds=settings.artifact_ttl_seconds)
        self.max_total = settings.artifact_max_total_bytes
        self.max_file = settings.artifact_max_file_bytes
        self.max_files = settings.artifact_max_files
        self.retain_sources = settings.artifact_retain_sources
        self.jobs = asyncio.Semaphore(settings.artifact_max_jobs)
        # Bytes reserved by bundles still being written. The quota decision, the
        # reservation and its release at publication all happen under this lock.
        self.space = threading.Condition()
        self.reserved = 0

    # Layout ------------------------------------------------------------------

    def base(self) -> Path:
        """The real root directory; created private, never a symlink."""
        if self.root.is_symlink():
            raise ArtifactError("storage_unavailable", "Artifact directory must not be a symlink")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        return self.root.resolve(strict=True)

    def bundle(self, artifact_id: str) -> Path:
        if not ARTIFACT_ID.fullmatch(artifact_id):
            raise ArtifactError("invalid_artifact_id", "Unknown artifact ID format")
        base = self.base()
        path = base / artifact_id
        if path.is_symlink() or (path.exists() and path.resolve().parent != base):
            raise ArtifactError("not_found", "Artifact is not stored by this server")
        return path

    def inside(self, directory: Path, name: str) -> Path:
        if not valid_name(name):
            raise ArtifactError("invalid_file_name", f"Invalid artifact file name: {name!r}")
        path = directory / name
        if path.is_symlink() or (path.exists() and path.resolve().parent != directory.resolve()):
            raise ArtifactError("not_found", "Artifact file is not stored by this server")
        return path

    # Writing -----------------------------------------------------------------

    async def create(
        self,
        *,
        kind: str,
        title: str | None,
        citation: str,
        inputs: list[ArtifactInput],
        files: list[PendingFile] | Callable[[str], list[PendingFile]],
        sources: dict[int, tuple[str, str, bytes]] | None = None,
        transformations: list[str] | None = None,
        warnings: list[str] | None = None,
    ) -> ArtifactManifest:
        """Write a bundle. A file builder receives its manifest URI within the job slot."""
        async with self.jobs:
            # A cancelled caller cannot stop the thread; keep its slot until it exits.
            return await finish_worker(
                self._create,
                kind,
                title,
                citation,
                inputs,
                files,
                sources or {},
                transformations or [],
                warnings or [],
            )

    def _create(self, kind, title, citation, inputs, files, sources, transformations, warnings):
        if not inputs:
            raise ValueError("An artifact needs at least one pinned input")
        inputs = [item.model_copy() for item in inputs]
        artifact_id = "art_" + secrets.token_hex(16)
        pending = list(files(uri(artifact_id, MANIFEST)) if callable(files) else files)
        warnings = list(warnings)
        for index, (name, mime, content) in sorted(sources.items()):
            item = inputs[index]
            if not self.retain_sources:
                item.retention_note = "Source retention is disabled on this server."
            elif len(content) > self.max_file:
                item.retention_note = (
                    f"Source is larger than the {self.max_file}-byte artifact file limit; "
                    "verify against the official page instead."
                )
            elif hashlib.sha256(content).hexdigest() != item.sha256:
                raise ValueError("Retained source bytes do not match the pinned input hash")
            else:
                item.retained_as = name
                item.retention_note = (
                    "Unchanged official file retained for verification; reuse follows "
                    "TÜİK's terms of use."
                )
                pending.append(
                    PendingFile(name=name, role="source", mime_type=mime, content=content)
                )
        names = [f.name for f in pending]
        if len(set(names)) != len(names) or MANIFEST in names:
            raise ValueError("Artifact file names must be unique and not manifest.json")
        if len(pending) + 1 > self.max_files:
            raise ValueError(f"An artifact holds at most {self.max_files} files")
        for item in pending:
            if not valid_name(item.name):
                raise ValueError(f"Invalid artifact file name: {item.name!r}")
            if len(item.content) > self.max_file:
                raise ValueError(
                    f"{item.name} is {len(item.content)} bytes; the limit is {self.max_file}"
                )
        base = self.base()
        created = datetime.now(UTC)
        times = {item.retrieved_at for item in inputs}
        if len(times) > 1:
            warnings.append(
                "Inputs were retrieved at different times; check each input's retrieved_at."
            )
        manifest = ArtifactManifest(
            artifact_id=artifact_id,
            kind=kind,
            title=title,
            created_at=created,
            expires_at=created + self.ttl,
            generator={"name": "tuik-mcp", "version": generator_version()},
            citation=citation,
            inputs=inputs,
            retrieval_times_differ=len(times) > 1,
            transformations=transformations,
            warnings=warnings,
            files=[
                ArtifactFile(
                    name=item.name,
                    role=item.role,
                    mime_type=item.mime_type,
                    size=len(item.content),
                    sha256=hashlib.sha256(item.content).hexdigest(),
                    uri=uri(artifact_id, item.name),
                )
                for item in pending
            ],
        )
        # The manifest is a stored file too: it counts against both budgets.
        record = manifest.model_dump_json(indent=2).encode()
        if len(record) > self.max_file:
            raise ValueError(
                f"{MANIFEST} would be {len(record)} bytes; the limit is {self.max_file}. "
                "Narrow the selection."
            )
        size = sum(len(item.content) for item in pending) + len(record)
        if size > self.max_total:
            raise ValueError("The artifact is larger than the whole artifact storage budget")
        self._reserve(base, size)
        # Build in a private temporary directory, then publish it with one rename.
        staging = base / f".staging-{artifact_id}"
        published = False
        try:
            staging.mkdir(mode=0o700)
            for item in pending:
                self._write(staging / item.name, item.content)
            self._write(staging / MANIFEST, record)
            with self.space:
                staging.rename(base / artifact_id)
                published = True
                self._release(size)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        finally:
            if not published:
                with self.space:
                    self._release(size)
        return manifest

    def _reserve(self, base: Path, size: int) -> None:
        """Make room for `size` bytes, counting every bundle still being written."""
        with self.space:
            while True:
                stored = self._sweep(base, reserve=self.reserved + size)[1]
                if stored + self.reserved + size <= self.max_total or not self.reserved:
                    break
                # Only bundles in progress fill the budget: wait for them to publish,
                # after which they can be evicted like any other bundle.
                self.space.wait()
            self.reserved += size

    def _release(self, size: int) -> None:
        self.reserved -= size
        self.space.notify_all()

    @staticmethod
    def _write(path: Path, content: bytes) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)

    # Retention ---------------------------------------------------------------

    def entries(self, base: Path) -> list[tuple[datetime, datetime, int, Path]]:
        """(created, expires, bytes, path) of bundles; broken or abandoned ones are expired."""
        expired = datetime.min.replace(tzinfo=UTC)
        found = []
        for path in base.iterdir():
            if path.is_symlink() or not path.is_dir():
                continue
            if path.name.startswith(".staging-"):
                # Another job may still be writing it; only leftovers of a crash expire.
                age = datetime.now(UTC).timestamp() - path.stat().st_mtime
                if age > STAGING_GRACE_SECONDS:
                    found.append((expired, expired, 0, path))
                continue
            if not ARTIFACT_ID.fullmatch(path.name):
                continue
            try:
                manifest = ArtifactManifest.model_validate_json((path / MANIFEST).read_bytes())
                size = sum(f.stat().st_size for f in path.iterdir() if f.is_file())
            except (OSError, ValidationError, ValueError):
                found.append((expired, expired, 0, path))
                continue
            found.append((manifest.created_at, manifest.expires_at, size, path))
        return found

    def sweep(self, base: Path | None = None) -> list[str]:
        """Drop expired, broken and abandoned bundles, then the oldest ones over budget."""
        base = base or self.base()
        with self.space:
            return self._sweep(base, reserve=self.reserved)[0]

    def _sweep(self, base: Path, *, reserve: int) -> tuple[list[str], int]:
        """Removed bundle IDs and the bytes still stored; the caller holds `space`."""
        now = datetime.now(UTC)
        removed = []
        kept = []
        for created, expires, size, path in self.entries(base):
            if expires <= now:
                shutil.rmtree(path, ignore_errors=True)
                removed.append(path.name)
            else:
                kept.append((created, size, path))
        total = sum(size for _, size, _ in kept)
        for _, size, path in sorted(kept):
            if total + reserve <= self.max_total:
                break
            shutil.rmtree(path, ignore_errors=True)
            removed.append(path.name)
            total -= size
        return removed, total

    # Reading -----------------------------------------------------------------

    def manifest(self, artifact_id: str) -> ArtifactManifest:
        path = self.bundle(artifact_id)
        try:
            manifest = ArtifactManifest.model_validate_json(
                self.inside(path, MANIFEST).read_bytes()
            )
        except FileNotFoundError as exc:
            raise ArtifactError(
                "not_found",
                "Artifact not found: it may have expired, been evicted for space, or been "
                "made by another server. Save again (save=true) to create a new artifact.",
            ) from exc
        except (OSError, ValidationError, ValueError) as exc:
            raise ArtifactError("corrupted", "Artifact manifest is unreadable") from exc
        if manifest.expires_at <= datetime.now(UTC):
            raise ArtifactError(
                "expired",
                f"Artifact expired at {manifest.expires_at.isoformat()}. Save again: the new "
                "artifact gets a new ID and is not a copy of this one.",
            )
        return manifest

    def read(self, artifact_id: str, name: str) -> tuple[bytes, str]:
        """File bytes and MIME type; the manifest itself is readable as manifest.json."""
        manifest = self.manifest(artifact_id)
        path = self.bundle(artifact_id)
        if name == MANIFEST:
            return self.inside(path, MANIFEST).read_bytes(), "application/json"
        entry = next((f for f in manifest.files if f.name == name), None)
        if entry is None:
            raise ArtifactError(
                "not_found", f"{name!r} is not in this artifact; see its manifest.json"
            )
        content = self.inside(path, name).read_bytes()
        if len(content) != entry.size or hashlib.sha256(content).hexdigest() != entry.sha256:
            raise ArtifactError("corrupted", f"{name} does not match its manifest hash")
        return content, entry.mime_type

    def summary(self, manifest: ArtifactManifest) -> ArtifactSummary:
        return ArtifactSummary(
            artifact_id=manifest.artifact_id,
            expires_at=manifest.expires_at,
            manifest_uri=uri(manifest.artifact_id, MANIFEST),
            files=manifest.files,
            retrieval=(
                "Read each file URI with MCP resources/read. On the computer running this "
                f"server you can also run: tuik-mcp-artifacts export {manifest.artifact_id} "
                "--to <directory>."
            ),
        )

    def export(self, artifact_id: str, destination: Path) -> list[Path]:
        """Copy a verified bundle into an existing directory, never overwriting files."""
        manifest = self.manifest(artifact_id)
        if not destination.is_dir():
            raise ArtifactError("invalid_destination", f"{destination} is not a directory")
        target = destination / artifact_id
        target.mkdir()
        written = []
        for name in [*(f.name for f in manifest.files), MANIFEST]:
            content, _ = self.read(artifact_id, name)
            self._write(target / name, content)
            written.append(target / name)
        return written


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="tuik-mcp-artifacts",
        description="List, inspect and export report artifacts stored by this computer's server.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="Stored, unexpired artifacts")
    show = commands.add_parser("show", help="Print an artifact's manifest")
    show.add_argument("artifact_id")
    export = commands.add_parser("export", help="Copy an artifact into a directory")
    export.add_argument("artifact_id")
    export.add_argument("--to", type=Path, required=True, help="An existing directory")
    args = parser.parse_args(argv)
    store = ArtifactStore(Settings.from_env())
    try:
        if args.command == "list":
            base = store.base()
            store.sweep(base)
            for created, expires, size, path in sorted(store.entries(base)):
                if ARTIFACT_ID.fullmatch(path.name):
                    print(
                        f"{path.name}  {created.isoformat()}  "
                        f"expires {expires.isoformat()}  {size} B"
                    )
        elif args.command == "show":
            print(json.dumps(store.manifest(args.artifact_id).model_dump(mode="json"), indent=2))
        else:
            for path in store.export(args.artifact_id, args.to):
                print(path)
    except (ArtifactError, OSError) as exc:
        print(f"{getattr(exc, 'code', type(exc).__name__)}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
