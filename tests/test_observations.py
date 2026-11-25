import csv
import hashlib
import io
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from openpyxl import Workbook

from tuik_mcp.clients.base import HttpClient, SourceError
from tuik_mcp.config import Settings
from tuik_mcp.models.observation import (
    Geography,
    ObservationSelection,
    Snapshot,
)
from tuik_mcp.observation_mappings import MAPPINGS
from tuik_mcp.parsers.limits import ParseLimits
from tuik_mcp.services.catalog import CatalogService
from tuik_mcp.services.observations import normalize_workbook, selected_periods

LABOUR = MAPPINGS["labour"]
ORIGINAL = "unemployment-rate-original"
ADJUSTED = "unemployment-rate-adjusted"
EMPLOYED = "employment-adjusted"
URL = "https://veriportali.tuik.gov.tr/tr/press/57988"


def selection(series=ORIGINAL, start="2026-09", end="2026-09", **kwargs):
    return ObservationSelection(series=series, start=start, end=end, **kwargs)


def labour_csv(rows, *, extra_column=None):
    dimensions = list(LABOUR.constants) + list(LABOUR.series[ORIGINAL].selectors)
    columns = [
        *dimensions,
        "Zaman",
        "Gözlem Değeri",
        "Ölçü Birimi",
        "Gizlilik Durumu",
    ]
    if extra_column:
        columns.append(extra_column)
    output = io.StringIO()
    writer = csv.DictWriter(output, columns, delimiter=";")
    writer.writeheader()
    for series, period, value, attrs in rows:
        writer.writerow(
            LABOUR.constants
            | LABOUR.series[series].selectors
            | {"Zaman": period, "Gözlem Değeri": value}
            | attrs
        )
    return output.getvalue().encode()


@pytest.fixture
async def open_service():
    clients = []

    def factory(body, **settings):
        def respond(request):
            if request.url.path == "/api/tr/dataflows":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": LABOUR.resource_id.removeprefix("flow:"),
                            "name": "Temel İşgücü Göstergeleri",
                            "downloadable": True,
                        }
                    ],
                )
            assert request.url.path.endswith("/file/csv")
            return httpx.Response(200, content=body, headers={"content-type": "text/csv"})

        http = HttpClient(
            Settings(request_interval_seconds=0, download_interval_seconds=0, **settings),
            httpx.MockTransport(respond),
        )
        service = CatalogService(http)
        clients.append((http, service))
        return service

    yield factory
    for http, service in clients:
        await service.close()
        await http.close()


async def test_csv_observations_preserve_coordinates_source_scale_and_adjustment(open_service):
    body = labour_csv(
        [
            (ADJUSTED, "2026-09", "8,4", {}),
            (EMPLOYED, "2026-09", "32445", {}),
            (ORIGINAL, "2026-09", "8,5", {}),
        ]
    )
    catalog = open_service(body)
    adjusted = await catalog.observations("labour", selection(ADJUSTED))
    original = await catalog.observations("labour", selection())
    employed = await catalog.observations("labour", selection(EMPLOYED))
    assert adjusted.adjustment == "seasonal"
    assert original.adjustment == "unadjusted"
    assert adjusted.measure.unit == "percent"
    assert employed.measure.unit == "person"
    assert employed.measure.multiplier == 1000
    assert employed.observations[0].numeric_value == Decimal("32445")
    assert employed.status == "complete"
    raw = original.observations[0]
    assert raw.original_value == "8,5"
    assert raw.numeric_value == Decimal("8.5")
    assert raw.locator.record == 4
    assert raw.locator.headers[raw.locator.column - 1] == "Gözlem Değeri"
    assert original.snapshot.sha256 == hashlib.sha256(body).hexdigest()
    assert original.dimensions["Temel Yaş"] == "15+"
    assert original.geography.code == "TR"
    assert original.origins["adjustment"] == "source_declared"
    assert original.origins["selection"] == "user_selected"
    second = await catalog.observations(
        "labour", selection(snapshot_sha256=original.snapshot.sha256)
    )
    assert second.observations[0].id == raw.id
    with pytest.raises(SourceError, match="exact|bytes"):
        await catalog.observations("labour", selection(snapshot_sha256="0" * 64))
    legacy = await catalog.data(LABOUR.resource_id)
    assert legacy.rows[2]["Gözlem Değeri"] == "8,5"


