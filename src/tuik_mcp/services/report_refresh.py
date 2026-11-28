"""Replay retained report recipes and explain changes within the selected evidence."""

import asyncio
import hashlib
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ValidationError

from tuik_mcp.artifacts import (
    MANIFEST,
    ArtifactError,
    ArtifactManifest,
    ArtifactSummary,
    PendingFile,
    parse_uri,
    uri,
)
from tuik_mcp.cache import MemoryTTLCache
from tuik_mcp.clients.base import SourceError
from tuik_mcp.models.report import (
    REPORT_RECIPE,
    SCHEMA_VERSION,
    Evidence,
    Point,
    Report,
    ReportSpec,
)
from tuik_mcp.observations import BY_RESOURCE
from tuik_mcp.observations.workbooks import BY_WORKBOOK
from tuik_mcp.services.observations import ordinal, period_frequency
from tuik_mcp.utils.rendering import rendered

DIFF = "refresh.json"
SEARCH_NOTE = "This refresh reads official sources directly. Search index freshness is independent."
DIFF_TYPES = ("new_period", "added", "removed", "revised")
ChangeKind = Literal["new_period", "added", "removed", "revised"]


class Change(BaseModel):
    evidence: str
    kind: ChangeKind
    period: str | None
    previous: Point | None
    current: Point | None


class RefreshReceipt(BaseModel):
    status: Literal["unchanged", "refreshed", "rejected"]
    message: str
    report_id: str
    report_status: Literal["ok", "draft", "rejected"]
    previous_manifest_uri: str
    source_cutoff: date | None
    last_successful_retrieval: datetime
    counts: dict[str, int]
    artifact: ArtifactSummary | None = None


def _snapshot_summary(manifest, report):
    summaries = {}
    for item in report.evidence:
        summaries.setdefault(item.source.resource_id, []).append(
            {"key": item.key, "id": item.source.recipe_id, "version": item.source.recipe_version}
        )
    return [
        {
            "resource_id": item.resource_id,
            "sha256": item.sha256,
            "retrieved_at": item.retrieved_at.isoformat(),
            "publication_date": item.publication_date,
            "retained_as": item.retained_as,
            "columns": item.selection.get("columns"),
            "recipes": summaries[item.resource_id],
        }
        for item in manifest.inputs
    ]


def _check_evidence(previous: Evidence, current: Evidence) -> None:
    fields = ("kind", "frequency", "dimensions", "unit", "measure", "adjustment", "notes")
    differences = [name for name in fields if getattr(previous, name) != getattr(current, name)]
    for name in ("resource_id", "recipe_id", "recipe_version", "source_scope"):
        if getattr(previous.source, name) != getattr(current.source, name):
            differences.append(name)
    if differences:
        raise SourceError(
            "recipe_update_required",
            f"{previous.key}: changed {', '.join(differences)}. Review the selection and save "
            "a new report recipe before comparing.",
            current.source.source_url,
        )


def _records(item: Evidence) -> dict[str | None, Point]:
    if item.kind == "series":
        return {p.period: p for p in item.points if p.id is not None or p.status != "missing"}
    if item.id is None and item.value is None:
        return {}
    period = item.period or item.period_to
    return {period: Point(period=period or "", value=item.value, status=item.status, id=item.id)}


def _value(point: Point) -> tuple[str, Decimal | None]:
    return point.status, Decimal(point.value) if point.value is not None else None


def compare(previous: Report, current: Report) -> list[Change]:
    changes = []
    for before, after in zip(previous.evidence, current.evidence, strict=True):
        if before.key != after.key:
            raise ValueError("Report evidence keys differ after replay")
        _check_evidence(before, after)
        old, new = _records(before), _records(after)
        for period in sorted(old.keys() | new.keys(), key=lambda p: p or ""):
            then, now = old.get(period), new.get(period)
            if then is None:
                latest = before.period_to
                extended = (
                    before.kind == "series"
                    and latest
                    and period
                    and (ordinal(period, before.frequency) > ordinal(latest, before.frequency))
                )
                kind = "new_period" if extended else "added"
            elif now is None:
                kind = "removed"
            elif _value(then) != _value(now):
                kind = "revised"
            else:
                continue
            changes.append(
                Change(evidence=before.key, kind=kind, period=period, previous=then, current=now)
            )
    return changes


