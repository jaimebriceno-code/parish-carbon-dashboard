"""Validator for the carbon dashboard data.

`validate_dataset` returns a list of `Issue`s. Errors mean the data must not be
published (the build stops on them). Warnings mean a person should look before
publishing. The vehicles and commute files may be header-only.

Given the parish settings it also checks the data against them: the factors file
must hold the row for the configured eGRID subregion (`UNKNOWN_SUBREGION`, which
lists the subregions it does hold), natural gas and, when there are travel rows,
the gasoline-car comparison (`MISSING_FACTOR`); and a missing Spanish edition is
only worth a warning on a site that is built in Spanish, for a factor the parish
uses. `docs/monthly-update.md` explains every code for the person entering data.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, replace
from typing import Literal

from carbon.calc import (
    GASOLINE_FACTOR_ID,
    NATURAL_GAS_FACTOR_ID,
    available_subregions,
    factors_in_use,
    grid_factor_id,
)
from carbon.models import (
    COMMUTE_QUALITIES,
    DATA_QUALITIES,
    FUELS,
    MILESTONE_KINDS,
    MILESTONE_PRECISIONS,
    UNIT_FOR_FUEL,
    Dataset,
    Reading,
)

PERIOD_MIN_DAYS = 25
PERIOD_MAX_DAYS = 35
PLAUSIBILITY_LIMIT = 0.40  # warn when a month moves more than 40% against the same month a year earlier

LONG_DIGIT_RUN = 9  # a run this long in free text looks like an account number

_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_DOLLAR_RE = re.compile(r"\$\s?\d")
_LONG_DIGITS_RE = re.compile(r"\d{%d,}" % LONG_DIGIT_RUN)
_SOLAR_WORD_RE = re.compile(r"\bsolar\b", re.IGNORECASE)


@dataclass(frozen=True)
class Issue:
    level: Literal["error", "warning"]
    code: str
    message: str


def _month_index(month: str) -> int | None:
    """Months since year 0 (so consecutive months differ by 1), or None if not YYYY-MM."""
    match = _MONTH_RE.match(month)
    if not match:
        return None
    return int(match.group(1)) * 12 + int(match.group(2)) - 1


def _month_label(index: int) -> str:
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _fmt(value: float) -> str:
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def validate_dataset(ds: Dataset, settings=None) -> list[Issue]:
    """Every problem in `ds`. `settings` (a `carbon.settings.Settings`) adds the checks that depend on the parish:
    without it the data is judged alone, as for a bilingual site, and no factor is required to be present."""
    issues: list[Issue] = []
    spanish = settings is None or "es" in settings.languages

    def error(code: str, message: str) -> None:
        issues.append(Issue("error", code, message))

    def warn(code: str, message: str) -> None:
        issues.append(Issue("warning", code, message))

    def check_text(place: str, name: str, text: str) -> None:
        """Free text must not carry dollar amounts or account-number-like digit runs (never echoed)."""
        if _DOLLAR_RE.search(text):
            error("DOLLAR_IN_TEXT", f"{place}: {name} contains a dollar amount; dollars never go in the data")
        if _LONG_DIGITS_RE.search(text):
            error(
                "LONG_DIGITS_IN_TEXT",
                f"{place}: {name} contains a run of {LONG_DIGIT_RUN} or more digits (looks like an account number)",
            )

    meters = {m.meter_id: m for m in ds.meters}
    for meter_id, n in Counter(m.meter_id for m in ds.meters).items():
        if n > 1:
            error("DUPLICATE_METER", f"{meter_id}: listed {n} times in meters.csv")
    for meter in ds.meters:
        if meter.fuel not in FUELS:
            error("INVALID_VALUE", f"{meter.meter_id}: fuel {meter.fuel!r} is not one of {', '.join(FUELS)}")
        check_text(f"meter {meter.meter_id}", "label", meter.label)
        check_text(f"meter {meter.meter_id}", "notes", meter.notes)

    # Checks on each reading by itself.
    usable: list[tuple[Reading, int]] = []  # readings with a valid month, for the series checks
    for r in ds.readings:
        where = f"{r.meter_id} {r.period_end_month}"
        month = _month_index(r.period_end_month)
        if month is None:
            error("INVALID_VALUE", f"{where}: period_end_month must be YYYY-MM")
        else:
            usable.append((r, month))

        meter = meters.get(r.meter_id)
        if meter is None:
            error("UNKNOWN_METER", f"{where}: meter {r.meter_id!r} is not in meters.csv")
        elif meter.fuel in UNIT_FOR_FUEL and r.unit != UNIT_FOR_FUEL[meter.fuel]:
            error(
                "UNIT_MISMATCH",
                f"{where}: unit {r.unit!r} on a {meter.fuel} meter; expected {UNIT_FOR_FUEL[meter.fuel]!r}",
            )

        if r.data_quality not in DATA_QUALITIES:
            error(
                "INVALID_VALUE",
                f"{where}: data_quality {r.data_quality!r} is not one of {', '.join(DATA_QUALITIES)}",
            )
        elif r.data_quality == "chart_measured" and r.uncertainty_pct is None:
            warn("MISSING_UNCERTAINTY", f"{where}: chart_measured reading has no uncertainty_pct")

        if r.quantity < 0:
            error("INVALID_VALUE", f"{where}: quantity {_fmt(r.quantity)} is below zero")
        elif r.quantity == 0:
            warn("ZERO_QUANTITY", f"{where}: quantity is zero; check the transcription")
        if r.uncertainty_pct is not None and not 0 <= r.uncertainty_pct <= 100:
            error("INVALID_VALUE", f"{where}: uncertainty_pct {_fmt(r.uncertainty_pct)} is outside 0 to 100")
        if r.peak_kw is not None and not r.peak_kw > 0:
            error("INVALID_VALUE", f"{where}: peak_kw {_fmt(r.peak_kw)} must be above zero when given")
        check_text(f"reading {where}", "note", r.note)

        if month is not None and r.period_end is not None and f"{r.period_end:%Y-%m}" != r.period_end_month:
            error(
                "PERIOD_MONTH_MISMATCH",
                f"{where}: period_end {r.period_end} is not in the labelled month {r.period_end_month}",
            )

        if (r.period_start is None) != (r.period_end is None):
            error("PERIOD_PARTIAL", f"{where}: give both period_start and period_end, or neither")
        elif r.period_start is not None and r.period_end is not None:
            days = (r.period_end - r.period_start).days
            if not PERIOD_MIN_DAYS <= days <= PERIOD_MAX_DAYS:
                error(
                    "PERIOD_LENGTH",
                    f"{where}: billing period is {days} days ({r.period_start} to {r.period_end}); "
                    f"expected {PERIOD_MIN_DAYS} to {PERIOD_MAX_DAYS}",
                )

    # Factors. The file may hold a row for every eGRID subregion; an id may appear only once, or the wrong row
    # could be picked silently.
    for factor_id, n in Counter(f.factor_id for f in ds.factors).items():
        if n > 1:
            error("DUPLICATE_FACTOR", f"{factor_id}: listed {n} times in factors.csv; keep one row per factor_id")
    for f in ds.factors:
        if not f.value > 0:
            error("INVALID_VALUE", f"factor {f.factor_id}: value {_fmt(f.value)} must be above zero")
    factor_ids = {f.factor_id for f in ds.factors}
    if settings is not None:
        wanted = grid_factor_id(settings.egrid_subregion)
        if wanted not in factor_ids:
            listed = ", ".join(available_subregions(ds)) or "none"
            error(
                "UNKNOWN_SUBREGION",
                f"parish.toml says [grid] egrid_subregion = \"{settings.egrid_subregion}\", but factors.csv has no "
                f"row {wanted}. The subregions it has are: {listed}. Check the code against EPA's Power Profiler.",
            )
        if any(m.fuel == "natural_gas" for m in ds.meters) and NATURAL_GAS_FACTOR_ID not in factor_ids:
            error("MISSING_FACTOR", f"factors.csv has no row {NATURAL_GAS_FACTOR_ID}, which prices the natural gas")
        if ds.commute and GASOLINE_FACTOR_ID not in factor_ids:
            error(
                "MISSING_FACTOR",
                f"factors.csv has no row {GASOLINE_FACTOR_ID}, which the travel line's comparison needs",
            )
        driven = {c.vehicle_id for c in ds.commute}
        for v in ds.vehicles:
            needed = grid_factor_id(v.charging_grid_region)
            if v.vehicle_id in driven and v.charging_place == "home" and needed not in factor_ids:
                listed = ", ".join(available_subregions(ds)) or "none"
                error(
                    "UNKNOWN_SUBREGION",
                    f"vehicle {v.vehicle_id}: charging_grid_region {v.charging_grid_region!r} has no row {needed} "
                    f"in factors.csv. The subregions it has are: {listed}.",
                )
    # The edition and revision text of the factors a parish uses is shown on the Methodology page (Spanish too),
    # so it gets the same free-text checks. A used factor with no Spanish wording is a warning on a site built
    # in Spanish: that page would show the English.
    if settings is None:
        used = ds.factors
    else:
        campus = replace(ds, grid_subregion=settings.egrid_subregion)  # the settings decide, whatever ds carries
        used = factors_in_use(campus, travel=settings.travel_enabled and bool(ds.commute))
    for f in used:
        place = f"factor {f.factor_id}"
        for name, text in (
            ("edition", f.edition),
            ("revision", f.revision),
            ("edition_es", f.edition_es),
            ("revision_es", f.revision_es),
        ):
            check_text(place, name, text)
        if not spanish:
            continue
        for name, text in (("edition_es", f.edition_es), ("revision_es", f.revision_es)):
            if not text.strip():
                warn(
                    "FACTOR_NO_SPANISH",
                    f"{place}: {name} is empty, so the Spanish Methodology page shows the English text; "
                    f"add the Spanish wording in factors.csv",
                )

    # Milestones, vehicles and commute months.
    for m in ds.milestones:
        place = f"milestone {m.date}"
        if m.kind not in MILESTONE_KINDS:
            error("INVALID_VALUE", f"{place}: kind {m.kind!r} is not one of {', '.join(MILESTONE_KINDS)}")
        if m.precision not in MILESTONE_PRECISIONS:
            error(
                "INVALID_VALUE",
                f"{place}: precision {m.precision!r} is not one of {', '.join(MILESTONE_PRECISIONS)}",
            )
        check_text(place, "label_en", m.label_en)
        check_text(place, "label_es", m.label_es)
        if spanish and not m.label_es.strip():
            error("MISSING_SPANISH", f"{place}: label_es is empty, and the site is also built in Spanish")
        # The About page mentions solar only once a dated solar_connected milestone exists. Enforced here, in the
        # data, so no other milestone label can bring the word onto the site early.
        if m.kind != "solar_connected":
            for name, text in (("label_en", m.label_en), ("label_es", m.label_es)):
                if _SOLAR_WORD_RE.search(text):
                    error(
                        "SOLAR_LABEL",
                        f"{place}: {name} says solar, but only a solar_connected milestone may; "
                        "use kind solar_connected or reword the label",
                    )
    vehicle_ids = {v.vehicle_id for v in ds.vehicles}
    for vehicle_id, n in Counter(v.vehicle_id for v in ds.vehicles).items():
        if n > 1:
            error("DUPLICATE_VEHICLE", f"{vehicle_id}: listed {n} times in vehicles.csv")
    for v in ds.vehicles:
        if not v.kwh_per_mile > 0:
            error("INVALID_VALUE", f"vehicle {v.vehicle_id}: kwh_per_mile {_fmt(v.kwh_per_mile)} must be above zero")
        check_text(f"vehicle {v.vehicle_id}", "label", v.label)
        check_text(f"vehicle {v.vehicle_id}", "kwh_per_mile_source", v.kwh_per_mile_source)
        check_text(f"vehicle {v.vehicle_id}", "note", v.note)
    for (vehicle_id, month), n in Counter((c.vehicle_id, c.period_end_month) for c in ds.commute).items():
        if n > 1:
            error(
                "DUPLICATE_COMMUTE",
                f"{vehicle_id} {month}: {n} rows in commute.csv for the same month; edit the existing row "
                f"instead of adding a second one",
            )
    for c in ds.commute:
        place = f"commute {c.vehicle_id} {c.period_end_month}"
        if c.vehicle_id not in vehicle_ids:
            error("UNKNOWN_VEHICLE", f"{place}: vehicle {c.vehicle_id!r} is not in vehicles.csv")
        if c.data_quality not in COMMUTE_QUALITIES:
            error(
                "INVALID_VALUE",
                f"{place}: data_quality {c.data_quality!r} is not one of {', '.join(COMMUTE_QUALITIES)}",
            )
        if c.miles < 0:
            error("INVALID_VALUE", f"{place}: miles {_fmt(c.miles)} is below zero")
        check_text(place, "note", c.note)

    # Duplicate readings: one row per meter per billing month.
    counts = Counter((r.meter_id, r.period_end_month) for r, _ in usable)
    for (meter_id, month), n in counts.items():
        if n > 1:
            error("DUPLICATE_READING", f"{meter_id} {month}: {n} readings for the same billing month")

    # Overlapping dated periods on the same meter. Utility periods are contiguous, so two periods may
    # share one boundary day (one ends the day the next starts); sharing two or more days is an error.
    dated: dict[str, list[Reading]] = {}
    for r in ds.readings:
        if r.period_start is not None and r.period_end is not None and r.period_end >= r.period_start:
            dated.setdefault(r.meter_id, []).append(r)
    for meter_id in sorted(dated):
        ordered = sorted(dated[meter_id], key=lambda x: (x.period_start, x.period_end))
        for i, a in enumerate(ordered):
            for b in ordered[i + 1 :]:
                if b.period_start > a.period_end:
                    break  # sorted by start, so nothing later can overlap a
                shared_days = (min(a.period_end, b.period_end) - b.period_start).days + 1
                if shared_days > 1:
                    error(
                        "PERIOD_OVERLAP",
                        f"{meter_id}: {a.period_end_month} ({a.period_start} to {a.period_end}) overlaps "
                        f"{b.period_end_month} ({b.period_start} to {b.period_end}) by {shared_days} days",
                    )

    # Series checks, one meter at a time (the first reading wins when a month is duplicated).
    series: dict[str, dict[int, Reading]] = {}
    for r, month in usable:
        series.setdefault(r.meter_id, {}).setdefault(month, r)

    for meter_id in sorted(series):
        by_month = series[meter_id]
        present = sorted(by_month)

        # Gaps: months with no reading between the first and last reading.
        gap_start: int | None = None
        for month in range(present[0], present[-1] + 1):
            if month not in by_month:
                gap_start = month if gap_start is None else gap_start
            elif gap_start is not None:
                span = _month_label(gap_start) if gap_start == month - 1 else (
                    f"{_month_label(gap_start)} to {_month_label(month - 1)}"
                )
                warn("GAP", f"{meter_id}: no reading for {span}")
                gap_start = None

        # Plausibility against the same month one year earlier.
        for month in present:
            earlier = by_month.get(month - 12)
            if earlier is None or earlier.quantity <= 0:
                continue
            now = by_month[month]
            change = (now.quantity - earlier.quantity) / earlier.quantity
            if abs(change) > PLAUSIBILITY_LIMIT + 1e-9:
                direction = "above" if change > 0 else "below"
                warn(
                    "PLAUSIBILITY",
                    f"{meter_id} {_month_label(month)}: {_fmt(now.quantity)} {now.unit} is "
                    f"{abs(change):.0%} {direction} {_month_label(month - 12)} ({_fmt(earlier.quantity)} "
                    f"{earlier.unit}); check the figure",
                )

    return issues
