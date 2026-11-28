"""Resolve report evidence, check narrative claims and save the report bundle (#91)."""

import hashlib
import json
from calendar import monthrange
from datetime import date
from decimal import Decimal

from tuik_mcp.artifacts import ArtifactInput, PendingFile
from tuik_mcp.calculations import PERIODS_PER_YEAR, rounded
from tuik_mcp.clients.base import SourceError
from tuik_mcp.config import PORTAL
from tuik_mcp.models.calculation import MeasureSpec, UnitSpec
from tuik_mcp.models.observation import Observation
from tuik_mcp.models.report import (
    GENERATED,
    MAX_SERIES_POINTS,
    Adjustment,
    CalculationEvidenceSpec,
    Chart,
    ChartLine,
    Evidence,
    EvidenceSource,
    Finding,
    ObservationEvidenceSpec,
    Point,
    Report,
    ReportCounts,
    ReportSpec,
    Section,
    SectionSpec,
    SeriesEvidenceSpec,
    Table,
    TableRow,
    Timeline,
)
from tuik_mcp.observations import BY_RESOURCE, SchemaChanged, unavailable
from tuik_mcp.observations.workbooks import BY_WORKBOOK, WorkbookRecipe
from tuik_mcp.reports.claims import Fact, check_text, geography_terms
from tuik_mcp.reports.numbers import display, unit_label
from tuik_mcp.reports.render import markdown
from tuik_mcp.reports.templates import TEXT, finding_text
from tuik_mcp.services.calculations import SOURCE_TYPES, csv_bytes, single
from tuik_mcp.services.observations import (
    MAX_LISTED_VALUES,
    ObservationService,
    ordinal,
    period_frequency,
    period_label,
)
from tuik_mcp.utils.normalization import validate_id
from tuik_mcp.utils.rendering import rendered

OPERATIONS = {
    "tr": {
        "difference": "fark",
        "percent_change": "yüzde değişim",
        "pp_difference": "puan farkı",
        "moving_average": "hareketli ortalama",
        "extrema": "",
        "rebase": "yeniden bazlanmış endeks",
    },
    "en": {
        "difference": "difference",
        "percent_change": "percent change",
        "pp_difference": "percentage-point change",
        "moving_average": "moving average",
        "extrema": "",
        "rebase": "rebased index",
    },
}
LIMITS = {
    "tr": {
        "missing": "{label}: {when} için değer yok ({reason}).",
        "coverage": (
            "{label}: {start}–{end} aralığındaki her dönem için geçerli değer yok; sonuç "
            "yalnızca kapsanan dönemleri anlatır."
        ),
        "adjustment": "{label}: {what} kaynakta ve tarifte belirtilmemiştir.",
        "seasonal": "mevsim etkisi düzeltmesi",
        "calendar": "takvim etkisi düzeltmesi",
        "both": "mevsim ve takvim etkisi düzeltmesi",
        "mixed_adjustment": (
            "{where}: mevsim veya takvim etkisinden arındırılmış ve arındırılmamış değerler "
            "birlikte yer alıyor; doğrudan karşılaştırılmamalıdır."
        ),
        "mixed_base": "{where}: farklı baz yıllı endeksler birlikte yer alıyor.",
        "release": (
            "{resource}: yayım tarihi bilinmiyor; erişim zamanı yayım tarihi yerine kullanılmaz."
        ),
        "cutoff": (
            "{resource}: yayım tarihi bilinmediği için veri kesim tarihinden ({cutoff}) önce "
            "yayımlandığı doğrulanamıyor."
        ),
        "vintage": "Kaynaklar farklı zamanlarda indirildi ({first} – {last}).",
        "unverified": (
            "Doğrulanmamış taslak içerik: {count} metin kanıtla doğrulanamadı ve "
            "işaretlenerek bırakıldı."
        ),
        "note": "{title} kaynak notu: {note}",
        "issue": "{label}: {issue}",
    },
    "en": {
        "missing": "{label}: no value for {when} ({reason}).",
        "coverage": (
            "{label}: not every period from {start} to {end} has a valid value; the result "
            "describes the covered periods only."
        ),
        "adjustment": "{label}: {what} is not stated by the source or the recipe.",
        "seasonal": "seasonal adjustment",
        "calendar": "calendar adjustment",
        "both": "seasonal and calendar adjustment",
        "mixed_adjustment": (
            "{where}: seasonally or calendar adjusted and unadjusted values appear together; "
            "do not compare them directly."
        ),
        "mixed_base": "{where}: indexes with different base periods appear together.",
        "release": (
            "{resource}: the release date is unknown; the retrieval time never stands in for it."
        ),
        "cutoff": (
            "{resource}: the release date is unknown, so release before the data cutoff "
            "({cutoff}) cannot be confirmed."
        ),
        "vintage": "Sources were downloaded at different times ({first} – {last}).",
        "unverified": (
            "Unverified draft content: {count} texts could not be verified against the "
            "evidence and are kept, marked."
        ),
        "note": "{title} source note: {note}",
        "issue": "{label}: {issue}",
    },
}
MESSAGES = {
    "ok": (
        "Every number, date and comparison in the narrative is bound to resolved evidence. "
        "Renderers read report.json; they never recalculate or add prose."
    ),
    "draft": (
        "DRAFT: some text failed the claim checks and is kept, marked as unverified; see "
        "each finding's problems and the limitations."
    ),
    "rejected": (
        "Rejected: some text failed the claim checks; nothing was saved. Fix the quoted "
        "problems, or pass on_unverified='mark' to keep them as a marked draft."
    ),
}


