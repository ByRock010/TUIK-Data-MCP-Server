"""Select and page normalized observations from one complete snapshot (#87)."""

from tuik_mcp.clients.base import SourceError
from tuik_mcp.config import PORTAL
from tuik_mcp.models.observation import (
    ObservationCounts,
    ObservationResponse,
    RecipeRef,
)
from tuik_mcp.observations import (
    BY_RESOURCE,
    RECIPES,
    UNAVAILABLE,
    Normalized,
    Recipe,
    SchemaChanged,
    normalize,
    unavailable,
)
from tuik_mcp.observations.normalize import PERIODS
from tuik_mcp.observations.recipes import EVIDENCE
from tuik_mcp.parsers.errors import ParseError, ParseLimitError
from tuik_mcp.utils.normalization import validate_id
from tuik_mcp.utils.rendering import rendered

MAX_LISTED_VALUES = 30


def period_frequency(text: str, name: str) -> str:
    for frequency, pattern in PERIODS.items():
        if pattern.fullmatch(text):
            return frequency
    raise ValueError(f"{name} must be a period such as 2026, 2026-Q1, 2026-H1 or 2026-03")


def validate_selection(filters, period_from, period_to, offset, limit) -> None:
    if filters is not None and (
        not isinstance(filters, dict)
        or len(filters) > 20
        or any(
            not isinstance(k, str) or not isinstance(v, str) or len(k) > 200 or len(v) > 200
            for k, v in filters.items()
        )
    ):
        raise ValueError("filters must map at most 20 concept names to exact published values")
    for value in (period_from, period_to):
        if value is not None and (not isinstance(value, str) or len(value) > 7):
            raise ValueError("Periods must be strings such as 2026, 2026-Q1 or 2026-03")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise ValueError("limit must be an integer between 1 and 200")


def catalog() -> ObservationResponse:
    return ObservationResponse(
        source_url=PORTAL,
        status="catalog",
        message=(
            "Reviewed observation recipes. Call get_tuik_observations with one of the "
            "resource_ids; use example as filters. Other resources keep the raw "
            "get_tuik_data path."
        ),
        recipes=[recipe.summary() for recipe in RECIPES],
        unavailable=list(UNAVAILABLE),
    )


