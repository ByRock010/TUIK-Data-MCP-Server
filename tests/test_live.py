import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest

from tuik_mcp.clients.base import HttpClient
from tuik_mcp.config import Settings
from tuik_mcp.observations import RECIPES
from tuik_mcp.observations.workbooks import WORKBOOK_RECIPES
from tuik_mcp.services.catalog import CatalogService


@pytest.mark.live
async def test_live_bulletin_related_datasets_to_csv():
    http = HttpClient(Settings())
    try:
        service = CatalogService(http)
        resource = await service.resource("press:58347")
        related = [item for item in resource.related_datasets if item.downloadable]
        # Since November 2026 the portal links flows without the optional
        # categories/ segment. Classification comes from the export catalog.
        assert related and all(item.category_path in ([], ["9", "9_4"]) for item in related)
        assert (await service.resource(related[0].id)).theme
        response = await service.data(resource.id)
        assert response.status == "related_datasets"
        data = await service.data(related[0].id, limit=2)
        assert data.status == "ok" and data.rows
        assert data.source_url.endswith("/file/csv")
    finally:
        await http.close()


@pytest.mark.live
async def test_live_three_level_bulletin_related_datasets():
    http = HttpClient(Settings())
    try:
        service = CatalogService(http)
        resource = await service.resource("press:57954")
        related = [item for item in resource.related_datasets if item.downloadable]
        assert len(related) == 2
        # A category path is optional upstream. When present it must be complete.
        assert all(item.category_path in ([], ["8", "8_1", "8_1_4"]) for item in related)
        assert (await service.data(resource.id)).status == "related_datasets"
        for item in related:
            # These exports can exceed the default 10 MiB response bound.
            # Resolve their catalog metadata; the other live test checks CSV rows.
            flow = await service.resource(item.id)
            assert flow.theme
            assert any(link.format == "csv" for link in flow.download_links)
    finally:
        await http.close()


