"""Reviewed XLS/XLSX cells from exact workbook snapshots in the #86 audit."""

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from openpyxl.utils.cell import column_index_from_string, coordinate_from_string

from tuik_mcp.identities import file_keys
from tuik_mcp.models.observation import (
    Adjustment,
    Adjustments,
    Frequency,
    Measure,
    Observation,
    Period,
    RecipeSummary,
    SpreadsheetHeader,
    SpreadsheetLocator,
    Unit,
)
from tuik_mcp.parsers.limits import ParseLimits
from tuik_mcp.parsers.query import missing
from tuik_mcp.parsers.spreadsheet import header_text, load_sheets

from .geography import PROVINCIAL, geography
from .normalize import Normalized, SchemaChanged
from .recipes import EVIDENCE, MeasureRule

FREQUENCY_LABELS: dict[Frequency, str] = {"year": "Yıllık", "month": "Aylık"}


@dataclass(frozen=True)
class WorkbookCell:
    address: str
    measure_label: str
    measure: MeasureRule
    # Header cell and, when expanded, the merged range's physical anchor.
    headers: tuple[tuple[str, str | None], ...]
    dimensions: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkbookRecipe:
    id: str
    domain: str
    press: int
    title: str
    format: Literal["xls", "xlsx"]
    sha256: str
    sheet: str
    period: str
    frequency: Frequency
    cells: tuple[WorkbookCell, ...]
    place: str = "Türkiye"
    base_period: str | None = None
    calendar_adjustment: Adjustment = "unknown"
    note_cells: tuple[str, ...] = ()
    version: int = 1
    notes: tuple[str, ...] = (
        "Only the cells reviewed for this exact workbook snapshot are normalized.",
    )

    @property
    def resource_ids(self) -> tuple[str, ...]:
        (key,) = file_keys([(self.title, "t")])
        return (f"press:{self.press}:file:{key}",)

    def summary(self) -> RecipeSummary:
        return RecipeSummary(
            id=self.id,
            version=self.version,
            domain=self.domain,
            title=self.title,
            resource_ids=list(self.resource_ids),
            format=self.format,
            snapshot_id="sha256:" + self.sha256,
            example_period=self.period,
            evidence=EVIDENCE,
            measures=[cell.measure_label for cell in self.cells],
            example={"İstatistiksel Gösterge": self.cells[0].measure_label},
        )


WORKBOOK_RECIPES = (
    WorkbookRecipe(
        id="cpi-basket-2026",
        domain="prices",
        press=58291,
        title="Tüketici fiyat endeksi (TÜFE) mal ve hizmet sepeti ve ağırlıklar",
        format="xlsx",
        sha256="dac4ba4265b46e327dac76020f34223fbb34b761d1d31b7d1d48670a2b96de69",
        sheet="Tablo 4",
        period="2026",
        frequency="year",
        cells=(
            WorkbookCell(
                "D5",
                "Ağırlık (%)",
                MeasureRule("share", "percent"),
                (("A1", None), ("A3", None), ("D4", None)),
                {"COICOP 2018": "01", "Harcama grubu": "Gıda ve alkolsüz içecekler"},
            ),
        ),
    ),
    WorkbookRecipe(
        id="industrial-change-2026-09",
        domain="industry",
        press=58189,
        title="Bölümlerin Üst Grup Endeks Değişimine Etkisi (2021=100)",
        format="xls",
        sha256="6836ada659f9b05dd4dce065424033e27061e03956d48dac0822022ca176b26c",
        sheet="s2",
        period="2026-09",
        frequency="month",
        cells=(
            WorkbookCell(
                "C8",
                "Yıllık değişim (%)",
                MeasureRule("change", "percent", basis="same_period_previous_year"),
                (("A1", None), ("A3", None), ("C4", None)),
                {"NACE Rev. 2": "C", "Üst endeks": "Toplam sanayi"},
            ),
            WorkbookCell(
                "D8",
                "Yıllık değişime etki (yüzde puan)",
                MeasureRule("change", "percentage_points", basis="same_period_previous_year"),
                (("A1", None), ("A3", None), ("D4", None)),
                {"NACE Rev. 2": "C", "Üst endeks": "Toplam sanayi"},
            ),
        ),
        base_period="2021",
        calendar_adjustment="adjusted",
        note_cells=("A52",),
    ),
    WorkbookRecipe(
        id="population-2025",
        domain="population",
        press=53899,
        title="İl ve cinsiyete göre il/ilçe merkezi, belde/köy nüfusu ve nüfus yoğunluğu",
        format="xls",
        sha256="d498853835df0ae43bc0fc478592ae87a8a15d238b28d73e4d77f21b57a1d55e",
        sheet="t3",
        period="2025",
        frequency="year",
        cells=(
            WorkbookCell(
                "C11",
                "Nüfus",
                MeasureRule("level", "persons"),
                (("A1", None), ("C3", "B3"), ("C4", None)),
                {"Cinsiyet": "Erkek", "Yerleşim": "Toplam"},
            ),
        ),
        place="Ankara",
    ),
)
BY_WORKBOOK = {recipe.resource_ids[0]: recipe for recipe in WORKBOOK_RECIPES}


