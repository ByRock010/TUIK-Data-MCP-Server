"""Export selected observations through the immutable artifact store."""

from typing import Literal

from tuik_mcp import workbooks
from tuik_mcp.artifacts import ArtifactInput, ArtifactSummary, PendingFile
from tuik_mcp.clients.base import SourceError
from tuik_mcp.config import PORTAL
from tuik_mcp.models.common import Provenance
from tuik_mcp.observations import BY_RESOURCE, SchemaChanged, unavailable
from tuik_mcp.observations.workbooks import BY_WORKBOOK, WorkbookRecipe
from tuik_mcp.services.observations import (
    ObservationService,
    validate_period_range,
    validate_selection,
)
from tuik_mcp.utils.normalization import validate_id

MAX_WORKBOOK_ROWS = 10_000
FILENAME = "tuik-observations.xlsx"
SOURCE_TYPES = {"csv": "text/csv", "xls": "application/vnd.ms-excel", "xlsx": workbooks.MIME_TYPE}


class WorkbookResponse(Provenance):
    status: Literal["ok"] = "ok"
    resource_id: str
    snapshot_id: str
    selected_rows: int
    artifact: ArtifactSummary


class WorkbookService:
    def __init__(self, observations: ObservationService):
        self.observations = observations

    async def export(
        self,
        resource_id: str,
        filters: dict[str, str] | None = None,
        period_from: str | None = None,
        period_to: str | None = None,
        snapshot_id: str | None = None,
    ) -> WorkbookResponse:
        validate_id(resource_id)
        validate_selection(filters, period_from, period_to, offset=0, limit=1)
        validate_period_range(period_from, period_to)
        held = unavailable(resource_id)
        if held is not None:
            raise SourceError(
                "unavailable", f"No reviewed observations for {held.domain}: {held.reason}", PORTAL
            )
        if resource_id not in BY_RESOURCE and resource_id not in BY_WORKBOOK:
            raise SourceError(
                "unsupported_observation_recipe",
                "Select a reviewed resource ID from get_tuik_observations before exporting.",
                PORTAL,
            )
        try:
            recipe, resource, file, table = await self.observations.table(resource_id, snapshot_id)
        except SchemaChanged as exc:
            raise SourceError(
                "schema_changed", f"The source no longer matches its audited recipe: {exc}", PORTAL
            ) from exc

        catalog = self.observations.catalog
        selected, selection, issues, coverage = await catalog.files.work(
            self.observations.select, table, filters or {}, period_from, period_to
        )
        if not selected:
            raise SourceError(
                "empty_selection", "No observations match the selection.", resource.source_url
            )
        if len(selected) > MAX_WORKBOOK_ROWS:
            raise SourceError(
                "workbook_row_limit",
                f"The selection has {len(selected)} observations. "
                f"Narrow it to {MAX_WORKBOOK_ROWS}.",
                resource.source_url,
            )
        observations = [table.observations[index] for index in selected]
        source_format = recipe.format if isinstance(recipe, WorkbookRecipe) else "csv"
        source_scope = "reviewed_cells" if isinstance(recipe, WorkbookRecipe) else "whole_export"
        recipe_ref = self.observations.reference(recipe)
        coverage_data = coverage.model_dump(mode="json") if coverage is not None else None
        artifact_selection = {
            "selection": selection,
            "selected_rows": len(selected),
            "constant_dimensions": table.constant,
            "recipe": recipe_ref.model_dump(mode="json"),
            "source_scope": source_scope,
            "range_coverage": coverage_data,
        }
        digest = "sha256:" + file.digest
        source_rows = [
            ("Publisher", resource.publisher),
            ("Resource", resource.title),
            ("Official page", resource.source_url),
            ("Resource ID", resource_id),
            ("Retrieved at (UTC)", file.retrieved_at.isoformat()),
            ("Published", resource.publication_date or "Not declared"),
            ("Source updated", resource.updated_at or "Not declared"),
            ("Snapshot", digest),
            ("Review recipe", f"{recipe_ref.id} v{recipe_ref.version} · {recipe_ref.evidence}"),
            ("Source scope", source_scope),
            ("Selection", workbooks.json_text(selection)),
            ("Constant dimensions", workbooks.json_text(table.constant)),
            ("Selected records", str(len(selected))),
        ]
        if coverage is not None:
            source_rows.extend(
                [
                    (
                        "Range coverage",
                        f"{coverage.valid} valid of {coverage.expected} expected "
                        f"series periods, {coverage.period_from} to {coverage.period_to}. "
                        "The manifest records reported gaps.",
                    ),
                    ("Coverage complete", "Yes" if coverage.complete else "No"),
                ]
            )
        source_rows.extend(("Recipe note", note) for note in recipe.notes)
        if resource.notes:
            source_rows.append(("Source note", resource.notes))
        source_rows.extend(("Selection warning", issue) for issue in issues)

        def render(manifest_uri: str) -> list[PendingFile]:
            return [
                PendingFile(
                    name=FILENAME,
                    role="output",
                    mime_type=workbooks.MIME_TYPE,
                    content=workbooks.build(
                        observations,
                        source_rows,
                        manifest_uri,
                        complete_range=coverage is not None and coverage.complete,
                    ),
                )
            ]

        artifact = await catalog.artifacts.create(
            kind="workbook",
            title=resource.title,
            citation=f"{resource.publisher} · {resource.source_url}",
            inputs=[
                ArtifactInput(
                    resource_id=resource_id,
                    source_url=resource.source_url,
                    file_url=file.link.source_url,
                    format=source_format,
                    retrieved_at=file.retrieved_at,
                    publication_date=resource.publication_date,
                    sha256=file.digest,
                    size=len(file.body),
                    selection=artifact_selection,
                )
            ],
            files=render,
            sources={0: (f"source.{source_format}", SOURCE_TYPES[source_format], file.body)},
            transformations=[
                "All selected records of the reviewed recipe are exported without pagination.",
                "Comparable series are grouped for counts and valid minimum/maximum.",
                "Excel values use 15 significant digits; exact decimals and source text are kept.",
            ],
            warnings=issues,
        )
        return WorkbookResponse(
            source_url=resource.source_url,
            retrieved_at=file.retrieved_at,
            resource_id=resource_id,
            snapshot_id=digest,
            selected_rows=len(selected),
            artifact=catalog.artifacts.summary(artifact),
        )