@pytest.mark.live
async def test_live_mcp_workflow():
    script = Path(__file__).parents[1] / "scripts" / "live_smoke.py"
    spec = importlib.util.spec_from_file_location("live_smoke", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = await module.run()
    assert set(report["tools"]) == {
        "chart_tuik_data",
        "search_tuik",
        "search_tuik_datasets",
        "get_tuik_resource",
        "get_tuik_data",
        "inspect_tuik_data",
        "list_tuik_themes",
        "read_tuik_document",
        "search_tuik_classifications",
        "get_tuik_observations",
        "calculate_tuik_series",
        "build_tuik_report",
        "export_tuik_workbook",
    }
    for key, fmt in [("inspect_csv", {"csv"}), ("inspect_excel", {"xls", "xlsx"})]:
        inspected = report[key]
        assert inspected["format"] in fmt and inspected["matched_rows"] >= 1
        assert inspected["snapshot_id"] == inspected["query_snapshot_id"]
    assert report["bulletin_csv"]["selection_status"] == "related_datasets"
    assert report["bulletin_csv"]["format"] == "csv"
    assert report["bulletin_excel"]["data"]["extraction"] in {"xls", "xlsx"}
    assert report["publication"]["status"] == "ok"
    assert report["publication"]["text"]


@pytest.mark.live
async def test_live_back_to_back_bulletin_downloads():
    http = HttpClient(Settings())
    try:
        service = CatalogService(http)
        search = await service.search("Tüketici Fiyat Endeksi")
        for result in search.results:
            if result.resource_type == "bulletin":
                resource = await service.resource(result.id)
                links = [link for link in resource.download_links if link.format == "xls"][:5]
                if len(links) >= 3:
                    break
        else:
            pytest.fail("No bulletin with at least three spreadsheet downloads was found")
        for link in links:
            # A throttle page raises; some links advertised as XLS deliver XLSX archives.
            body, verified = await service.portal.download(link)
            assert verified.verified and body
    finally:
        await http.close()


@pytest.mark.live
async def test_live_cpi_spreadsheet_and_xls_only_bulletin_rows():
    http = HttpClient(Settings.from_env())
    try:
        service = CatalogService(http)
        resource = await service.resource("press:58298")
        link = next(
            link
            for link in resource.download_links
            if link.title == "Tüketici fiyat endeksi ve değişim oranları"
        )
        data = await service.data(link.resource_id, limit=2)
        assert data.status == "ok" and len(data.rows) == 2
        assert data.unit == "2025=100" and data.sheet and data.table_title
        assert data.extraction == "xls" and data.total_rows >= 22
        before = data.retrieved_at
        assert (await service.data(link.resource_id, limit=1)).retrieved_at == before
        # #6's old press:54072 example advertises a first file that now returns 404.
        # This current XLS-only population bulletin exercises the same direct-bulletin path.
        population = await service.data("press:53899", limit=2)
        assert population.status == "ok" and population.rows and population.extraction == "xls"
        assert population.total_rows == 82
        labour = await service.resource("press:57987")
        file = next(
            link
            for link in labour.download_links
            if link.title == "Temel işgücü göstergeleri (15+ yaş)"
        )
        rows = await service.data(file.resource_id, limit=2)
        assert rows.status == "ok" and rows.rows and rows.unit == "15+ yaş - age"
    finally:
        await http.close()


@pytest.mark.live
async def test_live_observation_recipes_still_match_their_sources():
    """Every reviewed recipe maps the whole current export without failures (#87)."""
    http = HttpClient(Settings.from_env())
    try:
        service = CatalogService(http)
        for recipe in RECIPES:
            (resource_id,) = recipe.resource_ids
            result = await service.observations(resource_id, filters=dict(recipe.example))
            assert result.status == "ok", (resource_id, result.issues)
            assert result.complete and result.counts.selected > 0, resource_id
            whole = await service.observations(resource_id, limit=1)
            counts = whole.counts
            assert counts.unmapped == counts.invalid == counts.duplicate == 0, (resource_id, counts)
            assert counts.valid + counts.missing + counts.confidential == counts.source_records
    finally:
        await http.close()


@pytest.mark.live
async def test_live_cpi_year_ago_change_reproduces_the_published_annual_rate():
    """The calendar-matched percent change of the index equals TÜİK's own rate (#89)."""
    http = HttpClient(Settings.from_env())
    try:
        service = CatalogService(http)
        resource_id = "flow:DF_TUFE_SDMX_TT01+V1.0"
        total = {"COICOP 2018 (Amaca Göre Bireysel Tüketim Sınıflaması, 2018)": "TÜFE"}
        computed = await service.calculate(
            resource_id,
            "percent_change",
            filters={**total, "Endeks-Değişim": "Endeks"},
            basis="same_period_previous_year",
            period_from="2025-01",
        )
        rate = "Bir önceki yılın aynı ayına göre (yıllık) değişim oranı (%)"
        published = await service.observations(
            resource_id,
            filters={**total, "Endeks-Değişim": rate},
            period_from="2025-01",
            limit=200,
        )
        official = {o.period.value: o.value for o in published.observations if o.value}
        compared = [r for r in computed.series[0].results if r.period in official]
        assert len(compared) >= 12 and all(r.status == "valid" for r in compared)
        # TÜİK publishes the rate to two decimals; the derived value rounds to it.
        assert all(Decimal(r.rounded) == official[r.period] for r in compared), [
            (r.period, r.rounded, official[r.period]) for r in compared
        ]
    finally:
        await http.close()


@pytest.mark.live
async def test_live_reviewed_workbook_observation_cells():
    expected = {
        "cpi-basket-2026": {"D5": (Decimal("24.4444"), "percent")},
        "population-2025": {"C11": (Decimal("2911853"), "persons")},
        "industrial-change-2026-09": {
            "C8": (Decimal("2.58966869988598"), "percent"),
            "D8": (Decimal("2.28287426407597"), "percentage_points"),
        },
    }
    http = HttpClient(Settings.from_env())
    try:
        service = CatalogService(http)
        for recipe in WORKBOOK_RECIPES:
            (resource_id,) = recipe.resource_ids
            result = await service.observations(
                resource_id, period_from=recipe.period, period_to=recipe.period
            )
            assert result.status == "ok", (resource_id, result.issues)
            assert result.source_scope == "reviewed_cells" and result.coverage.complete
            assert result.publication_date and result.snapshot_id == "sha256:" + recipe.sha256
            assert {
                row.locator.cell: (row.value, row.unit.kind) for row in result.observations
            } == expected[recipe.id]
            if recipe.id == "population-2025":
                (row,) = result.observations
                assert row.locator.headers[1].merged_from == "B3"
    finally:
        await http.close()


@pytest.mark.live
async def test_live_report_binds_cpi_values_and_verifies_its_narrative():
    """A Turkish CPI report with host narrative passes the claim checks (#91)."""
    http = HttpClient(Settings.from_env())
    try:
        service = CatalogService(http)
        resource_id = "flow:DF_TUFE_SDMX_TT01+V1.0"
        total = {"COICOP 2018 (Amaca Göre Bireysel Tüketim Sınıflaması, 2018)": "TÜFE"}
        annual = {
            **total,
            "Endeks-Değişim": "Bir önceki yılın aynı ayına göre (yıllık) değişim oranı (%)",
        }
        period = max(
            o.period.value
            for o in (
                await service.observations(
                    resource_id, filters=annual, period_from="2025-01", limit=200
                )
            ).observations
            if o.status == "valid"
        )
        evidence = [
            {
                "kind": "observation",
                "key": "published",
                "resource_id": resource_id,
                "filters": annual,
                "period": period,
                "decimals": 2,
            },
            {
                "kind": "calculation",
                "key": "derived",
                "resource_id": resource_id,
                "filters": {**total, "Endeks-Değişim": "Endeks"},
                "operation": "percent_change",
                "basis": "same_period_previous_year",
                "period": period,
            },
        ]
        draft = await service.report(
            {
                "title": "TÜFE özeti",
                "evidence": evidence,
                "sections": [{"kind": "summary", "findings": [{"evidence": ["published"]}]}],
            }
        )
        value = next(e for e in draft.evidence if e.key == "published").display
        report = await service.report(
            {
                "title": "TÜFE özeti",
                "evidence": evidence,
                "sections": [
                    {
                        "kind": "summary",
                        "findings": [
                            {"evidence": ["published", "derived"]},
                            {
                                "evidence": ["published"],
                                "text": f"Yıllık enflasyon {period} döneminde {value} oldu.",
                                "claims": [{"evidence": "published", "quote": value}],
                            },
                        ],
                    }
                ],
            }
        )
        assert report.status == "ok", [s.findings for s in report.sections]
        published, derived = report.evidence
        assert published.id.startswith("obs:") and derived.id.startswith("calc:")
        # The derived year-ago change rounds to TÜİK's own published rate.
        assert derived.rounded == published.rounded
        assert report.artifact and report.timeline.latest_observation == period
    finally:
        await http.close()
