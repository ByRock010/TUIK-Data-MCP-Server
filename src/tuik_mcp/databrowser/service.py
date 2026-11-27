"""Opt-in, bounded queries against the audited TÜİK DataBrowser route."""

import asyncio
import csv
import io
import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from tuik_mcp.cache import MemoryTTLCache
from tuik_mcp.clients.base import HttpClient, SourceError
from tuik_mcp.databrowser.models import DimensionResponse
from tuik_mcp.databrowser.structure import Structure, read_structure
from tuik_mcp.models.observation import Adjustments, CsvLocator, Observation, Period
from tuik_mcp.utils.normalization import validate_id
from tuik_mcp.utils.rendering import rendered

HOST = "https://databrowser2.tuik.gov.tr"
PROFILES = json.loads(Path(__file__).with_name("profiles.json").read_bytes())
HEADERS = {"UserLang": "tr", "Referer": HOST + "/"}
ROW_LIMIT = 200
BYTE_LIMIT = 256 * 1024
DSD_BYTE_LIMIT = 3 * 1024 * 1024
VALUE = re.compile(r"[+-]?[0-9]{1,24}(?:\.[0-9]{1,24})?(?:[eE][+-]?[0-9]{1,2})?\Z")
FREQUENCIES = {"A": "year", "M": "month", "Q": "quarter", "S": "half"}
FALLBACK = "The public CSV/workbook tools remain available where the catalogue offers an export."


def profile_catalog() -> list[dict]:
    return [
        {
            "resource_id": resource,
            "domain": profile["domain"],
            "period": profile["period"],
            "required_filters": profile["required"],
        }
        for resource, profile in PROFILES.items()
    ]


def source_urls(resource: str) -> tuple[str, str, str]:
    code, version = resource.removeprefix("flow:").split("+V")
    key = f"TR,{code},{version}"
    return (
        f"TR:{code}({version})",
        f"{HOST}/api/core/nodes/1/datasets/{key}",
        f"{HOST}/#/tr/tuik/{key}",
    )


def select_rows(
    body: bytes, structure: Structure, profile: dict, count: int, filters: dict[str, list[str]]
) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig"), newline=""), strict=True)
    dimensions = list(structure.dimensions)
    headers = reader.fieldnames or []
    expected = ["DATAFLOW", *dimensions, "OBS_VALUE", *structure.attributes]
    if set(headers) != set(expected) or len(headers) != len(expected):
        raise ValueError("Unexpected SDMX-CSV columns")
    if [name for name in headers if name in structure.dimensions] != dimensions:
        raise ValueError("SDMX-CSV dimension order changed")
    approved_keys = {tuple(key) for key in profile["keys"]}
    seen = set()
    rows = []
    for row in reader:
        if len(rows) == count or None in row or None in row.values():
            raise ValueError("SDMX-CSV row count or shape differs from preflight")
        key = tuple(row[name] for name in dimensions)
        if row["DATAFLOW"] != structure.flow or row["TIME_PERIOD"] != profile["period"]:
            raise ValueError("Returned dataflow or period does not match the selection")
        if key not in approved_keys or key in seen:
            raise ValueError("A returned dimension key is unreviewed or duplicated")
        if any(row[name] not in codes for name, codes in filters.items()):
            raise ValueError("The response includes a row outside the code filters")
        if any(
            row[name] not in component.codes
            for name, component in structure.dimensions.items()
            if name != "TIME_PERIOD"
        ) or any(
            row[name] and component.codelist and row[name] not in component.codes
            for name, component in structure.attributes.items()
        ):
            raise ValueError("The response uses a code outside the versioned codelists")
        seen.add(key)
        rows.append(row)
    if len(rows) != count:
        raise ValueError("The SDMX-CSV is shorter than the preflight count")
    return rows


