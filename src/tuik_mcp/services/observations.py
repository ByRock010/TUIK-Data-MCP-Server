"""Select and page normalized observations from one complete snapshot (#87)."""

import base64
import binascii
import hashlib
import json
from itertools import islice

from tuik_mcp.clients.base import SourceError
from tuik_mcp.config import PORTAL
from tuik_mcp.models.observation import (
    ObservationCounts,
    ObservationResponse,
    RangeCoverage,
    RecipeRef,
    SeriesGap,
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
from tuik_mcp.observations.workbooks import (
    BY_WORKBOOK,
    WORKBOOK_RECIPES,
    WorkbookRecipe,
    normalize_workbook,
)
from tuik_mcp.parsers.errors import ParseError, ParseLimitError
from tuik_mcp.utils.normalization import validate_id
from tuik_mcp.utils.rendering import rendered

MAX_LISTED_VALUES = 30
MAX_RANGE_PERIODS = 2400
MAX_GAPS = 20
MAX_GAP_PERIODS = 24
CURSOR_PREFIX = "obs1."
MAX_CURSOR = 32768


def period_frequency(text: str, name: str) -> str:
    for frequency, pattern in PERIODS.items():
        if pattern.fullmatch(text):
            return frequency
    raise ValueError(f"{name} must be a period such as 2026, 2026-Q1, 2026-H1 or 2026-03")


def ordinal(text: str, frequency: str) -> int:
    """Consecutive periods of one frequency get consecutive integers."""
    year = int(text[:4])
    if frequency == "year":
        return year
    if frequency == "month":
        return year * 12 + int(text[5:7]) - 1
    return year * (2 if frequency == "half" else 4) + int(text[-1]) - 1


def period_label(number: int, frequency: str) -> str:
    if frequency == "year":
        return f"{number:04}"
    if frequency == "month":
        year, month = divmod(number, 12)
        return f"{year:04}-{month + 1:02}"
    year, part = divmod(number, 2 if frequency == "half" else 4)
    return f"{year:04}-{'H' if frequency == 'half' else 'Q'}{part + 1}"


def encode_cursor(
    resource_id: str,
    digest: str,
    selection: dict,
    offset: int,
    recipe: Recipe | WorkbookRecipe,
) -> str:
    payload = json.dumps(
        {
            "r": resource_id,
            "s": digest,
            "q": selection,
            "o": offset,
            "m": [recipe.id, recipe.version],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    check = hashlib.sha256(payload).hexdigest()[:16]
    return CURSOR_PREFIX + base64.urlsafe_b64encode(payload).decode().rstrip("=") + "." + check


def decode_cursor(cursor: str) -> dict:
    malformed = SourceError(
        "invalid_cursor", "cursor is malformed; restart the query without it.", PORTAL
    )
    if not isinstance(cursor, str) or len(cursor) > MAX_CURSOR:
        raise malformed
    if not cursor.startswith(CURSOR_PREFIX) or cursor.count(".") != 2:
        raise malformed
    data, check = cursor[len(CURSOR_PREFIX) :].split(".")
    try:
        payload = base64.b64decode(data + "=" * (-len(data) % 4), altchars=b"-_", validate=True)
        if hashlib.sha256(payload).hexdigest()[:16] != check:
            raise malformed
        state = json.loads(payload)
    except (binascii.Error, ValueError, RecursionError) as exc:
        raise malformed from exc
    if not isinstance(state, dict):
        raise malformed
    query = state.get("q")
    mapping = state.get("m")
    if not (
        isinstance(state.get("r"), str)
        and isinstance(state.get("s"), str)
        and isinstance(query, dict)
        and set(query) <= {"filters", "period_from", "period_to"}
        and type(state.get("o")) is int
        and state["o"] > 0
        and isinstance(mapping, list)
        and len(mapping) == 2
        and isinstance(mapping[0], str)
        and type(mapping[1]) is int
        and mapping[1] >= 1
    ):
        raise malformed
    return state


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


def validate_period_range(period_from: str | None, period_to: str | None) -> None:
    if period_from is not None and period_to is not None:
        frequency = period_frequency(period_from, "period_from")
        if frequency != period_frequency(period_to, "period_to"):
            raise ValueError("period_from and period_to must have the same frequency")
        if ordinal(period_from, frequency) > ordinal(period_to, frequency):
            raise ValueError("period_from is after period_to")


def catalog() -> ObservationResponse:
    return ObservationResponse(
        source_url=PORTAL,
        status="catalog",
        message=(
            "Reviewed observation recipes. Call get_tuik_observations with one of the "
            "resource_ids; use example as filters. Other resources keep the raw "
            "get_tuik_data path."
        ),
        recipes=[recipe.summary() for recipe in (*RECIPES, *WORKBOOK_RECIPES)],
        unavailable=list(UNAVAILABLE),
    )


class ObservationService:
    def __init__(self, catalog_service):
        self.catalog = catalog_service

    async def table(self, resource_id: str, snapshot_id: str | None = None):
        """The complete normalized snapshot: (recipe, resource, file, normalized).

        Raises SchemaChanged when the published columns no longer match the recipe.
        """
        recipe = BY_RESOURCE.get(resource_id) or BY_WORKBOOK[resource_id]
        resource = await self.catalog.resource(resource_id)
        workbook = isinstance(recipe, WorkbookRecipe)
        formats = {"xls", "xlsx"} if workbook else {"csv"}
        expected_format = recipe.format if workbook else "csv"
        link = next((item for item in resource.download_links if item.format in formats), None)
        if link is None:
            raise SourceError(
                "not_structured", "The reviewed source file is unavailable.", resource.source_url
            )
        files = self.catalog.files
        file = await files.download(link.resource_id or resource_id, link)
        actual_id = "sha256:" + file.digest
        if snapshot_id is not None and snapshot_id != actual_id:
            raise SourceError(
                "snapshot_changed",
                "The retained file differs from snapshot_id; restart without the cursor.",
                resource.source_url,
            )
        if workbook and recipe.sha256 != file.digest:
            raise SourceError(
                "snapshot_changed",
                "The workbook differs from the audited snapshot. Use raw inspection or "
                "review the workbook before updating the recipe.",
                resource.source_url,
            )
        if file.link.format != expected_format:
            raise SchemaChanged([f"expected {expected_format}, received {file.link.format}"])
        try:
            if workbook:
                normalized = await files.work(
                    normalize_workbook, recipe, file.body, file.digest, files.limits
                )
            else:
                limits = self.catalog.queries.limits
                normalized = await files.work(
                    normalize,
                    recipe,
                    file.body,
                    file.digest,
                    max_rows=limits.max_rows,
                    max_cells=limits.max_cells,
                )
        except ParseLimitError as exc:
            raise SourceError("parse_limit_exceeded", str(exc), resource.source_url) from exc
        except ParseError as exc:
            raise SourceError("parse_failed", str(exc), resource.source_url) from exc
        return recipe, resource, file, normalized

    async def observations(
        self,
        resource_id: str | None = None,
        filters: dict[str, str] | None = None,
        period_from: str | None = None,
        period_to: str | None = None,
        limit: int = 50,
        snapshot_id: str | None = None,
        cursor: str | None = None,
    ) -> ObservationResponse:
        offset = 0
        cursor_recipe = None
        if cursor is not None:
            state = decode_cursor(cursor)
            if filters is not None or period_from is not None or period_to is not None:
                raise SourceError(
                    "invalid_cursor",
                    "cursor already carries the selection; pass only cursor and limit, or "
                    "start a new query without cursor.",
                    PORTAL,
                )
            if resource_id not in (None, state["r"]) or snapshot_id not in (
                None,
                "sha256:" + state["s"],
            ):
                raise SourceError(
                    "invalid_cursor",
                    "cursor belongs to a different resource_id or snapshot_id.",
                    PORTAL,
                )
            resource_id, snapshot_id, offset = state["r"], "sha256:" + state["s"], state["o"]
            cursor_recipe = state["m"]
            filters = state["q"].get("filters")
            period_from = state["q"].get("period_from")
            period_to = state["q"].get("period_to")
        validate_selection(filters, period_from, period_to, offset, limit)
        if resource_id is None:
            if filters or period_from or period_to or snapshot_id:
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
        recipe = BY_RESOURCE.get(resource_id) or BY_WORKBOOK.get(resource_id)
        if cursor_recipe is not None and (
            recipe is None or cursor_recipe != [recipe.id, recipe.version]
        ):
            raise SourceError(
                "recipe_changed",
                "The observation recipe differs from the cursor; restart without it.",
                PORTAL,
            )
        if recipe is None:
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
        validate_period_range(period_from, period_to)
        try:
            recipe, resource, file, table = await self.table(resource_id, snapshot_id)
        except SchemaChanged as exc:
            return ObservationResponse(
                source_url=PORTAL,
                resource_id=resource_id,
                status="schema_changed",
                recipe=self.reference(recipe),
                message=(
                    "The published file no longer matches the reviewed recipe. "
                    "Read raw rows with get_tuik_data."
                ),
                issues=exc.issues,
            )
        selected, selection, issues, coverage = await self.catalog.files.work(
            self.select, table, filters or {}, period_from, period_to
        )
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
        if coverage is not None and not coverage.complete:
            issues.append(
                f"{coverage.expected - coverage.valid} of {coverage.expected} series periods "
                f"from {coverage.period_from} to {coverage.period_to} have no valid value; "
                "see coverage.gaps before aggregating."
            )
        notes = list(recipe.notes)
        if resource.notes:
            notes.append(resource.notes)
        workbook = isinstance(recipe, WorkbookRecipe)
        response = ObservationResponse(
            source_url=resource.source_url if workbook else file.link.source_url,
            retrieved_at=file.retrieved_at,
            resource_id=resource_id,
            status="ok",
            message=(
                "Normalized the reviewed cells in the exact workbook snapshot."
                if workbook
                else "Normalized from the complete CSV export. value_text is the published text; "
                "value is set only for valid observations."
            ),
            recipe=self.reference(recipe),
            snapshot_id="sha256:" + file.digest,
            publication_date=resource.publication_date,
            source_updated_at=resource.updated_at,
            source_scope="reviewed_cells" if workbook else "whole_export",
            complete=True,
            counts=counts,
            selection=selection,
            coverage=coverage,
            constant_dimensions=table.constant,
            dimension_values={k: v[:MAX_LISTED_VALUES] for k, v in table.varying.items()},
            dimension_values_truncated=any(
                len(v) > MAX_LISTED_VALUES for v in table.varying.values()
            ),
            notes=notes,
            issues=issues,
            offset=offset,
        )
        query = {
            key: value
            for key, value in (
                ("filters", filters or None),
                ("period_from", period_from),
                ("period_to", period_to),
            )
            if value is not None
        }

        def continuation(next_offset: int) -> str:
            return encode_cursor(resource_id, file.digest, query, next_offset, recipe)

        return await self.catalog.files.work(
            self.page,
            response,
            [table.observations[i] for i in selected],
            offset,
            limit,
            continuation,
        )

    @staticmethod
    def reference(recipe: Recipe | WorkbookRecipe) -> RecipeRef:
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
        if period_from is None and period_to is None:
            return selected, selection, issues, None
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
        coverage = ObservationService.coverage(table, selected, frequency, period_from, period_to)
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
        return selected, selection, issues, coverage

    @staticmethod
    def coverage(
        table: Normalized, candidates: list[int], frequency, period_from, period_to
    ) -> RangeCoverage:
        """Check every selected series for one valid value in each period of the range.

        Counting existing records cannot see a period the source never published, so
        the expected periods are enumerated from the range.
        """
        dated = [
            ordinal(table.observations[i].period.value, frequency)
            for i in candidates
            if table.observations[i].period is not None
        ]
        start = ordinal(period_from, frequency) if period_from is not None else None
        end = ordinal(period_to, frequency) if period_to is not None else None
        if start is None:
            start = min((n for n in dated if n <= end), default=end)
        if end is None:
            end = max((n for n in dated if n >= start), default=start)
        if end - start + 1 > MAX_RANGE_PERIODS:
            raise ValueError(
                f"The period range spans more than {MAX_RANGE_PERIODS} periods; narrow it"
            )
        identifying = [
            c
            for c in table.varying
            if c not in table.series_exclude and len({table.facets[i][c] for i in candidates}) > 1
        ]
        series: dict[tuple[str, ...], dict[int, bool]] = {}
        for i in candidates:
            periods = series.setdefault(tuple(table.facets[i][c] for c in identifying), {})
            observation = table.observations[i]
            if observation.period is None:
                continue
            number = ordinal(observation.period.value, frequency)
            if start <= number <= end:
                periods[number] = periods.get(number, False) or observation.status == "valid"
        width = end - start + 1
        valid = 0
        gaps = []
        incomplete = 0
        for key, periods in series.items():
            present = sum(periods.values())
            valid += present
            if present == width:
                continue
            incomplete += 1
            if len(gaps) >= MAX_GAPS:
                continue
            absent = islice((n for n in range(start, end + 1) if n not in periods), MAX_GAP_PERIODS)
            unusable = islice((n for n, ok in sorted(periods.items()) if not ok), MAX_GAP_PERIODS)
            gaps.append(
                SeriesGap(
                    dimensions=dict(zip(identifying, key, strict=True)),
                    absent=[period_label(n, frequency) for n in absent],
                    absent_count=width - len(periods),
                    without_value=[period_label(n, frequency) for n in unusable],
                    without_value_count=len(periods) - present,
                )
            )
        expected = len(series) * width
        return RangeCoverage(
            frequency=frequency,
            period_from=period_label(start, frequency),
            period_to=period_label(end, frequency),
            bounds=(
                "requested"
                if period_from is not None and period_to is not None
                else "from_requested"
                if period_from is not None
                else "to_requested"
            ),
            periods=width,
            series=len(series),
            expected=expected,
            valid=valid,
            complete=bool(series) and valid == expected,
            gaps=gaps,
            gaps_truncated=incomplete > len(gaps),
        )

    def page(self, response: ObservationResponse, rows, offset: int, limit: int, continuation):
        budget = self.catalog.queries.limits.max_output_bytes
        if offset > len(rows):
            raise SourceError(
                "invalid_cursor",
                f"cursor is beyond the {len(rows)} selected observations.",
                response.source_url,
            )

        def candidate(count: int) -> ObservationResponse:
            end = offset + count
            more = end < len(rows)
            return response.model_copy(
                update={
                    "observations": rows[offset:end],
                    "has_more": more,
                    "next_cursor": continuation(end) if more else None,
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