def period_end(period: str) -> date:
    year = int(period[:4])
    frequency = period_frequency(period, "period")
    if frequency == "year":
        return date(year, 12, 31)
    if frequency == "month":
        month = int(period[5:7])
    elif frequency == "quarter":
        month = int(period[-1]) * 3
    else:
        month = int(period[-1]) * 6
    return date(year, month, monthrange(year, month)[1])


def reference_of(period: str, frequency: str, basis: str | None) -> str | None:
    number = ordinal(period, frequency)
    if basis == "previous_period":
        return period_label(number - 1, frequency)
    if basis == "same_period_previous_year":
        return period_label(number - PERIODS_PER_YEAR[frequency], frequency)
    if basis == "previous_december" and frequency == "month":
        return f"{int(period[:4]) - 1}-12"
    return None


def unit_key(unit: UnitSpec) -> tuple:
    return unit.kind, unit.multiplier, unit.currency, unit.base_period


def unit_of(observation: Observation | None) -> UnitSpec:
    if observation is None or observation.unit is None:
        return UnitSpec(kind="unknown")
    unit = observation.unit
    return UnitSpec(
        kind=unit.kind,
        multiplier=unit.multiplier,
        currency=unit.currency,
        base_period=unit.base_period,
    )


def measure_of(observation: Observation | None) -> MeasureSpec:
    if observation is None or observation.measure is None:
        return MeasureSpec(type="unknown")
    return MeasureSpec(type=observation.measure.type, basis=observation.measure.basis)


def adjustment_of(observation: Observation | None) -> Adjustment:
    if observation is None:
        return Adjustment(seasonal="unknown", calendar="unknown")
    return Adjustment(
        seasonal=observation.adjustment.seasonal, calendar=observation.adjustment.calendar
    )


class Loaded:
    def __init__(self, recipe, resource, file, table):
        self.recipe, self.resource, self.file, self.table = recipe, resource, file, table
        self.workbook = isinstance(recipe, WorkbookRecipe)
        self.snapshot_id = "sha256:" + file.digest
        self.by_id = {o.id: o for o in table.observations}

    def source(self, resource_id: str) -> EvidenceSource:
        return EvidenceSource(
            resource_id=resource_id,
            recipe_id=self.recipe.id,
            recipe_version=self.recipe.version,
            snapshot_id=self.snapshot_id,
            source_url=self.resource.source_url if self.workbook else self.file.link.source_url,
            source_scope="reviewed_cells" if self.workbook else "whole_export",
            publication_date=self.resource.publication_date,
            retrieved_at=self.file.retrieved_at,
        )

    def one_series(self, key: str, filters: dict[str, str]) -> tuple[list[int], str]:
        """Dated records of the one series the filters leave, and its frequency."""
        table = self.table
        candidates, _, _, _ = ObservationService.select(table, filters, None, None)
        dated = [i for i in candidates if table.observations[i].period is not None]
        if not dated:
            raise ValueError(f"{key}: the filters select no dated observations")
        frequencies = {table.observations[i].period.frequency for i in dated}
        if len(frequencies) > 1:
            raise ValueError(
                f"{key}: the selection mixes {sorted(frequencies)} periods; filter Gözlem "
                "Sıklığı to one"
            )
        identifying = {
            concept: sorted({table.facets[i][concept] for i in dated})[:MAX_LISTED_VALUES]
            for concept in table.varying
            if concept not in table.series_exclude
            and len({table.facets[i][concept] for i in dated}) > 1
        }
        if identifying:
            raise ValueError(
                f"{key}: the filters leave several series; add filters so one remains: "
                f"{identifying}"
            )
        return dated, next(iter(frequencies))

    def label(self, spec, observation: Observation | None) -> str:
        if spec.label:
            self.check_label_scope(spec, observation)
            return spec.label
        measure = observation.measure.label if observation and observation.measure else None
        rest = [v for v in spec.filters.values() if v != measure]
        if measure is None:
            return ", ".join(rest) or self.recipe.title
        return f"{measure} ({', '.join(rest)})" if rest else measure

    def check_label_scope(self, spec, observation: Observation | None) -> None:
        """A caller's label may not name a geography the source series does not have.

        Labels are masked before claim checks, so a province in a label would turn a
        national value into a verified provincial statement.
        """
        named, national = geography_terms(spec.label)
        source, _ = geography_terms(source_geography(spec, observation, self.recipe.title))
        extra = sorted(named - source)
        if extra:
            raise ValueError(
                f"{spec.key}: label names {extra[0]!r}, which is not the geography of the "
                "source series; remove it or select that province's series"
            )
        if national and source:
            raise ValueError(
                f"{spec.key}: label states a national scope, but the source series is provincial"
            )


