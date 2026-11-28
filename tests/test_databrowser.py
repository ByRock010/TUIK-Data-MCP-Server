"""Recorded contracts, validation failures, and MCP coverage for the dimensional pilot."""

import csv
import io
import json
import sys
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tuik_mcp.clients.base import HttpClient, SourceError
from tuik_mcp.config import Settings
from tuik_mcp.databrowser.service import PROFILES, DataBrowser
from tuik_mcp.utils.rendering import rendered

FIXTURES = Path(__file__).with_name("fixtures") / "databrowser"
AUDIT = Path(__file__).resolve().parents[1] / "docs/verification/issue-104/evidence-2026-11-23.json"
PRICE = "flow:DF_TUFE_SDMX_TT05+V1.0"


def fixture_browser(resource=PRICE, count=None, transform=None, failure=None, **settings):
    profile = PROFILES[resource]
    domain = profile["domain"]
    calls = []
    file = (FIXTURES / f"{domain}-selected.csv").read_bytes()
    rows = list(csv.DictReader(io.StringIO(file.decode("utf-8-sig"))))

    def handler(request):
        route = request.url.path.rsplit("/", 1)[-1]
        calls.append(route)
        assert request.url.host == "databrowser2.tuik.gov.tr"
        if failure is not None:
            if failure == "timeout":
                raise httpx.ReadTimeout("fixture")
            return httpx.Response(failure, text="redacted test fixture")
        if route == "structure":
            assert json.loads(request.content) == []
            return httpx.Response(200, content=(FIXTURES / f"{domain}-dsd.json").read_bytes())
        criteria = json.loads(request.content)
        assert criteria[-1] == {
            "id": "TIME_PERIOD",
            "type": "TimeRange",
            "from": profile["start"],
            "to": profile["end"],
        }
        filters = {item["id"]: item["filterValues"] for item in criteria[:-1]}
        assert all(filters[key] == expected for key, expected in profile["required"].items())
        found = [row for row in rows if all(row[k] in vals for k, vals in filters.items())]
        if route == "FREQ":
            return httpx.Response(
                200, json=count if count is not None else {"obsCount": len(found)}
            )
        assert route == "csv"
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(transform(found) if transform else found)
        return httpx.Response(200, content=output.getvalue(), headers={"content-type": "text/csv"})

    http = HttpClient(
        Settings(min_interval_seconds=0, attempts=1, **settings), httpx.MockTransport(handler)
    )
    return http, DataBrowser(http), calls


async def query(browser, resource=PRICE, **options):
    return await browser.query(
        resource,
        **(
            {
                "period": PROFILES[resource]["period"],
                "filters": PROFILES[resource]["required"],
                "dimension": None,
                "offset": 0,
                "limit": 50,
                "snapshot_id": None,
            }
            | options
        ),
    )


@pytest.mark.parametrize("resource", PROFILES)
async def test_audited_rows_keep_their_values_keys_attributes_and_locators(resource):
    profile = PROFILES[resource]
    audit = json.loads(AUDIT.read_text())["domains"][profile["domain"]]
    http, browser, calls = fixture_browser(resource)
    try:
        expected = {
            tuple(row[key] for key in profile["dimensions"]): row for row in audit["matched_rows"]
        }
        result = await query(browser, resource)
        assert result.complete and result.total == len(expected) == audit["selection_count"]
        assert result.dsd == audit["dsd_id"] and result.snapshot_id.startswith("sha256:")
        assert 0 < result.response_bytes <= result.upstream_byte_limit
        records = []
        while True:
            records.extend(result.observations)
            if result.next_offset is None:
                break
            result = await query(
                browser, resource, offset=result.next_offset, snapshot_id=result.snapshot_id
            )
        for obs in records:
            fields = result.constant_dimensions | obs.dimensions | {"TIME_PERIOD": obs.period.value}
            row = expected.pop(tuple(fields[key] for key in profile["dimensions"]))
            assert obs.value == Decimal(row["portal_value"].replace(",", "."))
            assert obs.source_status == row["attributes"]
            assert obs.locator.column == "OBS_VALUE" and obs.locator.record >= 2
        assert not expected and calls.count("structure") == 1
    finally:
        await http.close()