async def test_missing_coverage_markers_units_and_duplicates_are_explicit(open_service):
    body = labour_csv(
        [
            (ORIGINAL, "2026-04", "8,2", {}),
            (ORIGINAL, "2026-05", "-", {}),
            (ORIGINAL, "2026-06", "3", {"Ölçü Birimi": "Unexpected"}),
            (ORIGINAL, "2026-07", "", {"Gizlilik Durumu": "Gizli veri"}),
            (ORIGINAL, "2026-08", "9.9", {}),
        ]
    )
    catalog = open_service(body)
    result = await catalog.observations("labour", selection(start="2026-04"))
    assert result.status == "incomplete"
    assert result.expected_count == 6
    assert result.valid_count == 1
    assert result.absent_periods == ["2026-09"]
    assert [row.status for row in result.observations] == [
        "observed",
        "missing",
        "invalid",
        "suppressed",
        "invalid",
    ]
    assert all(row.numeric_value is None for row in result.observations[1:])

    duplicate = labour_csv([(ORIGINAL, "2026-09", value, {}) for value in ("8,5", "8,6")])
    with pytest.raises(SourceError, match="More than one") as duplicate_error:
        await open_service(duplicate).observations("labour", selection())
    assert duplicate_error.value.code == "ambiguous_observation"

    extra_dimension = labour_csv([(ORIGINAL, "2026-09", "8,5", {})], extra_column="VINTAGE")
    with pytest.raises(SourceError, match="columns") as schema_error:
        await open_service(extra_dimension).observations("labour", selection())
    assert schema_error.value.code == "unsupported_observation_schema"

    with pytest.raises(SourceError, match="budget") as scan_error:
        await open_service(body, query_max_rows=2).observations("labour", selection())
    assert scan_error.value.code == "parse_limit_exceeded"


def test_frequency_and_length_are_validated():
    assert selected_periods(selection(start="2025-12", end="2026-02"), "month") == [
        "2025-12",
        "2026-01",
        "2026-02",
    ]
    assert selected_periods(selection(start="2025-Q4", end="2026-Q1"), "quarter") == [
        "2025-Q4",
        "2026-Q1",
    ]
    with pytest.raises(ValueError, match="Invalid year"):
        selected_periods(selection(), "year")
    with pytest.raises(ValueError, match="120"):
        selected_periods(selection(start="2000", end="2200"), "year")


def test_workbook_header_anchors_and_typed_units_and_geography():
    base = MAPPINGS["population-2025"]
    mapped = replace(base, format="xlsx", sheet="t3")
    series = mapped.series["ankara-men"]
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "t3"
    sheet["A1"] = "Population, 2025"
    sheet.merge_cells("B3:D3")
    sheet["B3"] = "Toplam\nTotal"
    sheet["C4"] = "Erkek\nMale"
    sheet["C11"] = 2_918_853
    output = io.BytesIO()
    workbook.save(output)
    body = output.getvalue()
    snapshot = Snapshot(
        source_url=URL,
        resource_id=mapped.resource_id,
        sha256=hashlib.sha256(body).hexdigest(),
        retrieved_at=datetime.now(UTC),
    )
    observation = normalize_workbook(body, mapped, series, ["2025"], snapshot, ParseLimits())[
        "2025"
    ]
    assert observation.numeric_value == Decimal(2_918_853)
    assert observation.locator.sheet == "t3"
    assert observation.locator.cell == "C11"
    assert observation.locator.headers[1].model_dump() == {
        "cell": "C3",
        "label": "Toplam Total",
        "merged_from": "B3",
    }
    assert series.measure.multiplier == 1
    assert series.geography.level == "province"
    assert series.geography.code == "TR510"
    nuts2 = Geography(code="TR51", label="Ankara", level="nuts2", system="İBBS")
    assert series.geography.level != nuts2.level and series.geography.code != nuts2.code

    industrial = MAPPINGS["industrial-change-2026-09"].series
    assert industrial["manufacturing-yoy-rate"].measure.unit == "percent"
    assert industrial["manufacturing-yoy-contribution"].measure.unit == "percentage_point"
