"""Emissions calculation for the carbon dashboard.

Everything here is a pure function of a `Dataset`; nothing is read from disk and nothing is rounded.
Rounding is for the presentation layers.

Units and boundaries (see the design spec, "Calculation", "Baseline year" and "Staff travel"):

- Emissions are metric tonnes CO2e: quantity times the factor in kg, divided by 1000.
- Scope 1 is natural gas. Scope 2 is purchased electricity, location-based.
- Billing months are used as reported, labelled `YYYY-MM` by the month the period ends.
  A period is twelve consecutive billing months. It is complete only when `ds.meters` has an
  electricity meter and a natural gas meter and every meter has a reading for all twelve months;
  a missing month is never filled with a zero.
- Staff travel (called commute in the code and data files) is scope 3, category 6 (business travel). It is calculated here but never added to a campus `Period`.

The functions assume data that has passed `carbon.validate`. They still raise `ValueError` (naming the
offending item) where carrying on would mean guessing: an unknown meter, vehicle or factor, a
duplicate reading, an unrecognised charging place.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace

from carbon.models import FUELS, Dataset, Factor

# Which factor prices each fuel. Natural gas has one factor for the whole country. Electricity uses the row for
# the campus's eGRID subregion: `electricity_` plus the subregion in lower case (electricity_rfcw for RFCW). The
# factors file may hold a row for every subregion; only the dataset's own is used.
NATURAL_GAS_FACTOR_ID = "natural_gas"
GASOLINE_FACTOR_ID = "gasoline_car_per_mile"
GRID_FACTOR_PREFIX = "electricity_"

# Readings of these qualities are exact. Anything else (chart_estimate, chart_measured) is an estimate.
EXACT_QUALITIES: frozenset[str] = frozenset({"bill", "utility_export"})

PERIOD_MONTHS = 12
CALENDAR_BASELINE_YEAR = 2025  # the default for Dataset.baseline_year; a parish sets its own in parish.toml

_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


# --- result types ------------------------------------------------------------


@dataclass(frozen=True)
class MonthEmissions:
    """One reading priced with its factor."""

    meter_id: str
    fuel: str
    period_end_month: str  # "YYYY-MM"
    quantity: float
    unit: str
    tco2e: float
    data_quality: str
    factor_id: str


@dataclass(frozen=True)
class Period:
    """Twelve consecutive billing months, summed over every meter."""

    label: str  # the baseline year, such as "2025", for January to December of it; otherwise "YYYY-MM to YYYY-MM"
    months: tuple[str, ...]  # twelve "YYYY-MM" strings, oldest first
    electricity_kwh: float
    gas_therms: float
    tco2e_electricity: float  # scope 2, location-based
    tco2e_gas: float  # scope 1
    tco2e_total: float
    estimated_share: float  # fraction of tco2e_total that comes from estimated readings, 0.0 to 1.0
    provisional: bool


@dataclass(frozen=True)
class CommuteResult:
    """One vehicle's commute for one month (scope 3, category 6, business travel). Never part of a campus total."""

    vehicle_id: str
    period_end_month: str
    miles: float
    ev_kwh: float
    tco2e_counted_here: float  # 0.0 when charging at the church: already in campus electricity
    counted_in_campus: bool
    avoided_tco2e: float  # compared with a gasoline car; informational, not an offset
    data_quality: str


# --- factors -----------------------------------------------------------------


def _find_factor(ds: Dataset, factor_id: str) -> Factor | None:
    return next((f for f in ds.factors if f.factor_id == factor_id), None)


def factor_value(ds: Dataset, factor_id: str) -> float:
    """The factor's value in kg CO2e per its unit (kWh, therm or mile). Raises ValueError if it is absent."""
    factor = _find_factor(ds, factor_id)
    if factor is None:
        raise ValueError(f"no emission factor {factor_id!r} in the factors data")
    return factor.value


def grid_factor_id(subregion: str) -> str:
    """The factor id for an eGRID subregion: "RFCW" gives "electricity_rfcw"."""
    return GRID_FACTOR_PREFIX + subregion.strip().lower()


def available_subregions(ds: Dataset) -> list[str]:
    """The eGRID subregions the factors data has a row for, in capitals, sorted."""
    return sorted(
        {f.factor_id[len(GRID_FACTOR_PREFIX):].upper() for f in ds.factors if f.factor_id.startswith(GRID_FACTOR_PREFIX)}
    )