async def test_structure_and_extra_filter_keep_codes_and_snapshot_continuation():
    http, browser, calls = fixture_browser()
    try:
        metadata = await query(browser, period=None, filters=None)
        assert [item["id"] for item in metadata.dimensions] == PROFILES[PRICE]["dimensions"]
        codes = await query(browser, period=None, filters=None, dimension="OZEL_KAPSAM_TUFE")
        assert any(set(item["labels"]) >= {"tr", "en"} for item in codes.codes)
        assert all(call == "structure" for call in calls)
        full = await query(browser, limit=1)
        assert full.total == 2 and full.next_offset == 1
        filtered = await query(browser, filters={"OZEL_KAPSAM_TUFE": ["GT_1"], "DEGISIM": ["1"]})
        assert filtered.total == 1 and filtered.constant_dimensions["DEGISIM"] == "1"
        second = await query(browser, offset=1, snapshot_id=full.snapshot_id)
        assert second.total == 2 and second.observations[0].locator.record == 3
        with pytest.raises(SourceError, match="Restart") as error:
            await query(browser, offset=1, snapshot_id="sha256:" + "a" * 64)
        assert error.value.code == "snapshot_changed"
        with pytest.raises(ValueError, match="invalid or unaudited"):
            await query(browser, filters={"OZEL_KAPSAM_TUFE": ["GT_1"], "DEGISIM": ["missing"]})
    finally:
        await http.close()


@pytest.mark.parametrize(
    "count,code",
    [
        ({"criteria": []}, "unknown_selection_count"),
        ({"obsCount": True}, "unknown_selection_count"),
        ({"obsCount": 201}, "selection_too_large"),
        ({"obsCount": 0}, "empty"),
    ],
)
async def test_preflight_refuses_or_reports_empty_before_csv(count, code):
    http, browser, calls = fixture_browser(count=count)
    try:
        if code == "empty":
            result = await query(browser)
            assert result.status == "empty" and result.complete and result.total == 0
        else:
            with pytest.raises(SourceError) as error:
                await query(browser)
            assert error.value.code == code
        assert calls == ["structure", "FREQ"]
    finally:
        await http.close()


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "upstream_auth_required"),
        (403, "upstream_forbidden"),
        (404, "upstream_not_found"),
        (429, "upstream_rate_limited"),
        ("timeout", "upstream_timeout"),
    ],
)
async def test_http_errors_are_distinct_and_do_not_echo_the_upstream_body(status, code):
    http, browser, _ = fixture_browser(failure=status)
    try:
        with pytest.raises(SourceError) as error:
            await query(browser)
        assert error.value.code == code and "redacted" not in str(error.value)
    finally:
        await http.close()


@pytest.mark.parametrize(
    "transform",
    [
        lambda rows: rows[:-1],
        lambda rows: [rows[0], rows[0]],
        lambda rows: [rows[0] | {"TIME_PERIOD": "2026-11"}, rows[1]],
    ],
)
async def test_partial_duplicate_or_out_of_scope_csv_is_refused(transform):
    http, browser, _ = fixture_browser(transform=transform)
    try:
        with pytest.raises(SourceError) as error:
            await query(browser)
        assert error.value.code == "upstream_contract"
    finally:
        await http.close()


async def test_response_budget_and_restrictive_privacy_flag():
    def changed(rows):
        return [rows[0] | {"CONF_STATUS": "C"}, rows[1]]

    http, browser, _ = fixture_browser(transform=changed, query_max_output_bytes=2400)
    try:
        result = await query(browser)
        first = result.observations[0]
        assert first.status == "confidential" and first.value is None and first.value_text == ""
        assert len(rendered(result.model_dump(mode="json"))) <= 2400
    finally:
        await http.close()


async def test_stdio_discovers_and_queries_the_opt_in_tool():
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).with_name("search_stdio_server.py")), "databrowser"],
    )
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as client:
        await client.initialize()
        tool = "query_tuik_dimensions"
        fixtures = (await client.call_tool(tool, {})).structuredContent["profiles"]
        assert len(fixtures) == 6
        profile = next(p for p in fixtures if p["resource_id"] == PRICE)
        result = await client.call_tool(
            tool,
            {
                "resource_id": PRICE,
                "period": profile["period"],
                "filters": profile["required_filters"],
            },
        )
        assert result.structuredContent["total"] == 2
        assert result.structuredContent["observations"][0]["value"] == "140.0811924821"
