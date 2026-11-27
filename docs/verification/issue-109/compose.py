"""Replay the CPI and USD/TRY public snapshots without a network or credentials."""

import argparse
import json
import re
import sys
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

MAX_INPUT_BYTES = 32 * 1024
MAX_MONTHS = 12
CPI_ID = "flow:DF_TUFE_SDMX_TT01+V1.0"
CPI_URL = "https://veriportali.tuik.gov.tr/api/tr/dataflows/DF_TUFE_SDMX_TT01%2BV1.0/file/csv"
FX_ID = "TP.DK.USD.A.YTL"
TCMB_TERMS = "https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB%2BTR/Bottom%2BMenu/Diger"

Month = Annotated[str, Field(pattern=r"^[1-9][0-9]{3}-(0[1-9]|1[0-2])$")]


class PilotModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Observation(PilotModel):
    id: str = Field(min_length=1, max_length=90)
    period: Month
    source_date: date | None
    value_text: str = Field(pattern=r"^[0-9]{1,12}([.,][0-9]{1,12})?$")
    source_url: str = Field(max_length=160)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    retrieved_at: AwareDatetime
    locator: str = Field(min_length=1, max_length=100)


class Series(PilotModel):
    publisher: Literal["TÜİK", "TCMB"]
    redistributor: None
    series_id: str
    channel: Literal["tuik_public_csv", "tcmb_public_daily_xml"]
    source_terms: str
    frequency: Literal["monthly"]
    unit: str
    currency: str | None
    index_base: str | None
    seasonal_adjustment: str
    calendar_adjustment: str
    aggregation: str
    decimal_separator: Literal[".", ","]
    observations: list[Observation] = Field(min_length=2, max_length=MAX_MONTHS)

    def records(self) -> dict[str, Observation]:
        by_period = {observation.period: observation for observation in self.observations}
        if len(by_period) != len(self.observations):
            raise ValueError("Select one explicit observation vintage per series and month")
        if len({o.id for o in self.observations}) != len(by_period):
            raise ValueError("Source observation IDs must be unique within each series")
        for observation in self.observations:
            separator = r"\." if self.decimal_separator == "." else ","
            if (
                re.fullmatch(rf"[0-9]{{1,12}}(?:{separator}[0-9]{{1,12}})?", observation.value_text)
                is None
            ):
                raise ValueError("Observation does not match the stated decimal notation")
            if Decimal(observation.value_text.replace(",", ".")) <= 0:
                raise ValueError("Index levels and exchange rates must be positive")
        return by_period


class PilotInput(PilotModel):
    schema_version: Literal["tuik.evds-pilot-input/1"]
    base_month: Month
    cpi: Series
    usd_try: Series

    @model_validator(mode="after")
    def validate_profiles(self):
        cpi = self.cpi
        fx = self.usd_try
        select_fields = {
            "publisher",
            "series_id",
            "channel",
            "source_terms",
            "unit",
            "currency",
            "index_base",
            "seasonal_adjustment",
            "calendar_adjustment",
            "aggregation",
            "decimal_separator",
        }
        if cpi.model_dump(include=select_fields) != {
            "publisher": "TÜİK",
            "series_id": CPI_ID,
            "channel": "tuik_public_csv",
            "source_terms": "https://www.tuik.gov.tr/Kurumsal/Yasal_Uyari",
            "unit": "index",
            "currency": None,
            "index_base": "2025=100",
            "seasonal_adjustment": "original",
            "calendar_adjustment": "original",
            "aggregation": "published_month",
            "decimal_separator": ",",
        }:
            raise ValueError("Select the original monthly TÜİK headline CPI level (2025=100)")
        if fx.model_dump(include=select_fields) != {
            "publisher": "TCMB",
            "series_id": FX_ID,
            "channel": "tcmb_public_daily_xml",
            "source_terms": TCMB_TERMS,
            "unit": "TRY per USD",
            "currency": "TRY/USD",
            "index_base": None,
            "seasonal_adjustment": "not_applicable",
            "calendar_adjustment": "not_applicable",
            "aggregation": "last_published_business_day",
            "decimal_separator": ".",
        }:
            raise ValueError("Select the TCMB USD buying rate in TRY per USD at month end")

        for observation in cpi.observations:
            if observation.source_date is not None or observation.source_url != CPI_URL:
                raise ValueError("CPI observations must cite the approved TÜİK CSV")
        for observation in fx.observations:
            day = observation.source_date
            if day is None or day.isoformat()[:7] != observation.period:
                raise ValueError("The exchange-rate source date must be in its matched month")
            if (
                observation.source_url
                != f"https://www.tcmb.gov.tr/kurlar/{day:%Y%m}/{day:%d%m%Y}.xml"
            ):
                raise ValueError("Exchange rates must cite the documented TCMB daily source")
        return self


def rebase(observation: Observation, reference: Observation) -> dict:
    current = Decimal(observation.value_text.replace(",", "."))
    base = Decimal(reference.value_text.replace(",", "."))
    with localcontext(prec=60, rounding=ROUND_HALF_EVEN):
        index = (current * 100 / base).quantize(Decimal("0.0001"))
    return {"index": str(index), "observation_id": observation.id, "base_id": reference.id}


def compose(payload: PilotInput) -> dict:
    cpi = payload.cpi.records()
    fx = payload.usd_try.records()
    if cpi.keys() != fx.keys():
        raise ValueError(
            "Report months differ: source observations were not filled or interpolated"
        )
    months = sorted(cpi)
    if payload.base_month != months[0]:
        raise ValueError("Use the first matched month as the normalization base")
    ordinals = [int(month[:4]) * 12 + int(month[5:]) for month in months]
    if ordinals[-1] - ordinals[0] + 1 != len(months):
        raise ValueError("A complete consecutive month window is required")
    base = payload.base_month
    return {
        "schema_version": "tuik.evds-pilot-evidence/1",
        "method": {
            "frequency": "monthly",
            "exchange_rate_sampling": "Use the recorded last published business day of each month",
            "normalization": "Normalize each series separately: 100 × value / its base-month value",
            "base_month": base,
            "rounding": "half-even, four decimal places",
            "interpretation": "Parallel nominal movements of two distinct measures.",
        },
        "sources": {
            "cpi": payload.cpi.model_dump(mode="json"),
            "usd_try": payload.usd_try.model_dump(mode="json"),
        },
        "months": [
            {
                "period": month,
                "cpi": rebase(cpi[month], cpi[base]),
                "usd_try": rebase(fx[month], fx[base]),
            }
            for month in months
        ],
    }


def distinct_keys(pairs: list[tuple[str, object]]) -> dict:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate JSON field")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "fixture", nargs="?", type=Path, default=Path(__file__).with_name("2026-q1.json")
    )
    args = parser.parse_args()
    try:
        with args.fixture.open("rb") as file:
            data = file.read(MAX_INPUT_BYTES + 1)
        if len(data) > MAX_INPUT_BYTES:
            raise ValueError("Fixture exceeds 32 KiB")
        evidence = compose(
            PilotInput.model_validate(json.loads(data, object_pairs_hook=distinct_keys))
        )
    except (OSError, ValueError) as exc:
        # Do not echo raw external inputs or strings from validation errors.
        print(
            f"Pilot evidence refused ({type(exc).__name__}). Check the documented input profile.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