class ObservationService:
    def __init__(self, catalog_service):
        self.catalog = catalog_service

    async def table(self, resource_id: str, snapshot_id: str | None = None):
        """The complete normalized snapshot: (recipe, resource, file, normalized).

        Raises SchemaChanged when the published columns no longer match the recipe.
        """
        recipe = BY_RESOURCE[resource_id]
        resource = await self.catalog.resource(resource_id)
        link = next((item for item in resource.download_links if item.format == "csv"), None)
        if link is None:
            raise SourceError(
                "not_structured", "This resource advertises no CSV export.", resource.source_url
            )
        files = self.catalog.files
        file = await files.download(link.resource_id or resource_id, link)
        if snapshot_id is not None and snapshot_id != "sha256:" + file.digest:
            raise SourceError(
                "snapshot_changed",
                "The retained file differs from snapshot_id; restart from offset 0.",
                file.link.source_url,
            )
        limits = self.catalog.queries.limits
        try:
            normalized = await files.work(
                normalize,
                recipe,
                file.body,
                file.digest,
                max_rows=limits.max_rows,
                max_cells=limits.max_cells,
            )
        except ParseLimitError as exc:
            raise SourceError("parse_limit_exceeded", str(exc), file.link.source_url) from exc
        except ParseError as exc:
            raise SourceError("parse_failed", str(exc), file.link.source_url) from exc
        return recipe, resource, file, normalized

    async def observations(
        self,
        resource_id: str | None = None,
        filters: dict[str, str] | None = None,
        period_from: str | None = None,
        period_to: str | None = None,
        offset: int = 0,
        limit: int = 50,
        snapshot_id: str | None = None,
    ) -> ObservationResponse:
        validate_selection(filters, period_from, period_to, offset, limit)
        if resource_id is None:
            if filters or period_from or period_to or offset or snapshot_id:
                raise ValueError("Selections need a resource_id from the recipe catalog")
            return catalog()
        validate_id(resource_id)
        held = unavailable(resource_id)
        if held is not None:
            return ObservationResponse(
                source_url=PORTAL,
                resource_id=resource_id,
                status="unavailable",
                message=f"No normalized observations for {held.domain}: {held.reason}",
                unavailable=[held],
            )
        if resource_id not in BY_RESOURCE:
            flow = resource_id.split("+", 1)[0] + "+"
            reviewed = [r for r in BY_RESOURCE if r.startswith(flow)]
            hint = f" Reviewed version: {reviewed[0]}." if reviewed else ""
            return ObservationResponse(
                source_url=PORTAL,
                resource_id=resource_id,
                status="unsupported",
                message=(
                    "No reviewed observation recipe covers this resource." + hint + " Call "
                    "get_tuik_observations without resource_id to list recipes, or read raw "
                    "rows with get_tuik_data."
                ),
            )
        if period_from is not None and period_to is not None:
            if period_frequency(period_from, "period_from") != period_frequency(
                period_to, "period_to"
            ):
                raise ValueError("period_from and period_to must have the same frequency")
        recipe = BY_RESOURCE[resource_id]
        try:
            recipe, resource, file, table = await self.table(resource_id, snapshot_id)
        except SchemaChanged as exc:
            return ObservationResponse(
                source_url=PORTAL,
                resource_id=resource_id,
                status="schema_changed",
                recipe=self.reference(recipe),
                message=(
                    "The published columns no longer match the reviewed recipe, so no "
                    "observations are returned. Read raw rows with get_tuik_data."
                ),
                issues=exc.issues,
            )
        selected, selection, issues = self.select(table, filters or {}, period_from, period_to)
        counts = ObservationCounts(
            source_records=table.source_records,
            selected=len(selected),
            **{
                status: sum(1 for i in selected if table.observations[i].status == status)
                for status in (
                    "valid",
                    "missing",
                    "confidential",
                    "invalid",
                    "duplicate",
                    "unmapped",
                )
            },
        )
        if counts.duplicate:
            issues.append(
                f"{counts.duplicate} records share a period and dimensions; none of them "
                "is a valid input."
            )
        if counts.unmapped or counts.invalid:
            issues.append(
                f"{counts.unmapped + counts.invalid} records failed the recipe; see each "
                "observation's reason."
            )
        notes = list(recipe.notes)
        if resource.notes:
            notes.append(resource.notes)
        response = ObservationResponse(
            source_url=file.link.source_url,
            retrieved_at=file.retrieved_at,
            resource_id=resource_id,
            status="ok",
            message=(
                "Normalized from the complete snapshot. value_text is the published text; "
                "value is set only for valid observations. Fields marked source were "
                "published; recipe fields come from the reviewed mapping."
            ),
            recipe=self.reference(recipe),
            snapshot_id="sha256:" + file.digest,
            complete=True,
            counts=counts,
            selection=selection,
            constant_dimensions=table.constant,
            dimension_values={k: v[:MAX_LISTED_VALUES] for k, v in table.varying.items()},
            dimension_values_truncated=any(
                len(v) > MAX_LISTED_VALUES for v in table.varying.values()
            ),
            notes=notes,
            issues=issues,
            offset=offset,
        )
        return await self.catalog.files.work(
            self.page, response, [table.observations[i] for i in selected], offset, limit
        )

    @staticmethod
    def reference(recipe: Recipe) -> RecipeRef:
        return RecipeRef(
            id=recipe.id,
            version=recipe.version,
            domain=recipe.domain,
            title=recipe.title,
            evidence=EVIDENCE,
        )

    @staticmethod
    def select(table: Normalized, filters: dict[str, str], period_from, period_to):
        concepts = list(table.facets[0]) if table.facets else []
        for concept, value in filters.items():
            if concept not in concepts:
                raise ValueError(f"Unknown concept {concept!r}; filter on one of {concepts}")
            values = table.varying.get(concept) or [table.constant.get(concept)]
            if value not in values:
                raise ValueError(
                    f"{concept!r} has no value {value!r}; published values include "
                    f"{values[:MAX_LISTED_VALUES]}"
                )
        selected = [
            index
            for index, facet in enumerate(table.facets)
            if all(facet[c] == v for c, v in filters.items())
        ]
        selection = dict(filters)
        issues: list[str] = []
        if period_from is not None or period_to is not None:
            frequency = period_frequency(period_from or period_to, "period")
            frequencies = {
                table.observations[i].period.frequency
                for i in selected
                if table.observations[i].period is not None
            }
            if frequencies - {frequency}:
                raise ValueError(
                    f"The selection has {sorted(frequencies)} periods; filter Gözlem Sıklığı "
                    "to one frequency before a period range"
                )
            undated = [i for i in selected if table.observations[i].period is None]
            if undated:
                issues.append(
                    f"{len(undated)} selected records have no valid period and are outside "
                    "the period range."
                )
            selected = [
                i
                for i in selected
                if (period := table.observations[i].period) is not None
                and (period_from is None or period.value >= period_from)
                and (period_to is None or period.value <= period_to)
            ]
            if period_from is not None:
                selection["period_from"] = period_from
            if period_to is not None:
                selection["period_to"] = period_to
        return selected, selection, issues

    def page(self, response: ObservationResponse, rows, offset: int, limit: int):
        budget = self.catalog.queries.limits.max_output_bytes
        if offset > len(rows):
            raise ValueError(f"offset is beyond the {len(rows)} selected observations")

        def candidate(count: int) -> ObservationResponse:
            end = offset + count
            more = end < len(rows)
            return response.model_copy(
                update={
                    "observations": rows[offset:end],
                    "has_more": more,
                    "next_offset": end if more else None,
                }
            )

        def fits(result: ObservationResponse) -> bool:
            return len(rendered(result.model_dump(mode="json"))) <= budget

        high = min(limit, len(rows) - offset)
        if fits(candidate(high)):
            return candidate(high)
        low = 0
        while low < high:
            middle = (low + high + 1) // 2
            if fits(candidate(middle)):
                low = middle
            else:
                high = middle - 1
        if low == 0:
            raise SourceError(
                "query_limit_exceeded",
                "Metadata or one observation exceeds the output budget; narrow the filters.",
                response.source_url,
            )
        return candidate(low)
