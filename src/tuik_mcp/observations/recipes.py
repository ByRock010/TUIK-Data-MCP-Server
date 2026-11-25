"""Reviewed mapping recipes from audited TÜİK CSV families to observations (#87).

A recipe covers exact dataflow IDs and versions verified in the #86 source-table
audit. It lists every column concept it expects. A file with an extra or missing
concept is refused as `schema_changed`, and a label without an entry here is
`unmapped`. Nothing is inferred from a column name or label pattern.

Export headers sometimes append the first record's code, as in `Zaman (2015-01)`
or `Ölçü Birimi (TJ)`, and the same flow can drop it on another download.
Recipes therefore name concepts without that suffix.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from tuik_mcp.models.observation import (
    Adjustment,
    ChangeBasis,
    Frequency,
    GeographyLevel,
    MeasureType,
    RecipeSummary,
    UnavailableFamily,
    UnitKind,
)

from .geography import NATIONAL, PROVINCIAL

EVIDENCE = "docs/verification/issue-86 (2026-11-23)"
FREQUENCIES: Mapping[str, Frequency] = MappingProxyType(
    {"Aylık": "month", "Çeyreklik": "quarter", "Altı aylık": "half", "Yıllık": "year"}
)
STATUS_COLUMNS = ("Gözlem Durumu", "Gizlilik Durumu")
# SDMX attribute columns the reviewed families publish empty. A value would change
# the meaning of the observation (for example a unit multiplier), so it is refused.
EMPTY_ATTRIBUTES = (
    "Ondalık Basamak Sayısı",
    "Zaman Formatı",
    "Birim Çarpanı",
    "Zaman Serisi Dönüşümü",
)
RELEASED = frozenset({"", "Yayıma serbest"})
CONFIDENTIAL = frozenset({"Gizli veri"})


@dataclass(frozen=True)
class MeasureRule:
    type: MeasureType
    unit: UnitKind
    multiplier: int = 0
    basis: ChangeBasis | None = None


@dataclass(frozen=True)
class Recipe:
    id: str
    version: int
    domain: str
    title: str
    resource_ids: tuple[str, ...]
    value: str
    measure: str
    measures: Mapping[str, MeasureRule]
    dimensions: tuple[str, ...]
    example: Mapping[str, str]
    period: str = "Zaman"
    frequency: str = "Gözlem Sıklığı"
    geography: str = "Coğrafi Kapsam"
    geographies: Mapping[str, tuple[str, GeographyLevel]] = NATIONAL
    # Published file coverage when row geography is a separate dimension.
    coverage: str | None = None
    unit: str | None = "Ölçü Birimi"
    # Accepted published unit labels and the currency each one states.
    units: Mapping[str, str | None] = MappingProxyType({})
    base_period: str | None = None
    adjustment: str | None = None
    adjustments: Mapping[str, tuple[Adjustment, Adjustment]] = MappingProxyType({})
    # Seasonal and calendar adjustment declared by the recipe when no column says it.
    fixed_adjustment: tuple[Adjustment, Adjustment] = ("unknown", "unknown")
    # Dimensions that do not separate observations, with the reason in `notes`.
    identity_exclude: tuple[str, ...] = ()
    # TÜİK exports use semicolons; another delimiter fails the column check.
    delimiter: str = ";"
    decimal_separator: str = ","
    notes: tuple[str, ...] = ()

    def concepts(self) -> tuple[str, ...]:
        """Every non-attribute column concept the recipe requires."""
        named = (
            self.frequency,
            self.geography,
            self.coverage,
            self.unit,
            self.base_period,
            self.adjustment,
            self.measure,
            *self.dimensions,
            self.period,
            self.value,
        )
        return tuple(concept for concept in named if concept is not None)

    def summary(self) -> RecipeSummary:
        return RecipeSummary(
            id=self.id,
            version=self.version,
            domain=self.domain,
            title=self.title,
            resource_ids=list(self.resource_ids),
            evidence=EVIDENCE,
            measures=list(self.measures),
            example=dict(self.example),
        )


INDEX = MeasureRule("index", "index")
PERCENT_CHANGE = {
    "Bir önceki aya göre değişim oranı (%)": MeasureRule(
        "change", "percent", basis="previous_period"
    ),
    "Bir önceki yılın Aralık ayına göre değişim (%)": MeasureRule(
        "change", "percent", basis="previous_december"
    ),
    "Bir önceki yılın aynı ayına göre (yıllık) değişim oranı (%)": MeasureRule(
        "change", "percent", basis="same_period_previous_year"
    ),
    "Oniki aylık ortalamalara göre değişim (%)": MeasureRule(
        "change", "percent", basis="twelve_month_average"
    ),
}
TRADE_DIMENSIONS = (
    "Dış Ticaret Türü",
    "İstatistiksel Gösterge",
    "İktisadi Faaliyet",
    "Dış Ticaret Endeks Sektör Kodları",
)

RECIPES = (
    Recipe(
        id="cpi-main-groups",
        version=1,
        domain="prices",
        title="CPI main expenditure groups: index and change rates",
        resource_ids=("flow:DF_TUFE_SDMX_TT01+V1.0",),
        value="Gözlem Değeri",
        measure="Endeks-Değişim",
        measures={"Endeks": INDEX, **PERCENT_CHANGE},
        dimensions=(
            "Sınıflama Düzeyi",
            "COICOP 2018 (Amaca Göre Bireysel Tüketim Sınıflaması, 2018)",
        ),
        base_period="Temel Dönem/Yıl",
        # TT05 is the separately published seasonally adjusted CPI family.
        fixed_adjustment=("not_adjusted", "not_adjusted"),
        example={"Endeks-Değişim": "Endeks", "Sınıflama Düzeyi": "TÜFE"},
        notes=(
            "Seasonal and calendar adjustment are recipe-defined: the seasonally "
            "adjusted CPI is the separate DF_TUFE_SDMX_TT05 family.",
        ),
    ),
    Recipe(
        id="labour-monthly-core",
        version=1,
        domain="labour",
        title="Monthly core labour force indicators, national",
        resource_ids=("flow:DF_ISGUCU_AYLIK_TEMEL_ISGUCU_C+V1.0",),
        value="Gözlem Değeri",
        measure="İşgücü Göstergeleri",
        measures={
            "Nüfus (Bin Kişi)": MeasureRule("level", "persons", 3),
            "İşgücü (Bin Kişi)": MeasureRule("level", "persons", 3),
            "İstihdam (Bin Kişi)": MeasureRule("level", "persons", 3),
            "İşsiz (Bin Kişi)": MeasureRule("level", "persons", 3),
            "İşgücüne Dahil Olmayanlar (Bin Kişi)": MeasureRule("level", "persons", 3),
            "İşgücüne Katılma Oranı (%)": MeasureRule("rate", "percent"),
            "İstihdam Oranı (%)": MeasureRule("rate", "percent"),
            "İşsizlik Oranı (%)": MeasureRule("rate", "percent"),
        },
        dimensions=("İstatistiksel Gösterge", "Temel Yaş", "Cinsiyet"),
        adjustment="Mevsimsel Düzeltme",
        adjustments={
            "Mevsim etkilerinden arındırılmış": ("adjusted", "unknown"),
            "Arındırılmamış": ("not_adjusted", "unknown"),
        },
        example={
            "İşgücü Göstergeleri": "İşsizlik Oranı (%)",
            "Mevsimsel Düzeltme": "Mevsim etkilerinden arındırılmış",
            "Temel Yaş": "15+",
            "Cinsiyet": "Toplam",
        },
    ),
    Recipe(
        id="population-age-dependency",
        version=1,
        domain="population",
        title="Age dependency ratios by province of residence",
        resource_ids=("flow:DF_ADNKS_T24+V1.1",),
        value="Gözlem",
        measure="ADNKS Gösterge",
        measures={
            "Toplam yaş bağımlılık oranı(%)": MeasureRule("ratio", "percent"),
            "Çocuk bağımlılık oranı % (0-14 yaş)": MeasureRule("ratio", "percent"),
            "Yaşlı bağımlılık oranı % (65+ yaş)": MeasureRule("ratio", "percent"),
        },
        dimensions=("İstatistiksel Gösterge", "Yaş Grubu"),
        geography="İkamet edilen yer",
        geographies=PROVINCIAL,
        coverage="Coğrafi Kapsam",
        fixed_adjustment=("not_applicable", "not_applicable"),
        identity_exclude=("Yaş Grubu",),
        example={"ADNKS Gösterge": "Toplam yaş bağımlılık oranı(%)", "İkamet edilen yer": "Ankara"},
        notes=(
            "Yaş Grubu is `Toplam` in one year and `Uygulanabilir değil` in the others "
            "for the same ratios, so it does not separate observations. Duplicate "
            "periods are still refused.",
            "Province codes are recipe-defined from the published province names.",
        ),
    ),
    Recipe(
        id="trade-terms-gold-excluded",
        version=1,
        domain="foreign_trade",
        title="Terms of trade excluding gold",
        resource_ids=("flow:DF_ALTINSIZ_DTH+V1.0",),
        value="Gözlem Değeri",
        measure="Dış Ticaret Göstergeleri",
        measures={"Altın Hariç Dış Ticaret Hadleri": INDEX},
        dimensions=TRADE_DIMENSIONS,
        units={"Oran": None},
        base_period="Temel Dönem/Yıl",
        example={
            "Gözlem Sıklığı": "Aylık",
            "Dış Ticaret Endeks Sektör Kodları": "0 Canlı hayvanlar ve gıda maddeleri",
            "İktisadi Faaliyet": "Gıda ve canlı hayvanlar",
        },
        notes=(
            "One file mixes monthly, quarterly and annual observations; select "
            "Gözlem Sıklığı before comparing periods.",
        ),
    ),
    Recipe(
        id="trade-export-unit-value-gold-excluded",
        version=1,
        domain="foreign_trade",
        title="Export unit value index excluding gold, US dollar basis",
        resource_ids=("flow:DF_ALTINSIZ_IHRACAT_BDE+V1.0",),
        value="Gözlem Değeri",
        measure="Endeks-Değişim",
        measures={"Endeks": INDEX},
        dimensions=("Dış Ticaret Göstergeleri", *TRADE_DIMENSIONS),
        units={"ABD doları": "USD"},
        base_period="Temel Dönem/Yıl",
        example={
            "Gözlem Sıklığı": "Yıllık",
            "Dış Ticaret Endeks Sektör Kodları": "T GENEL ENDEKS",
        },
        notes=(
            "Values are an index of US dollar unit values, not dollar amounts.",
            "One file mixes monthly, quarterly and annual observations; select "
            "Gözlem Sıklığı before comparing periods.",
        ),
    ),
    Recipe(
        id="industry-final-energy-tj",
        version=1,
        domain="industry",
        title="Annual final energy consumption in industry, terajoules",
        resource_ids=("flow:DF_SANAYI_ENERJI_YILLIK_NIHAI_TUKETIM_TJ+V1.0",),
        value="Gözlem Değeri",
        measure="Enerji Gösterge",
        measures={
            "Sanayi sektöründe kullanılan enerji kaynaklarının yıllık toplam nihai "
            "enerji tüketim miktarları (TJ)": MeasureRule("level", "terajoule"),
        },
        dimensions=(
            "İstatistiksel Gösterge",
            "Enerji Kaynak",
            "Bölüm ve gruplar",
            "NACE Rev. 2, Kısım",
        ),
        units={"Terajul": None},
        fixed_adjustment=("not_applicable", "not_applicable"),
        example={"Enerji Kaynak": "Doğal Gaz", "NACE Rev. 2, Kısım": "Toplam"},
        notes=(
            "A published 0 is a true zero or less than half the unit; it stays a "
            "valid 0. Confidential cells are empty and never become zero.",
        ),
    ),
)

UNAVAILABLE = (
    UnavailableFamily(
        domain="housing_sales",
        flows=[
            "flow:DF_SATIS_SEKLI_SATIS_DURUMU_V3",
            "flow:DF_YABANCILARA_SATIS_V3",
            "flow:DF_UYRUKLARA_GORE_SATISLAR_V3",
            "flow:DF_CINSIYETE_GORE_SATISLAR_V3",
        ],
        reason=(
            "Held by the #86 audit: dwelling/workplace classification, count versus "
            "share, geography and vintage are not verified."
        ),
    ),
)

BY_RESOURCE = {resource: recipe for recipe in RECIPES for resource in recipe.resource_ids}


def unavailable(resource_id: str) -> UnavailableFamily | None:
    """Held families match every version of the flow."""
    flow = resource_id.split("+", 1)[0]
    return next((f for f in UNAVAILABLE if flow in f.flows), None)