class ReportRefreshService:
    def __init__(self, catalog):
        self.catalog = catalog
        self.saved = MemoryTTLCache(max_entries=128)
        self.write_lock = asyncio.Lock()

    def read(self, manifest: ArtifactManifest) -> tuple[Report, ReportSpec]:
        if manifest.kind != "report":
            raise ArtifactError("invalid_report", "The artifact is not a saved TÜİK report.")
        try:
            content, _ = self.catalog.artifacts.read(manifest.artifact_id, "report.json")
            report = Report.model_validate_json(content)
            if any(item.name == REPORT_RECIPE for item in manifest.files):
                content, _ = self.catalog.artifacts.read(manifest.artifact_id, REPORT_RECIPE)
                spec = ReportSpec.model_validate_json(content)
            else:
                spec = report.spec
        except ValidationError as exc:
            raise ArtifactError(
                "invalid_report", "The saved report or recipe cannot be read."
            ) from exc
        if (
            report.schema_version != SCHEMA_VERSION
            or report.status == "rejected"
            or spec is None
            or spec != report.spec
        ):
            raise ArtifactError("invalid_report", "The artifact lacks a supported report recipe.")
        return report, spec

    async def refresh(self, manifest_uri, series_through=None, source_cutoff=None):
        previous_id, name = parse_uri(manifest_uri)
        if name != MANIFEST:
            raise ArtifactError("invalid_report", "Use the saved report's manifest_uri.")
        store = self.catalog.artifacts
        previous_manifest_uri = uri(previous_id, MANIFEST)
        try:
            original = await asyncio.to_thread(store.manifest, previous_id)
        except ArtifactError as exc:
            if exc.code in {"not_found", "expired"}:
                raise ArtifactError(
                    "unreproducible",
                    "The original report is expired or unavailable. Its "
                    "prior evidence cannot be replayed from current TÜİK data.",
                ) from exc
            raise
        previous, spec = await asyncio.to_thread(self.read, original)
        extensions = dict(series_through or {})
        by_key = {item.key: item for item in previous.evidence}
        selections = []
        for selection in spec.evidence:
            updated: dict = {}
            # A snapshot pin is historical evidence rather than a request to serve cached data.
            # Keep the original pin if current bytes still match it.
            if selection.key in extensions:
                target = extensions.pop(selection.key)
                item = by_key[selection.key]
                if (
                    selection.kind != "series"
                    or period_frequency(target, "series_through") != item.frequency
                ):
                    raise ValueError(f"{selection.key}: use a matching series period.")
                if ordinal(target, item.frequency) < ordinal(selection.period_to, item.frequency):
                    raise ValueError(f"{selection.key}: series_through cannot shorten the range.")
                updated["period_to"] = target
            selections.append(selection.model_copy(update=updated))
        if extensions:
            raise ValueError(
                f"Unknown evidence keys in series_through: {', '.join(sorted(extensions))}"
            )
        if source_cutoff is not None and spec.source_cutoff and source_cutoff < spec.source_cutoff:
            raise ValueError("source_cutoff cannot precede the saved report's cutoff.")
        cutoff = source_cutoff or spec.source_cutoff
        replay = spec.model_copy(update={"evidence": selections, "source_cutoff": cutoff})

        # Isolate source/parse caches for each attempt. The same paced HTTP client is reused.
        fresh = type(self.catalog)(self.catalog.portal.http)
        try:
            loaded = {}
            confirmed = []
            for selection in replay.evidence:
                prior = by_key[selection.key]
                recipe = BY_RESOURCE.get(selection.resource_id) or BY_WORKBOOK.get(
                    selection.resource_id
                )
                if recipe is None or (recipe.id, recipe.version) != (
                    prior.source.recipe_id,
                    prior.source.recipe_version,
                ):
                    raise SourceError(
                        "recipe_update_required",
                        f"{selection.key}: the reviewed mapping changed. Save a new report recipe.",
                        prior.source.source_url,
                    )
                try:
                    source = await fresh.reports.load(loaded, selection.resource_id, None)
                except SourceError as exc:
                    if exc.code in {"schema_changed", "snapshot_changed"}:
                        raise SourceError(
                            "recipe_update_required",
                            f"{selection.key}: {exc} Review and save the recipe again.",
                            exc.source_url,
                        ) from exc
                    raise
                if (
                    selection.snapshot_id is not None
                    and selection.snapshot_id != source.snapshot_id
                ):
                    selection = selection.model_copy(update={"snapshot_id": None})
                confirmed.append(selection)
            replay = replay.model_copy(update={"evidence": confirmed})
            current, loaded = await fresh.reports.resolve_report(replay, loaded=loaded)
        finally:
            await fresh.close()

        original_inputs = {item.resource_id: item for item in original.inputs}
        for resource_id, snapshot in loaded.items():
            original_input = original_inputs[resource_id]
            columns = original_input.selection.get("columns")
            if original_input.format != snapshot.file.link.format or (
                columns is not None and columns != snapshot.table.columns
            ):
                raise SourceError(
                    "recipe_update_required",
                    f"{resource_id}: source format or column mapping changed. Review the recipe.",
                    snapshot.resource.source_url,
                )

        changes = compare(previous, current)
        counts = {kind: 0 for kind in DIFF_TYPES}
        for change in changes:
            counts[change.kind] += 1
        previous_inputs = _snapshot_summary(original, previous)
        current_inputs = [
            {
                "resource_id": key,
                "sha256": snapshot.file.digest,
                "columns": snapshot.table.columns,
                "publication_date": snapshot.resource.publication_date,
                "retrieved_at": snapshot.file.retrieved_at.isoformat(),
                "recipes": [
                    {"key": i.key, "id": i.source.recipe_id, "version": i.source.recipe_version}
                    for i in current.evidence
                    if i.source.resource_id == key
                ],
            }
            for key, snapshot in loaded.items()
        ]
        unchanged = (
            current.report_id == previous.report_id
            and current.status == previous.status
            and [i["publication_date"] for i in previous_inputs]
            == [i["publication_date"] for i in current_inputs]
        )
        receipt = RefreshReceipt(
            status="rejected"
            if current.status == "rejected"
            else "unchanged"
            if unchanged
            else "refreshed",
            message=SEARCH_NOTE,
            report_id=current.report_id,
            report_status=current.status,
            previous_manifest_uri=previous_manifest_uri,
            source_cutoff=cutoff,
            last_successful_retrieval=current.timeline.retrieved_to,
            counts=counts,
        )
        if current.status == "rejected":
            return receipt.model_copy(
                update={
                    "message": "The refreshed evidence no longer verifies the report narrative. "
                    "Update the recipe before saving. " + SEARCH_NOTE
                }
            )
        if unchanged:
            return self.fit(receipt, original)

        diff = {
            "schema_version": "tuik.report-refresh/1",
            "previous_manifest_uri": previous_manifest_uri,
            "previous_report_id": previous.report_id,
            "current_report_id": current.report_id,
            "source_cutoff": cutoff.isoformat() if cutoff else None,
            "previous_inputs": previous_inputs,
            "current_inputs": current_inputs,
            "counts": counts,
            "changes": [change.model_dump(mode="json") for change in changes],
            "warnings": [
                SEARCH_NOTE,
                *(
                    [
                        "Some earlier raw inputs were not retained. Only the saved report evidence "
                        "is available for comparison."
                    ]
                    if any(not item["retained_as"] for item in previous_inputs)
                    else []
                ),
            ],
        }
        key = hashlib.sha256(
            rendered(
                [previous_id, current.report_id, [i["publication_date"] for i in current_inputs]]
            )
        ).hexdigest()
        async with self.write_lock:
            cached = self.saved.get(key)
            if cached is not None:
                try:
                    manifest = await asyncio.to_thread(store.manifest, cached)
                    return self.fit(receipt, manifest)
                except ArtifactError as exc:
                    if exc.code not in {"expired", "not_found"}:
                        raise
            manifest = await self.catalog.reports.save(
                current,
                loaded,
                extra=PendingFile(
                    name=DIFF, role="data", mime_type="application/json", content=rendered(diff)
                ),
            )
            self.saved.set(key, manifest.artifact_id, 3600)
        return self.fit(receipt, manifest)

    def fit(self, receipt, manifest):
        summary = self.catalog.artifacts.summary(manifest)
        result = receipt.model_copy(update={"artifact": summary})
        if (
            len(rendered(result.model_dump(mode="json")))
            <= self.catalog.queries.limits.max_output_bytes
        ):
            return result
        summary = summary.model_copy(
            update={"files": [], "retrieval": "Read manifest_uri for all files."}
        )
        return receipt.model_copy(update={"artifact": summary})