def observation(row, index, structure, digest, constants) -> Observation:
    raw = row["OBS_VALUE"]
    flags = {name: row[name] for name in structure.attributes}
    confidential = flags.get("CONF_STATUS") not in {None, "", "F"}
    if confidential:
        status, value, raw = "confidential", None, ""
    elif not raw or raw in {"-", ":"}:
        status, value = "missing", None
    elif not VALUE.fullmatch(raw):
        status, value = "invalid", None
    else:
        status, value = "valid", Decimal(raw)
    freq = row["FREQ"]
    if freq not in FREQUENCIES:
        raise ValueError("Unexpected observation frequency")
    return Observation(
        id=f"obs:{digest[:16]}:{index}",
        status=status,
        value=value,
        value_text=raw,
        locator=CsvLocator(record=index, column="OBS_VALUE"),
        period=Period(
            value=row["TIME_PERIOD"],
            frequency=FREQUENCIES[freq],
            frequency_label=structure.dimensions["FREQ"].codes[freq].get("tr", freq),
            source="source",
        ),
        adjustment=Adjustments(seasonal="unknown", calendar="unknown", source="unknown"),
        dimensions={
            dimension: row[dimension]
            for dimension in structure.dimensions
            if dimension != "TIME_PERIOD" and dimension not in constants
        },
        source_status=flags,
    )


