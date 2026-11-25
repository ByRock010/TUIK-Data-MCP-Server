"""Reviewed version-one mappings for the #86 audit. Select one exact series."""

from dataclasses import dataclass, field
from typing import Literal

from tuik_mcp.identities import file_keys
from tuik_mcp.models.observation import (
    Adjustment,
    Frequency,
    Geography,
    MappingDescription,
    MappingDiscovery,
    Measure,
    SeriesDescription,
)

NATIONAL = Geography(code="TR", label="Türkiye", level="national", system="ISO 3166-1")
ANKARA = Geography(
    code="TR510", label="Ankara", level="province", system="İBBS", vintage="İBBS 2005, edition 164"
)
PERSONS = Measure(kind="level", unit="person", multiplier=1)
THOUSAND_PERSONS = Measure(kind="level", unit="person", multiplier=1000)
PERCENT = Measure(kind="rate", unit="percent", multiplier=1)
CPI_INDEX = Measure(kind="index", unit="index", multiplier=1, base_year=2025)
CPI_CHANGE = PERCENT.model_copy(update={"base_year": 2025})
TRADE_RATIO = Measure(kind="rate", unit="ratio", multiplier=1, base_year=2015)
ENERGY = Measure(kind="level", unit="terajoule", multiplier=1)


@dataclass(frozen=True)
class Series:
    frequency: Frequency
    measure: Measure
    example: str
    selectors: dict[str, str]
    geography: Geography = NATIONAL
    adjustment: Adjustment | None = None
    # Reviewed workbook coordinates are one-based. Anchors document source merges.
    row: int | None = None
    column: int | None = None
    headers: tuple[tuple[str, str | None], ...] = ()
    note_cells: tuple[str, ...] = ()


@dataclass(frozen=True)
class Mapping:
    id: str
    domain: str
    resource_id: str
    format: Literal["csv", "xls", "xlsx"]
    series: dict[str, Series]
    period: str = "Zaman"
    value: str = "Gözlem Değeri"
    # Exact source-declared values from workbook families or CSV attributes.
    constants: dict[str, str] = field(default_factory=dict)
    snapshot_sha256: str | None = None
    sheet: str | None = None

    def describe(self) -> MappingDescription:
        return MappingDescription(
            id=self.id,
            domain=self.domain,
            resource_id=self.resource_id,
            format=self.format,
            required_snapshot_sha256=self.snapshot_sha256,
            series=[
                SeriesDescription(
                    id=key,
                    frequency=spec.frequency,
                    measure=spec.measure,
                    geography=spec.geography,
                    adjustment=spec.adjustment,
                    example_period=spec.example,
                    selectors={**self.constants, **spec.selectors},
                )
                for key, spec in self.series.items()
            ],
        )


def workbook_id(press: int, title: str) -> str:
    key = file_keys([(title, "t")])[0]
    return f"press:{press}:file:{key}"


CPI_BASKET_TITLE = "Tüketici fiyat endeksi (TÜFE) mal ve hizmet sepeti ve ağırlıklar"
POPULATION_TITLE = "İl ve cinsiyete göre il/ilçe merkezi, belde/köy nüfusu ve nüfus yoğunluğu"
INDUSTRY_TITLE = "Bölümlerin Üst Grup Endeks Değişimine Etkisi (2021=100)"