def factor_id_for_fuel(ds: Dataset, fuel: str) -> str | None:
    """The id of the factor that prices `fuel` on this campus, or None for a fuel that has none.

    Raises ValueError when electricity is asked for and the dataset does not say which subregion it is in.
    """
    if fuel == "natural_gas":
        return NATURAL_GAS_FACTOR_ID
    if fuel == "electricity":
        if not ds.grid_subregion:
            raise ValueError(
                "the dataset does not say which eGRID subregion the campus is in "
                "(parish.toml, [grid] egrid_subregion), so electricity cannot be priced"
            )
        return grid_factor_id(ds.grid_subregion)
    return None


def factors_in_use(ds: Dataset, *, travel: bool = True) -> list[Factor]:
    """The factors this parish's figures are built on, in the order of the factors file.

    That is the campus's own electricity row, natural gas and, with `travel`, the gasoline-car comparison and
    the grid row of any other subregion a listed vehicle charges in away from the campus. Every other
    `electricity_<subregion>` row in the file is somebody else's and is neither used nor published.
    """
    wanted = {NATURAL_GAS_FACTOR_ID}
    if ds.grid_subregion:
        wanted.add(grid_factor_id(ds.grid_subregion))
    if travel:
        wanted.add(GASOLINE_FACTOR_ID)
        driven = {month.vehicle_id for month in ds.commute}
        for vehicle in ds.vehicles:
            if vehicle.vehicle_id in driven and vehicle.charging_place == "home":
                wanted.add(grid_factor_id(vehicle.charging_grid_region))
    return [f for f in ds.factors if f.factor_id in wanted]


# --- campus emissions --------------------------------------------------------


def monthly_emissions(ds: Dataset) -> list[MonthEmissions]:
    """Price every reading, in reading order. Gaps stay gaps: no row exists for a month with no reading."""
    fuel_of = {meter.meter_id: meter.fuel for meter in ds.meters}
    kg_per_unit: dict[str, tuple[str, float]] = {}  # fuel -> (factor_id, kg CO2e per unit), looked up once

    rows = []
    for reading in ds.readings:
        fuel = fuel_of.get(reading.meter_id)
        if fuel is None:
            raise ValueError(
                f"reading {reading.meter_id!r} {reading.period_end_month}: that meter is not listed in the meters data"
            )
        if fuel not in kg_per_unit:
            factor_id = factor_id_for_fuel(ds, fuel)
            if factor_id is None:
                raise ValueError(f"meter {reading.meter_id!r}: no emission factor is defined for fuel {fuel!r}")
            kg_per_unit[fuel] = (factor_id, factor_value(ds, factor_id))
        factor_id, factor = kg_per_unit[fuel]
        rows.append(
            MonthEmissions(
                meter_id=reading.meter_id,
                fuel=fuel,
                period_end_month=reading.period_end_month,
                quantity=reading.quantity,
                unit=reading.unit,
                tco2e=reading.quantity * factor / 1000.0,
                data_quality=reading.data_quality,
                factor_id=factor_id,
            )
        )
    return rows


def _month_index(month: str) -> int:
    match = _MONTH_RE.match(month)
    if match is None:
        raise ValueError(f"expected a billing month as YYYY-MM, got {month!r}")
    return int(match.group(1)) * 12 + int(match.group(2)) - 1


def _month_label(index: int) -> str:
    year, month = divmod(index, 12)
    return f"{year:04d}-{month + 1:02d}"


def _period_label(months: tuple[str, ...], baseline_year: int) -> str:
    if months[0] == f"{baseline_year}-01" and months[-1] == f"{baseline_year}-12":
        return str(baseline_year)
    return f"{months[0]} to {months[-1]}"


def _build_period(months: tuple[str, ...], rows: list[MonthEmissions], baseline_year: int) -> Period:
    electricity = [r for r in rows if r.fuel == "electricity"]
    gas = [r for r in rows if r.fuel == "natural_gas"]
    tco2e_electricity = math.fsum(r.tco2e for r in electricity)
    tco2e_gas = math.fsum(r.tco2e for r in gas)
    tco2e_total = tco2e_electricity + tco2e_gas
    estimated = [r for r in rows if r.data_quality not in EXACT_QUALITIES]
    if tco2e_total > 0:
        estimated_share = min(1.0, math.fsum(r.tco2e for r in estimated) / tco2e_total)
    else:
        estimated_share = 0.0
    return Period(
        label=_period_label(months, baseline_year),
        months=months,
        electricity_kwh=math.fsum(r.quantity for r in electricity),
        gas_therms=math.fsum(r.quantity for r in gas),
        tco2e_electricity=tco2e_electricity,
        tco2e_gas=tco2e_gas,
        tco2e_total=tco2e_total,
        estimated_share=estimated_share,
        provisional=bool(estimated),
    )