class DataBrowser:
    def __init__(self, http: HttpClient):
        self.http = http
        self.cache = MemoryTTLCache(max_entries=len(PROFILES))
        self.locks = {resource: asyncio.Lock() for resource in PROFILES}

    async def get_structure(self, resource: str, flow: str, endpoint: str) -> Structure:
        async with self.locks[resource]:
            if cached := self.cache.get(resource):
                return cached
            url = endpoint + "/download/structure"
            response = await self.http.request(
                "POST",
                url,
                json=[],
                headers=HEADERS,
                max_bytes=DSD_BYTE_LIMIT,
                classify_status=True,
            )
            try:
                structure = read_structure(
                    response.json(), flow, sha256(response.content).hexdigest(), PROFILES[resource]
                )
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise SourceError(
                    "upstream_contract",
                    "The DataBrowser DSD did not match its audited version.",
                    url,
                ) from exc
            self.cache.set(resource, structure, ttl=3600)
            return structure

    async def query(
        self,
        resource_id: str | None,
        *,
        period: str | None,
        filters: dict[str, list[str]] | None,
        dimension: str | None,
        offset: int,
        limit: int,
        snapshot_id: str | None,
    ) -> DimensionResponse:
        if resource_id is None:
            if any(value is not None for value in (filters, period, dimension, snapshot_id)):
                raise ValueError(
                    "Select a resource_id before requesting dimensions or observations"
                )
            profiles = profile_catalog()
            if offset >= len(profiles):
                raise ValueError("Offset is outside the audited dataflow catalogue")
            result = DimensionResponse(
                status="catalog",
                source_url=HOST,
                profiles=profiles[offset : offset + limit],
                total=len(profiles),
                offset=offset,
                message="Opt-in experiment for audited DataBrowser versions. " + FALLBACK,
            )
            return self.fit(result, "profiles")
        validate_id(resource_id)
        profile = PROFILES.get(resource_id)
        if profile is None:
            raise ValueError("This dataflow version is outside the audited DataBrowser pilot")
        flow, endpoint, source_url = source_urls(resource_id)
        if filters is not None:
            if dimension is not None or period != profile["period"]:
                raise ValueError(f"Query the reviewed period {profile['period']} without dimension")
            if not filters or "FREQ" in filters or "TIME_PERIOD" in filters:
                raise ValueError("Pass audited code filters without FREQ or TIME_PERIOD")
            if any(
                sorted(filters.get(name, [])) != sorted(codes)
                for name, codes in profile["required"].items()
            ):
                raise ValueError("Keep the profile's required code filters")
            if offset and snapshot_id is None:
                raise ValueError("A result snapshot_id is required for observation continuation")
        elif period is not None or snapshot_id is not None or (offset and dimension is None):
            raise ValueError("Specify a code-list dimension for paging or supply query filters")

        structure = await self.get_structure(resource_id, flow, endpoint)
        base = dict(
            source_url=source_url,
            resource_id=resource_id,
            dataflow=flow,
            dsd=structure.id,
            structure_sha256=structure.sha256,
            period=profile["period"],
        )
        if filters is None:
            response = DimensionResponse(
                **base,
                status="structure",
                message="Codes are DSD metadata. Coverage is established by query preflight. "
                + FALLBACK,
                dimensions=[entry.describe() for entry in structure.dimensions.values()],
                attributes=[entry.describe() for entry in structure.attributes.values()],
                offset=offset,
            )
            if dimension is not None:
                entry = structure.dimensions.get(dimension) or structure.attributes.get(dimension)
                if entry is None or not entry.codes:
                    raise ValueError("Choose a coded dimension or attribute from this DSD")
                response.dimension = dimension
                response.total = len(entry.codes)
                selected = list(entry.codes)[offset : offset + limit]
                response.codes = [entry.describe_code(code) for code in selected]
                return self.fit(response, "codes")
            return self.fit(response, "codes")

        resolved = {}
        criteria = []
        approved_keys = profile["keys"]
        for name, codes in filters.items():
            entry = structure.dimensions.get(name)
            if entry is None or not codes or len(set(codes)) != len(codes):
                raise ValueError("Choose an audited dimension with distinct codes")
            index = profile["dimensions"].index(name)
            allowed = {key[index] for key in approved_keys}
            if any(code not in entry.codes or code not in allowed for code in codes):
                raise ValueError(f"Code is invalid or unaudited for {name}")
            criteria.append({"id": name, "type": "CodeValues", "filterValues": codes, "period": 0})
            resolved[name] = [entry.describe_code(code) for code in codes]
        criteria.append(
            {
                "id": "TIME_PERIOD",
                "type": "TimeRange",
                "from": profile["start"],
                "to": profile["end"],
            }
        )
        preflight_url = endpoint + "/PartialCodelists/FREQ"
        response = await self.http.request(
            "POST",
            preflight_url,
            json=criteria,
            headers=HEADERS,
            max_bytes=BYTE_LIMIT,
            classify_status=True,
        )
        try:
            count = response.json()["obsCount"]
        except (ValueError, KeyError, TypeError) as exc:
            raise SourceError(
                "unknown_selection_count",
                "DataBrowser did not confirm the count. " + FALLBACK,
                source_url,
            ) from exc
        if type(count) is not int or count < 0:
            raise SourceError(
                "unknown_selection_count", "DataBrowser returned an invalid count.", source_url
            )
        if count > ROW_LIMIT:
            raise SourceError(
                "selection_too_large", f"Selection exceeds {ROW_LIMIT} observations.", source_url
            )
        if count == 0:
            if snapshot_id is not None:
                raise SourceError("snapshot_changed", "Restart from the first page.", source_url)
            result = DimensionResponse(
                **base,
                status="empty",
                filters=resolved,
                complete=True,
                message="DataBrowser reported zero observations for the selected codes.",
                retrieved_at=datetime.now(UTC),
                response_bytes=0,
            )
            return self.fit(result, "observations")
        raw = await self.http.request(
            "POST",
            endpoint + "/download/csv",
            json=criteria,
            headers=HEADERS,
            max_bytes=BYTE_LIMIT,
            classify_status=True,
        )
        digest = sha256(raw.content).hexdigest()
        actual_snapshot = "sha256:" + digest
        if snapshot_id is not None and snapshot_id != actual_snapshot:
            raise SourceError("snapshot_changed", "Restart from the first page.", source_url)
        if offset >= count:
            raise ValueError("Offset is outside the selected observations")
        try:
            rows = select_rows(raw.content, structure, profile, count, filters)
            constants = {
                name: rows[0][name]
                for name in structure.dimensions
                if name != "TIME_PERIOD" and all(row[name] == rows[0][name] for row in rows)
            }
            observations = [
                observation(row, index + 2, structure, digest, constants)
                for index, row in enumerate(rows)
                if offset <= index < offset + limit
            ]
        except (ValueError, csv.Error, KeyError) as exc:
            raise SourceError(
                "upstream_contract",
                "DataBrowser returned inconsistent coded observations. " + FALLBACK,
                source_url,
            ) from exc
        result = DimensionResponse(
            **base,
            status="ok",
            filters=resolved,
            snapshot_id=actual_snapshot,
            retrieved_at=datetime.now(UTC),
            response_bytes=len(raw.content),
            complete=True,
            total=count,
            offset=offset,
            constant_dimensions=constants,
            observations=observations,
            message="Dimension and attribute codes are source metadata. Statistical units, "
            "geography, adjustment and measure semantics require a reviewed observation recipe. "
            + FALLBACK,
        )
        return self.fit(result, "observations")

    def fit(self, result: DimensionResponse, field: str) -> DimensionResponse:
        items = getattr(result, field)
        had_items = bool(items)
        budget = self.http.settings.query_max_output_bytes
        while True:
            end = result.offset + len(items)
            result.next_offset = end if items and end < result.total else None
            if len(rendered(result.model_dump(mode="json"))) <= budget and (items or not had_items):
                return result
            if not items:
                raise SourceError(
                    "query_limit_exceeded",
                    "The output budget is too small for this reply.",
                    result.source_url,
                )
            items.pop()