def normalize_workbook(
    recipe: WorkbookRecipe, body: bytes, digest: str, limits: ParseLimits
) -> Normalized:
    """Extract the reviewed cell set, preserving source coordinates and merge anchors."""
    sheet = next(
        (sheet for sheet in load_sheets(body, recipe.format, limits) if sheet.name == recipe.sheet),
        None,
    )
    if sheet is None:
        raise SchemaChanged([f"missing reviewed workbook sheet {recipe.sheet!r}"])

    def read_cell(address: str):
        col, row = coordinate_from_string(address)
        try:
            return sheet.rows[row - 1][column_index_from_string(col) - 1]
        except IndexError as exc:
            raise SchemaChanged([f"missing reviewed cell {sheet.name}!{address}"]) from exc

    notes = [header_text(read_cell(address)) for address in recipe.note_cells]
    label = FREQUENCY_LABELS[recipe.frequency]
    location = (
        geography(recipe.place, "TR", "national")
        if recipe.place == "Türkiye"
        else geography(recipe.place, *PROVINCIAL[recipe.place])
    )
    facets = [
        {
            "İstatistiksel Gösterge": cell.measure_label,
            "Coğrafi Kapsam": recipe.place,
            "Gözlem Sıklığı": label,
            **cell.dimensions,
        }
        for cell in recipe.cells
    ]
    distinct = {
        concept: list(dict.fromkeys(facet[concept] for facet in facets)) for concept in facets[0]
    }
    constant = {concept: values[0] for concept, values in distinct.items() if len(values) == 1}
    varying = {concept: values for concept, values in distinct.items() if len(values) > 1}

    observations = []
    for spec, facet in zip(recipe.cells, facets, strict=True):
        cell = read_cell(spec.address)
        value_text = "" if cell.empty else str(cell.value)
        number = Decimal(str(cell.number)) if cell.number is not None else None
        if number is not None and not number.is_finite():
            number = None
        status: Literal["valid", "missing", "invalid"] = (
            "valid" if number is not None else "missing" if missing(value_text) else "invalid"
        )
        seed = f"{recipe.resource_ids[0]}\0{digest}\0{sheet.name}\0{spec.address}"
        observations.append(
            Observation(
                id="obs:" + hashlib.sha256(seed.encode()).hexdigest(),
                value_text=value_text,
                value=number,
                status=status,
                reason=None if status == "valid" else "Reviewed cell has no usable number",
                locator=SpreadsheetLocator(
                    format=recipe.format,
                    sheet=sheet.name,
                    cell=spec.address,
                    headers=[
                        SpreadsheetHeader(
                            cell=address,
                            text=header_text(read_cell(anchor or address)),
                            merged_from=anchor,
                        )
                        for address, anchor in spec.headers
                    ],
                ),
                period=Period(
                    value=recipe.period,
                    frequency=recipe.frequency,
                    frequency_label=label,
                    source="recipe",
                ),
                geography=location,
                unit=Unit(
                    kind=spec.measure.unit,
                    multiplier=spec.measure.multiplier,
                    base_period=recipe.base_period,
                    source="recipe",
                ),
                measure=Measure(
                    type=spec.measure.type,
                    label=spec.measure_label,
                    basis=spec.measure.basis,
                    source="recipe",
                ),
                adjustment=Adjustments(
                    seasonal="unknown",
                    calendar=recipe.calendar_adjustment,
                    label=notes[0] if notes else None,
                    source="source" if notes else "unknown",
                ),
                dimensions={concept: facet[concept] for concept in varying},
                notes=notes,
            )
        )
    return Normalized({}, observations, facets, constant, varying, ("Gözlem Sıklığı",))