def source_geography(spec, observation: Observation | None, title: str) -> str:
    """The published text that establishes a series' geography."""
    parts = [title, *spec.filters.values()]
    if observation is not None:
        parts += list(observation.dimensions.values())
        if observation.measure is not None:
            parts.append(observation.measure.label)
        if observation.geography is not None:
            parts.append(observation.geography.label)
    return " ".join(part for part in parts if part)


def fact(evidence: Evidence) -> Fact:
    extremum = None
    if evidence.kind == "calculation" and evidence.id and evidence.id.endswith((":min", ":max")):
        extremum = evidence.id.rsplit(":", 1)[1]
    periods = {
        p
        for p in (
            evidence.period,
            evidence.reference_period,
            evidence.period_from,
            evidence.period_to,
            *evidence.periods,
        )
        if p
    }
    return Fact(
        key=evidence.key,
        status=evidence.status,
        reason=evidence.reason,
        value=Decimal(evidence.value) if evidence.value is not None else None,
        unit=evidence.unit,
        change=evidence.measure.type == "change",
        extremum=extremum,
        periods=periods,
    )


def labels_of(items: list[Evidence]) -> list[str]:
    found = []
    for item in items:
        found += [item.label, item.measure_label, item.unit_label, *item.dimensions.values()]
        found += [p.period for p in item.points]
    # Longest first, so a label is masked before any label it contains.
    return sorted({text for text in found if text}, key=len, reverse=True)


def latest(periods) -> str | None:
    return max(periods, key=period_end, default=None)