def twelve_month_periods(ds: Dataset) -> list[Period]:
    """Every window of twelve consecutive billing months in which every meter has all twelve readings.

    The dataset must list at least one electricity meter and one natural gas meter; otherwise there are
    no complete windows. Windows are returned oldest first, one per end month. A window with any missing
    month is skipped; nothing is filled in. `provisional` is True when any reading in the window is an estimate.
    """
    rows = monthly_emissions(ds)
    by_meter_and_month: dict[tuple[str, str], MonthEmissions] = {}
    for row in rows:
        key = (row.meter_id, row.period_end_month)
        if key in by_meter_and_month:
            raise ValueError(f"{row.meter_id!r} has more than one reading for {row.period_end_month}")
        by_meter_and_month[key] = row

    # A baseline needs both fuels. Without an electricity meter and a gas meter, no window is complete,
    # because a one-fuel total would understate the footprint.
    if not set(FUELS) <= {meter.fuel for meter in ds.meters}:
        return []

    meter_ids = list(dict.fromkeys(meter.meter_id for meter in ds.meters))
    periods = []
    for end_month in sorted({row.period_end_month for row in rows}, key=_month_index):
        last = _month_index(end_month)
        months = tuple(_month_label(i) for i in range(last - PERIOD_MONTHS + 1, last + 1))
        keys = [(meter_id, month) for meter_id in meter_ids for month in months]
        if all(key in by_meter_and_month for key in keys):
            periods.append(_build_period(months, [by_meter_and_month[key] for key in keys], ds.baseline_year))
    return periods


def select_baseline(ds: Dataset) -> Period | None:
    """The complete baseline calendar year (`ds.baseline_year`) if the data has it, else the latest complete
    window (marked provisional).

    Returns None when no window of twelve months is complete for every meter.
    """
    periods = twelve_month_periods(ds)
    if not periods:
        return None
    for period in periods:
        if period.label == str(ds.baseline_year):
            return period
    return replace(periods[-1], provisional=True)


# --- staff travel (scope 3, category 6; the code and data files call it commute) ---


def commute_results(ds: Dataset) -> list[CommuteResult]:
    """Price each commute month, in the order given.

    EV electricity is miles times the vehicle's kWh per mile. At home, its emissions use the grid factor
    for the vehicle's charging region and are counted only here; avoided emissions are the gasoline-car
    emissions for the same miles minus those. At the church, the electricity is already in the campus
    meter, so nothing is counted here and the avoided figure is the gasoline-car emissions in full.
    """
    vehicles = {vehicle.vehicle_id: vehicle for vehicle in ds.vehicles}
    results = []
    for month in ds.commute:
        vehicle = vehicles.get(month.vehicle_id)
        if vehicle is None:
            raise ValueError(
                f"commute {month.vehicle_id!r} {month.period_end_month}: no vehicle with that id in the vehicles data"
            )
        ev_kwh = month.miles * vehicle.kwh_per_mile
        gasoline_tco2e = month.miles * factor_value(ds, GASOLINE_FACTOR_ID) / 1000.0

        if vehicle.charging_place == "home":
            factor_id = grid_factor_id(vehicle.charging_grid_region)
            grid = _find_factor(ds, factor_id)
            if grid is None:
                raise ValueError(
                    f"vehicle {vehicle.vehicle_id!r} charges in grid region {vehicle.charging_grid_region!r}, "
                    f"but the factors data has no {factor_id!r} row; add that region's eGRID factor"
                )
            counted_here = ev_kwh * grid.value / 1000.0
            counted_in_campus = False
            avoided = gasoline_tco2e - counted_here
        elif vehicle.charging_place == "church":
            counted_here = 0.0
            counted_in_campus = True
            avoided = gasoline_tco2e
        else:
            raise ValueError(
                f"vehicle {vehicle.vehicle_id!r}: charging place {vehicle.charging_place!r} is not 'home' or 'church'"
            )

        results.append(
            CommuteResult(
                vehicle_id=month.vehicle_id,
                period_end_month=month.period_end_month,
                miles=month.miles,
                ev_kwh=ev_kwh,
                tco2e_counted_here=counted_here,
                counted_in_campus=counted_in_campus,
                avoided_tco2e=avoided,
                data_quality=month.data_quality,
            )
        )
    return results