MAPPINGS = {
    spec.id: spec
    for spec in (
        Mapping(
            "cpi",
            "prices",
            "flow:DF_TUFE_SDMX_TT01+V1.0",
            "csv",
            {
                key: Series("month", measure, "2026-10", {"Endeks-Değişim (1)": indicator})
                for key, measure, indicator in (
                    ("index", CPI_INDEX, "Endeks"),
                    ("monthly-change", CPI_CHANGE, "Bir önceki aya göre değişim oranı (%)"),
                    (
                        "annual-change",
                        CPI_CHANGE,
                        "Bir önceki yılın aynı ayına göre (yıllık) değişim oranı (%)",
                    ),
                )
            },
            period="Zaman (2005-01)",
            constants={
                "Coğrafi Kapsam (TR)": "Türkiye",
                "Gözlem Sıklığı (M)": "Aylık",
                "Sınıflama Düzeyi (TUFE)": "TÜFE",
                "Temel Dönem/Yıl (2025)": "2025",
                "COICOP 2018 (Amaca Göre Bireysel Tüketim Sınıflaması, 2018) (0)": "TÜFE",
            },
        ),
        Mapping(
            "labour",
            "labour",
            "flow:DF_ISGUCU_AYLIK_TEMEL_ISGUCU_C+V1.0",
            "csv",
            {
                f"{key}-{suffix}": Series(
                    "month",
                    measure,
                    "2026-09",
                    {"Mevsimsel Düzeltme": label, "İşgücü Göstergeleri": indicator},
                    adjustment=adjustment,
                )
                for key, measure, indicator in (
                    ("unemployment-rate", PERCENT, "İşsizlik Oranı (%)"),
                    ("employment", THOUSAND_PERSONS, "İstihdam (Bin Kişi)"),
                )
                for suffix, label, adjustment in (
                    ("original", "Arındırılmamış", "unadjusted"),
                    ("adjusted", "Mevsim etkilerinden arındırılmış", "seasonal"),
                )
            },
            constants={
                "Coğrafi Kapsam": "Türkiye",
                "Gözlem Sıklığı": "Aylık",
                "İstatistiksel Gösterge": "Temel İşgücü Göstergeleri",
                "Temel Yaş": "15+",
                "Cinsiyet": "Toplam",
            },
        ),
        Mapping(
            "age-dependency",
            "population",
            "flow:DF_ADNKS_T24+V1.1",
            "csv",
            {
                "national": Series("year", PERCENT, "2025", {"İkamet edilen yer": "Toplam"}),
                "ankara": Series(
                    "year", PERCENT, "2025", {"İkamet edilen yer": "Ankara"}, geography=ANKARA
                ),
            },
            value="Gözlem",
            constants={
                "Coğrafi Kapsam": "Türkiye",
                "Gözlem Sıklığı": "Yıllık",
                "İstatistiksel Gösterge": "Adrese Dayalı Nüfus Kayıt Sistemi",
                "ADNKS Gösterge": "Toplam yaş bağımlılık oranı(%)",
                "Yaş Grubu": "Toplam",
            },
        ),
        Mapping(
            "trade-terms",
            "foreign-trade",
            "flow:DF_ALTINSIZ_DTH+V1.0",
            "csv",
            {
                key: Series(frequency, TRADE_RATIO, period, {"Gözlem Sıklığı": label})
                for key, frequency, period, label in (
                    ("monthly", "month", "2025-12", "Aylık"),
                    ("quarterly", "quarter", "2025-Q4", "Çeyreklik"),
                    ("annual", "year", "2025", "Yıllık"),
                )
            },
            constants={
                "Coğrafi Kapsam": "Türkiye",
                "Ölçü Birimi": "Oran",
                "Dış Ticaret Göstergeleri": "Altın Hariç Dış Ticaret Hadleri",
                "Dış Ticaret Türü": "Diğer",
                "İstatistiksel Gösterge": "Dış Ticaret Endeksleri",
                "İktisadi Faaliyet": "-",
                "Dış Ticaret Endeks Sektör Kodları": "T GENEL ENDEKS",
                "Temel Dönem/Yıl": "2015",
            },
        ),
        Mapping(
            "industrial-energy",
            "industry",
            "flow:DF_SANAYI_ENERJI_YILLIK_NIHAI_TUKETIM_TJ+V1.0",
            "csv",
            {"annual-total": Series("year", ENERGY, "2025", {})},
            period="Zaman (2024)",
            constants={
                "Gözlem Sıklığı": "Yıllık",
                "Coğrafi Kapsam": "Türkiye",
                "İstatistiksel Gösterge (E_SSNETI)": (
                    "Sanayi Sektörü Nihai Enerji Tüketim İstatistikleri"
                ),
                "Ölçü Birimi (TJ)": "Terajul",
                "Enerji Gösterge (SAN_ENERJI_TJ)": (
                    "Sanayi sektöründe kullanılan enerji kaynaklarının yıllık toplam nihai "
                    "enerji tüketim miktarları (TJ)"
                ),
                "Enerji Kaynak (T)": "Toplam",
                "Bölüm ve gruplar (B07)": "Toplam",
                "NACE Rev. 2, Kısım (B)": "Toplam",
            },
        ),
        Mapping(
            "cpi-basket-2026",
            "prices",
            workbook_id(58291, CPI_BASKET_TITLE),
            "xlsx",
            {
                "food": Series(
                    "year",
                    PERCENT,
                    "2026",
                    {"COICOP 2018": "01", "group": "Gıda ve alkolsüz içecekler"},
                    row=5,
                    column=4,
                    headers=(("A1", None), ("A3", None), ("D4", None)),
                )
            },
            snapshot_sha256="dac4ba4265b46e327dac76020f34223fbb34b761d1d31b7d1d48670a2b96de69",
            sheet="Tablo 4",
        ),
        Mapping(
            "industrial-change-2026-09",
            "industry",
            workbook_id(58189, INDUSTRY_TITLE),
            "xls",
            {
                key: Series(
                    "month",
                    Measure(kind="rate", unit=unit, multiplier=1, base_year=2021),
                    "2026-09",
                    {"NACE Rev. 2": "C", "Upper index": "Toplam sanayi", "Comparison": label},
                    adjustment="calendar",
                    row=8,
                    column=column,
                    headers=(("A1", None), ("A3", None), (f"{letter}4", None)),
                    note_cells=("A52",),
                )
                for key, unit, label, column, letter in (
                    ("manufacturing-yoy-rate", "percent", "Annual change", 3, "C"),
                    (
                        "manufacturing-yoy-contribution",
                        "percentage_point",
                        "Annual contribution",
                        4,
                        "D",
                    ),
                )
            },
            snapshot_sha256="6836ada659f9b05dd4dce065424033e27061e03956d48dac0822022ca176b26c",
            sheet="s2",
        ),
        Mapping(
            "population-2025",
            "population",
            workbook_id(53899, POPULATION_TITLE),
            "xls",
            {
                "ankara-men": Series(
                    "year",
                    PERSONS,
                    "2025",
                    {"İl": "Ankara", "Cinsiyet": "Erkek", "Yerleşim": "Toplam"},
                    geography=ANKARA,
                    row=11,
                    column=3,
                    headers=(("A1", None), ("C3", "B3"), ("C4", None)),
                ),
            },
            snapshot_sha256="d498853835df0ae43bc0fc478592ae87a8a15d238b28d73e4d77f21b57a1d55e",
            sheet="t3",
        ),
    )
}

UNAVAILABLE = {
    "housing": "Area, dwelling/workplace, count/share and vintage gates remain unresolved (#86).",
}


def describe(mapping: Mapping | None = None) -> MappingDiscovery:
    return MappingDiscovery(
        mappings=[
            item.describe() for item in (MAPPINGS.values() if mapping is None else [mapping])
        ],
        unavailable_domains=UNAVAILABLE,
    )