class ReportService:
    def __init__(self, catalog_service):
        self.catalog = catalog_service

    async def load(self, cache: dict, resource_id: str, snapshot_id: str | None) -> Loaded:
        validate_id(resource_id)
        loaded = cache.get(resource_id)
        if loaded is None:
            held = unavailable(resource_id)
            if held is not None:
                raise SourceError(
                    "unavailable",
                    f"No reviewed observations for {held.domain}: {held.reason}",
                    PORTAL,
                )
            if resource_id not in BY_RESOURCE and resource_id not in BY_WORKBOOK:
                raise SourceError(
                    "unsupported",
                    f"{resource_id} has no reviewed observation recipe. Reports cite reviewed "
                    "observations only; call get_tuik_observations without resource_id to "
                    "list them.",
                    PORTAL,
                )
            try:
                loaded = Loaded(*await self.catalog.normalized.table(resource_id, snapshot_id))
            except SchemaChanged as exc:
                raise SourceError(
                    "schema_changed",
                    "The published file no longer matches the reviewed recipe: "
                    + "; ".join(exc.issues),
                    PORTAL,
                ) from exc
            cache[resource_id] = loaded
        if snapshot_id is not None and snapshot_id != loaded.snapshot_id:
            raise SourceError(
                "snapshot_changed",
                f"{resource_id}: the current file is {loaded.snapshot_id}, not {snapshot_id}.",
                loaded.resource.source_url,
            )
        return loaded

    async def resolve(self, spec, language: str, cache: dict) -> Evidence:
        loaded = await self.load(cache, spec.resource_id, spec.snapshot_id)
        if isinstance(spec, CalculationEvidenceSpec):
            return await self.calculation(spec, language, loaded)
        dated, frequency = loaded.one_series(spec.key, spec.filters)
        observations = loaded.table.observations
        for name in ("period", "period_from", "period_to"):
            value = getattr(spec, name, None)
            if value is not None and period_frequency(value, name) != frequency:
                raise ValueError(f"{spec.key}: {name} {value!r} is not a {frequency} period")
        records = [observations[i] for i in dated]
        valid = [o for o in records if o.status == "valid"]
        if valid:
            single(
                f"{spec.key}",
                "unit",
                {unit_key(unit_of(o)) for o in valid},
            )
            single(f"{spec.key}", "measure", {(o.measure.type, o.measure.basis) for o in valid})
            single(
                f"{spec.key}",
                "adjustment",
                {(o.adjustment.seasonal, o.adjustment.calendar) for o in valid},
            )
        if isinstance(spec, ObservationEvidenceSpec):
            return self.observation(spec, language, loaded, records, valid, frequency)
        return self.series(spec, language, loaded, records, valid, frequency)

    def observation(
        self,
        spec: ObservationEvidenceSpec,
        language: str,
        loaded: Loaded,
        records: list[Observation],
        valid: list[Observation],
        frequency: str,
    ) -> Evidence:
        at = [o for o in records if o.period.value == spec.period]
        chosen = at[0] if len(at) == 1 else None
        like = chosen if chosen is not None and chosen.unit is not None else (valid or [None])[0]
        unit, measure = unit_of(like), measure_of(like)
        status, reason, value = "missing", None, None
        if not at:
            reason = f"no source record for {spec.period}"
        elif chosen is None:
            reason = f"{spec.period} has duplicate source records"
        elif chosen.status != "valid":
            reason = f"{spec.period} is {chosen.status}" + (
                f": {chosen.reason}" if chosen.reason else ""
            )
        else:
            status, value = "valid", chosen.value
        return Evidence(
            key=spec.key,
            kind="observation",
            label=loaded.label(spec, like),
            id=chosen.id if chosen is not None else None,
            status=status,
            reason=reason,
            value=format(value, "f") if value is not None else None,
            rounded=rounded(value, spec.decimals) if value is not None else None,
            display=display(value, spec.decimals, unit, language) if value is not None else None,
            decimals=spec.decimals,
            frequency=frequency,
            period=spec.period,
            reference_period=(
                reference_of(spec.period, frequency, measure.basis)
                if measure.type == "change"
                else None
            ),
            unit=unit,
            unit_label=(like.unit.label if like and like.unit else None)
            or unit_label(unit, language),
            measure=measure,
            measure_label=like.measure.label if like and like.measure else None,
            adjustment=adjustment_of(like),
            derived=False,
            inputs=[o.id for o in at],
            dimensions=dict(spec.filters),
            coverage_complete=status == "valid",
            source=loaded.source(spec.resource_id),
            notes=list(loaded.recipe.notes),
        )

    def series(
        self,
        spec: SeriesEvidenceSpec,
        language: str,
        loaded: Loaded,
        records: list[Observation],
        valid: list[Observation],
        frequency: str,
    ) -> Evidence:
        start, end = ordinal(spec.period_from, frequency), ordinal(spec.period_to, frequency)
        if end < start:
            raise ValueError(f"{spec.key}: period_from is after period_to")
        if end - start + 1 > MAX_SERIES_POINTS:
            raise ValueError(
                f"{spec.key}: a chart series takes at most {MAX_SERIES_POINTS} periods; "
                "narrow the range"
            )
        by_period: dict[int, list[Observation]] = {}
        for o in records:
            by_period.setdefault(ordinal(o.period.value, frequency), []).append(o)
        points = []
        for number in range(start, end + 1):
            label = period_label(number, frequency)
            found = by_period.get(number, [])
            if len(found) == 1:
                o = found[0]
                points.append(
                    Point(
                        period=label,
                        status=o.status,
                        value=format(o.value, "f") if o.value is not None else None,
                        id=o.id,
                    )
                )
            else:
                points.append(Point(period=label, status="duplicate" if found else "missing"))
        like = (valid or [None])[0]
        unit = unit_of(like)
        complete = all(p.status == "valid" for p in points)
        any_valid = any(p.status == "valid" for p in points)
        return Evidence(
            key=spec.key,
            kind="series",
            label=loaded.label(spec, like),
            status="valid" if any_valid else "missing",
            reason=None
            if any_valid
            else f"no valid value from {spec.period_from} to {spec.period_to}",
            decimals=spec.decimals,
            frequency=frequency,
            period_from=spec.period_from,
            period_to=spec.period_to,
            unit=unit,
            unit_label=(like.unit.label if like and like.unit else None)
            or unit_label(unit, language),
            measure=measure_of(like),
            measure_label=like.measure.label if like and like.measure else None,
            adjustment=adjustment_of(like),
            derived=False,
            inputs=[p.id for p in points if p.id],
            dimensions=dict(spec.filters),
            points=points,
            coverage_complete=complete,
            source=loaded.source(spec.resource_id),
            notes=list(loaded.recipe.notes),
        )

    async def calculation(
        self, spec: CalculationEvidenceSpec, language: str, loaded: Loaded
    ) -> Evidence:
        if spec.operation == "extrema":
            period_from, period_to = spec.period_from, spec.period_to
        elif spec.basis == "periods":
            period_from = period_to = None
        else:
            period_from = period_to = spec.period
        response = await self.catalog.calculate(
            spec.resource_id,
            spec.operation,
            filters=spec.filters,
            basis=spec.basis,
            periods=spec.periods,
            window=spec.window,
            base_period=spec.base_period,
            period_from=period_from,
            period_to=period_to,
            decimals=spec.decimals,
            snapshot_id=loaded.snapshot_id,
            bounded=False,
        )
        if len(response.series) != 1:
            raise ValueError(
                f"{spec.key}: the calculation has {len(response.series)} series; filter to one"
            )
        (series,) = response.series
        frequency = response.frequency
        cid = response.calculation_id
        first_input = next(iter(series.inputs), None)
        like = loaded.by_id.get(first_input.id) if first_input else None
        base = loaded.label(spec.model_copy(update={"label": None}), like)
        suffix = OPERATIONS[language][spec.operation]
        label = spec.label or (f"{base}, {suffix}" if suffix else base)
        unit = response.output_unit
        common = dict(
            key=spec.key,
            kind="calculation",
            label=label,
            decimals=spec.decimals,
            frequency=frequency,
            unit=unit,
            unit_label=unit_label(unit, language),
            measure=response.output_measure,
            measure_label=like.measure.label if like and like.measure else None,
            adjustment=adjustment_of(like),
            derived=True,
            formula=response.formula,
            calculation_id=cid,
            dimensions=dict(spec.filters),
            coverage_complete=response.coverage.complete,
            source=loaded.source(spec.resource_id),
            notes=list(loaded.recipe.notes),
            issues=list(response.issues),
        )
        if spec.operation == "extrema":
            extremum = next((e for e in series.extrema if e.kind == spec.extremum), None)
            range_ = dict(period_from=response.period_from, period_to=response.period_to)
            if extremum is None:
                return Evidence(
                    **common,
                    **range_,
                    id=f"{cid}:{spec.extremum}",
                    status="missing",
                    reason=f"no valid value from {response.period_from} to {response.period_to}",
                )
            value = Decimal(extremum.value)
            return Evidence(
                **common,
                **range_,
                id=f"{cid}:{spec.extremum}",
                status="valid",
                value=extremum.value,
                rounded=extremum.rounded,
                display=display(value, spec.decimals, unit, language),
                periods=extremum.periods,
                tie_count=extremum.tie_count,
                inputs=extremum.inputs,
            )
        (result,) = series.results
        if spec.basis == "periods":
            reference = spec.periods[0]
        elif spec.operation == "rebase":
            reference = spec.base_period
        elif spec.operation in {"difference", "percent_change", "pp_difference"}:
            reference = reference_of(result.period, frequency, spec.basis)
        else:
            reference = None
        window_start = (
            period_label(ordinal(result.period, frequency) - spec.window + 1, frequency)
            if spec.operation == "moving_average"
            else None
        )
        value = Decimal(result.value) if result.value is not None else None
        return Evidence(
            **common,
            id=f"{cid}:{result.period}",
            status=result.status,
            reason=result.reason,
            value=result.value,
            rounded=result.rounded,
            display=display(value, spec.decimals, unit, language) if value is not None else None,
            period=result.period,
            reference_period=reference,
            period_from=window_start,
            period_to=result.period if window_start else None,
            inputs=result.inputs,
        )

    async def build(self, spec: ReportSpec | dict) -> Report:
        spec = spec if isinstance(spec, ReportSpec) else ReportSpec.model_validate(spec)
        language = spec.language
        cache: dict[str, Loaded] = {}
        evidence = [await self.resolve(item, language, cache) for item in spec.evidence]
        by_key = {item.key: item for item in evidence}
        facts = {item.key: fact(item) for item in evidence}
        limits = LIMITS[language]
        limitations: list[str] = []

        def limit(text: str) -> None:
            if text not in limitations:
                limitations.append(text)

        # Dates: a source released or observed after the cutoff is refused.
        if spec.source_cutoff is not None:
            for item in evidence:
                released = item.source.publication_date
                if released and date.fromisoformat(released[:10]) > spec.source_cutoff:
                    raise ValueError(
                        f"{item.key}: the source was released on {released[:10]}, after "
                        f"source_cutoff {spec.source_cutoff}"
                    )
                last = latest(p for p in (item.period, item.period_to) if p)
                if last and period_end(last) > spec.source_cutoff:
                    raise ValueError(
                        f"{item.key}: period {last} ends after source_cutoff {spec.source_cutoff}"
                    )

        adjustment_noted: set = set()
        for item in evidence:
            when = item.period or f"{item.period_from}–{item.period_to}"
            if item.status != "valid":
                limit(limits["missing"].format(label=item.label, when=when, reason=item.reason))
            if not item.coverage_complete and item.status == "valid":
                start = item.period_from or item.period
                end = item.period_to or item.period
                limit(limits["coverage"].format(label=item.label, start=start, end=end))
            for issue in item.issues:
                limit(limits["issue"].format(label=item.label, issue=issue))
            unknown_parts = [
                part
                for part in ("seasonal", "calendar")
                if getattr(item.adjustment, part) == "unknown"
            ]
            series_id = (item.source.resource_id, tuple(sorted(item.dimensions.items())))
            if unknown_parts and series_id not in adjustment_noted:
                adjustment_noted.add(series_id)
                what = limits["both"] if len(unknown_parts) == 2 else limits[unknown_parts[0]]
                limit(limits["adjustment"].format(label=item.label, what=what))
            for note in item.notes:
                title = cache[item.source.resource_id].recipe.title
                limit(limits["note"].format(title=title, note=note))

        def mixed(items: list[Evidence], where: str) -> None:
            # Unknown is not a difference; adjusted beside not adjusted is.
            if any(
                len({getattr(i.adjustment, part) for i in items} - {"unknown"}) > 1
                for part in ("seasonal", "calendar")
            ):
                limit(limits["mixed_adjustment"].format(where=where))
            bases = {i.unit.base_period for i in items if i.unit.kind == "index"}
            if len(bases) > 1:
                limit(limits["mixed_base"].format(where=where))

        all_labels = labels_of(evidence)
        all_periods = set().union(*(f.periods for f in facts.values()))
        problems: list[str] = []
        for name in ("title", "subtitle", "audience"):
            value = getattr(spec, name)
            if value:
                _, found = check_text(value, [], {}, language, all_labels, all_periods)
                problems += [f"{name}: {p}" for p in found]

        headings = TEXT[language]["headings"]
        section_specs = list(spec.sections)
        placed = {s.kind for s in section_specs}
        section_specs += [SectionSpec(kind=kind) for kind in GENERATED if kind not in placed]
        sections: list[Section] = []
        findings_total = verified = 0
        for number, section_spec in enumerate(section_specs, 1):
            title = section_spec.title or headings[section_spec.kind]
            if section_spec.title:
                _, found = check_text(title, [], {}, language, all_labels, all_periods)
                problems += [f"section {number} title: {p}" for p in found]
            section = Section(kind=section_spec.kind, title=title)
            for finding_spec in section_spec.findings:
                items = [by_key[k] for k in finding_spec.evidence]
                mixed(items, title)
                if finding_spec.text is None:
                    text, claims = finding_text(items, language)
                    origin = "template"
                else:
                    text, claims, origin = finding_spec.text, finding_spec.claims, "host"
                scope = {k: facts[k] for k in finding_spec.evidence}
                allowed = set().union(*(f.periods for f in scope.values()))
                checked, found = check_text(
                    text, claims, scope, language, labels_of(items), allowed
                )
                ok = not found and all(c.status == "verified" for c in checked)
                findings_total += 1
                verified += ok
                section.findings.append(
                    Finding(
                        origin=origin,
                        status="verified" if ok else "unverified",
                        text=text,
                        evidence=list(finding_spec.evidence),
                        claims=checked,
                        problems=found,
                    )
                )
            if section_spec.table is not None:
                items = [by_key[k] for k in section_spec.table.rows]
                mixed(items, title)
                section.table = Table(
                    columns=TEXT[language]["columns"],
                    rows=[
                        TableRow(
                            evidence=i.key,
                            label=i.label,
                            period=i.period or f"{i.period_from}–{i.period_to}",
                            value=i.display,
                            status=i.status,
                            id=i.id,
                        )
                        for i in items
                    ],
                )
            if section_spec.chart is not None:
                items = [by_key[k] for k in section_spec.chart.series]
                mixed(items, title)
                section.chart = self.chart(section_spec, items, language, number)
            sections.append(section)

        unverified = findings_total - verified
        status = "ok"
        if unverified or problems:
            status = "rejected" if spec.on_unverified == "reject" else "draft"
        if status == "draft":
            limit(limits["unverified"].format(count=unverified + (1 if problems else 0)))
        resources: dict[tuple[str, str], Evidence] = {}
        for item in evidence:
            resources.setdefault((item.source.resource_id, item.source.snapshot_id), item)
        for item in resources.values():
            if item.source.publication_date is None:
                limit(limits["release"].format(resource=item.source.resource_id))
                if spec.source_cutoff is not None:
                    limit(
                        limits["cutoff"].format(
                            resource=item.source.resource_id, cutoff=spec.source_cutoff
                        )
                    )
        retrieved = sorted({item.source.retrieved_at for item in evidence})
        if len(retrieved) > 1:
            limit(
                limits["vintage"].format(
                    first=retrieved[0].isoformat(), last=retrieved[-1].isoformat()
                )
            )
        methods = [TEXT[language]["normalized"]]
        derived = [i for i in evidence if i.derived]
        for item in derived:
            methods.append(f"{item.label}: {item.formula} [{item.calculation_id}]")
        if derived:
            methods += [TEXT[language]["derived"], TEXT[language]["rounding"]]
        unknown = TEXT[language]["unknown"]
        sources = [
            f"TÜİK, {cache[resource_id].recipe.title} ({resource_id}), "
            f"{item.source.source_url}; {snapshot}; "
            + ("yayım" if language == "tr" else "released")
            + f": {item.source.publication_date or unknown}; "
            + ("erişim" if language == "tr" else "retrieved")
            + f": {item.source.retrieved_at.isoformat()}"
            for (resource_id, snapshot), item in resources.items()
        ]
        for section in sections:
            section.items = {
                "methods": methods,
                "limitations": limitations,
                "sources": sources,
            }.get(section.kind, [])

        identity = {
            "spec": spec.model_dump(mode="json"),
            "snapshots": sorted({(i.key, i.source.snapshot_id) for i in evidence}),
        }
        canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        known = [i.source.publication_date for i in resources.values() if i.source.publication_date]
        report = Report(
            source_url=evidence[0].source.source_url,
            retrieved_at=retrieved[-1],
            status=status,
            message=MESSAGES[status],
            report_id="rpt:" + hashlib.sha256(canonical.encode()).hexdigest()[:16],
            title=spec.title,
            subtitle=spec.subtitle,
            audience=spec.audience,
            language=language,
            timeline=Timeline(
                latest_release=max(known, default=None),
                release_unknown=sorted(
                    {
                        i.source.resource_id
                        for i in resources.values()
                        if not i.source.publication_date
                    }
                ),
                latest_observation=latest(
                    p
                    for i in evidence
                    if i.status == "valid"
                    for p in (
                        [i.period]
                        if i.kind == "observation"
                        else [i.period or i.period_to]
                        if i.kind == "calculation" and not i.periods
                        else i.periods or [pt.period for pt in i.points if pt.status == "valid"]
                    )
                    if p
                ),
                retrieved_from=retrieved[0],
                retrieved_to=retrieved[-1],
                data_cutoff=spec.source_cutoff,
            ),
            counts=ReportCounts(
                evidence=len(evidence),
                findings=findings_total,
                verified=verified,
                unverified=unverified,
            ),
            evidence=evidence,
            sections=sections,
            limitations=limitations,
            problems=problems,
            spec=spec,
        )
        if status != "rejected":
            manifest = await self.save(report, cache)
            report = report.model_copy(
                update={"artifact": self.catalog.artifacts.summary(manifest)}
            )
        return self.fit(report)

    def chart(self, section: SectionSpec, items: list[Evidence], language: str, number: int):
        units = {unit_key(i.unit) for i in items}
        if len(units) > 1:
            raise ValueError(
                f"section {number}: a chart's series must share one unit and base; got "
                f"{sorted(map(str, units))}. Charts never get a second axis: split the chart "
                "or rebase the series"
            )
        frequencies = {i.frequency for i in items}
        if len(frequencies) > 1:
            raise ValueError(f"section {number}: a chart mixes {sorted(frequencies)} periods")
        unit = items[0].unit
        descriptions = []
        for item in items:
            valid = [p for p in item.points if p.status == "valid"]
            gaps = len(item.points) - len(valid)
            if not valid:
                descriptions.append(f"{item.label}: " + TEXT[language]["missing"])
                continue
            low = min(valid, key=lambda p: Decimal(p.value))
            high = max(valid, key=lambda p: Decimal(p.value))

            def shown(point: Point, decimals: int = item.decimals) -> str:
                return display(Decimal(point.value), decimals, unit, language)

            if language == "tr":
                text = (
                    f"{item.label}, {item.period_from}–{item.period_to}: ilk {shown(valid[0])} "
                    f"({valid[0].period}), son {shown(valid[-1])} ({valid[-1].period}), en düşük "
                    f"{shown(low)} ({low.period}), en yüksek {shown(high)} ({high.period})"
                )
                text += f"; {gaps} dönem değersiz." if gaps else "."
            else:
                text = (
                    f"{item.label}, {item.period_from}–{item.period_to}: first {shown(valid[0])} "
                    f"({valid[0].period}), last {shown(valid[-1])} ({valid[-1].period}), lowest "
                    f"{shown(low)} ({low.period}), highest {shown(high)} ({high.period})"
                )
                text += f"; {gaps} periods without a value." if gaps else "."
            descriptions.append(text)
        return Chart(
            type=section.chart.type,
            unit=unit,
            unit_label=items[0].unit_label,
            frequency=items[0].frequency,
            series=[ChartLine(evidence=i.key, label=i.label, points=i.points) for i in items],
            alt_text=" ".join(descriptions),
        )

    def fit(self, report: Report) -> Report:
        """Keep the inline reply within the output budget; report.json is complete."""
        budget = self.catalog.queries.limits.max_output_bytes

        def size(item: Report) -> int:
            return len(rendered(item.model_dump(mode="json")))

        if size(report) <= budget:
            return report
        report = report.model_copy(
            update={
                "inline_complete": False,
                "spec": None,
                "evidence": [e.model_copy(update={"points": []}) for e in report.evidence],
                "sections": [
                    s.model_copy(
                        update={
                            "chart": s.chart.model_copy(
                                update={
                                    "series": [
                                        line.model_copy(update={"points": []})
                                        for line in s.chart.series
                                    ]
                                }
                            )
                            if s.chart
                            else None
                        }
                    )
                    for s in report.sections
                ],
            }
        )
        if size(report) <= budget:
            return report
        report = report.model_copy(update={"evidence": []})
        if size(report) <= budget:
            return report
        # Keep only what needs action: findings that failed their checks.
        sections = [
            s.model_copy(
                update={
                    "findings": [f for f in s.findings if f.status == "unverified"],
                    "table": None,
                    "chart": None,
                    "items": [],
                }
            )
            for s in report.sections
            if any(f.status == "unverified" for f in s.findings)
        ]
        return report.model_copy(update={"sections": sections})

    async def save(self, report: Report, cache: dict[str, Loaded]):
        document = report.model_dump(mode="json", exclude={"artifact"})
        rows = []
        for item in report.evidence:
            rows.append(
                [
                    item.key,
                    item.kind,
                    item.label,
                    item.id or "",
                    item.status,
                    item.period or "",
                    item.reference_period or "",
                    item.period_from or "",
                    item.period_to or "",
                    item.value or "",
                    item.rounded or "",
                    item.display or "",
                    item.unit.kind,
                    item.unit.multiplier,
                    item.unit.currency or "",
                    item.unit.base_period or "",
                    item.measure.type,
                    item.measure.basis or "",
                    item.derived,
                    item.formula or "",
                    " ".join(item.inputs),
                    item.source.resource_id,
                    item.source.snapshot_id,
                    item.source.publication_date or "",
                    item.source.retrieved_at.isoformat(),
                    item.source.source_url,
                ]
            )
            for point in item.points:
                rows.append(
                    [item.key, "point", item.label, point.id or "", point.status, point.period]
                    + [""] * 3
                    + [point.value or ""]
                    + [""] * 16
                )
        header = [
            "key",
            "kind",
            "label",
            "id",
            "status",
            "period",
            "reference_period",
            "period_from",
            "period_to",
            "value",
            "rounded",
            "display",
            "unit",
            "multiplier",
            "currency",
            "base_period",
            "measure",
            "basis",
            "derived",
            "formula",
            "inputs",
            "resource_id",
            "snapshot_id",
            "publication_date",
            "retrieved_at",
            "source_url",
        ]
        used = []
        for item in report.evidence:
            loaded = cache[item.source.resource_id]
            if loaded not in used:
                used.append(loaded)
        inputs, sources = [], {}
        for index, loaded in enumerate(used):
            resource_id = next(rid for rid, value in cache.items() if value is loaded)
            inputs.append(
                ArtifactInput(
                    resource_id=resource_id,
                    source_url=loaded.resource.source_url,
                    file_url=loaded.file.link.source_url,
                    format=loaded.file.link.format,
                    retrieved_at=loaded.file.retrieved_at,
                    publication_date=loaded.resource.publication_date,
                    sha256=loaded.file.digest,
                    size=len(loaded.file.body),
                    selection={
                        "evidence": [
                            e.key for e in report.evidence if e.source.resource_id == resource_id
                        ]
                    },
                )
            )
            fmt = loaded.file.link.format
            sources[index] = (
                f"source-{index + 1}.{fmt}",
                SOURCE_TYPES.get(fmt, "application/octet-stream"),
                loaded.file.body,
            )
        readme = "\n".join(
            [
                f"Report: {report.title}",
                f"ID: {report.report_id} ({report.schema_version}), status {report.status}",
                f"Findings: {report.counts.verified} verified, {report.counts.unverified} "
                "unverified",
                "",
                "report.json is the validated report every renderer reads: evidence with "
                "observation or calculation IDs, findings with their claim checks, tables, "
                "chart data, methods, limitations and sources. report.md is a reference "
                "rendering in which all text is escaped. evidence.csv lists each value and "
                "series point with its source.",
                "Derived values recompute from calculate_tuik_series with the same "
                "specification and snapshot; their calculation_id is a digest of both.",
                "",
                *(f"Limitation: {item}" for item in report.limitations),
            ]
        ).encode()
        citation = (
            "Kaynak: TÜİK · "
            + "; ".join(i.resource_id for i in inputs)[:160]
            + f" · tuik-mcp raporu {report.report_id}"
        )
        return await self.catalog.artifacts.create(
            kind="report",
            title=report.title[:200],
            citation=citation,
            inputs=inputs,
            files=[
                PendingFile(
                    name="report.json",
                    role="output",
                    mime_type="application/json",
                    content=json.dumps(document, ensure_ascii=False, indent=2).encode(),
                ),
                PendingFile(
                    name="report.md",
                    role="output",
                    mime_type="text/markdown",
                    content=markdown(report).encode(),
                ),
                PendingFile(
                    name="evidence.csv",
                    role="data",
                    mime_type="text/csv",
                    content=csv_bytes(header, rows),
                ),
                PendingFile(
                    name="README.txt", role="notes", mime_type="text/plain", content=readme
                ),
            ],
            sources=sources,
            transformations=[
                "Evidence resolved from reviewed normalized observations (tuik.observation/1) "
                "and deterministic calculations (tuik.calculation/1).",
                "Every number, date and comparison in narrative text was checked against its "
                "bound evidence.",
            ],
            warnings=list(report.limitations),
        )


__all__ = ["ReportService"]
