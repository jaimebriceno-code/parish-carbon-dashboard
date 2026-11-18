"""Data model and CSV loading for the carbon dashboard.

Every dataclass is frozen. `load_dataset` reads the CSV files in a data
directory and turns them into a `Dataset`. The parish settings decide two things
about the reading: with travel switched off the two travel files are not read at
all (they may be missing), and an English-only site needs no Spanish milestone
labels. It refuses any file that has a
column which could carry dollar amounts or account identifiers, and it raises
`ValueError` (naming the file, line and column) for anything it cannot parse.

Judging whether the numbers make sense (units, gaps, duplicates, plausibility)
is the validator's job, in `carbon/validate.py`.
"""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal

Fuel = Literal["electricity", "natural_gas"]
MilestoneKind = Literal["boiler", "solar_connected", "other"]

FUELS: tuple[str, ...] = ("electricity", "natural_gas")
UNIT_FOR_FUEL: dict[str, str] = {"electricity": "kWh", "natural_gas": "therm"}
DATA_QUALITIES: tuple[str, ...] = ("bill", "chart_estimate", "chart_measured", "utility_export")
COMMUTE_QUALITIES: tuple[str, ...] = ("estimate", "odometer")
MILESTONE_KINDS: tuple[str, ...] = ("boiler", "solar_connected", "other")
# How much of a milestone's date is known. A year-only milestone is stored as 1 January of that year (a date
# is required), and the site then shows only the year, so it never states a day nobody knows.
MILESTONE_PRECISIONS: tuple[str, ...] = ("year", "month", "day")

# Column names that must never appear in any data file (matched by exact name,
# ignoring case and surrounding spaces). Dollar amounts, account numbers and
# meter numbers are never stored in this repository.
FORBIDDEN_COLUMNS: frozenset[str] = frozenset(
    {
        "account",
        "account_number",
        "account_no",
        "acct",
        "acct_number",
        "meter_number",
        "meter_no",
        "meter_serial",
        "amount",
        "amount_usd",
        "amount_due",
        "cost",
        "cost_usd",
        "price",
        "charge",
        "charges",
        "dollars",
        "usd",
        "total_due",
    }
)

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


@dataclass(frozen=True)
class Meter:
    meter_id: str  # a made-up slug such as "electric-main", never an account or meter number
    fuel: Fuel
    label: str
    notes: str = ""


@dataclass(frozen=True)
class Reading:
    meter_id: str
    period_end_month: str  # "YYYY-MM", the month the billing period ends in
    quantity: float
    unit: str  # "kWh" for electricity, "therm" for natural_gas
    data_quality: str  # bill | chart_estimate | chart_measured | utility_export
    period_start: date | None = None  # period_start and period_end: both or neither
    period_end: date | None = None
    peak_kw: float | None = None
    generation_kwh: float | None = None
    export_kwh: float | None = None
    uncertainty_pct: float | None = None
    note: str = ""


@dataclass(frozen=True)
class Milestone:
    date: date
    kind: MilestoneKind
    label_en: str
    label_es: str
    precision: str = "day"  # year | month | day: how much of `date` is known


@dataclass(frozen=True)
class Factor:
    factor_id: str
    value: float
    unit: str
    gases: str
    source: str
    edition: str
    revision: str
    url: str
    retrieved: str  # ISO date the builder fetched it
    # The Spanish Methodology page shows these two in place of `edition` and `revision`, which stay English (the
    # feed publishes only the English). Empty means no Spanish text: the page then falls back to the English.
    edition_es: str = ""
    revision_es: str = ""


@dataclass(frozen=True)
class Vehicle:
    vehicle_id: str
    label: str
    kwh_per_mile: float
    charging_place: str  # "home" or "church"
    charging_grid_region: str  # eGRID subregion where it charges; any electricity_<subregion> row in factors.csv
    kwh_per_mile_source: str = ""  # where the efficiency figure came from (internal: never published)
    note: str = ""  # free text for whoever maintains the data (internal: never published)


@dataclass(frozen=True)
class CommuteMonth:
    vehicle_id: str
    period_end_month: str  # "YYYY-MM"
    miles: float
    data_quality: str  # estimate | odometer
    note: str = ""


@dataclass(frozen=True)
class Dataset:
    meters: list[Meter] = field(default_factory=list)
    readings: list[Reading] = field(default_factory=list)
    milestones: list[Milestone] = field(default_factory=list)
    factors: list[Factor] = field(default_factory=list)
    vehicles: list[Vehicle] = field(default_factory=list)
    commute: list[CommuteMonth] = field(default_factory=list)
    # Two facts about the campus that come from parish.toml, carried here because the calculation needs them:
    # the eGRID subregion whose `electricity_<subregion>` factor prices the campus electricity ("" when the
    # dataset was built without settings; pricing electricity then raises), and the calendar year the
    # baseline moves to once all twelve of its billing months are in.
    grid_subregion: str = ""
    baseline_year: int = 2025


# --- parsing helpers -------------------------------------------------------


class _Row:
    """One CSV row, with parse helpers that raise ValueError naming file, line and column."""

    def __init__(self, file: str, line: int, data: dict):
        self._file = file
        self._line = line
        self._data = data

    def _fail(self, column: str, problem: str) -> ValueError:
        return ValueError(f"{self._file} line {self._line}, column {column!r}: {problem}")

    def text(self, column: str) -> str:
        return (self._data.get(column) or "").strip()

    def required(self, column: str) -> str:
        value = self.text(column)
        if not value:
            raise self._fail(column, "a value is required")
        return value

    def month(self, column: str) -> str:
        value = self.required(column)
        if not _MONTH_RE.match(value):
            raise self._fail(column, f"expected YYYY-MM, got {value!r}")
        return value

    def number(self, column: str) -> float:
        return self._to_number(column, self.required(column))

    def optional_number(self, column: str) -> float | None:
        value = self.text(column)
        return self._to_number(column, value) if value else None

    def required_date(self, column: str) -> date:
        return self._to_date(column, self.required(column))

    def optional_date(self, column: str) -> date | None:
        value = self.text(column)
        return self._to_date(column, value) if value else None

    def _to_number(self, column: str, value: str) -> float:
        try:
            number = float(value)
        except ValueError:
            raise self._fail(column, f"not a number: {value!r}") from None
        if not math.isfinite(number):
            raise self._fail(column, f"not a finite number: {value!r}")
        return number

    def _to_date(self, column: str, value: str) -> date:
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise self._fail(column, f"expected YYYY-MM-DD, got {value!r}") from None


def _read_rows(path: Path, required: tuple[str, ...], optional: tuple[str, ...] = ()) -> list[_Row]:
    """Read a CSV whose columns must be exactly the `required` ones plus any of the `optional` ones."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        forbidden = [name for name in header if name.strip().lower() in FORBIDDEN_COLUMNS]
        if forbidden:
            raise ValueError(
                f"{path.name}: forbidden column(s) {', '.join(repr(n) for n in forbidden)}. "
                "Dollar amounts, account numbers and meter numbers never go in the data files."
            )
        known = required + optional
        unexpected = [name for name in header if name not in known]
        if unexpected:
            raise ValueError(
                f"{path.name}: unexpected column(s) {', '.join(repr(n) for n in unexpected)}; "
                f"the known columns are {', '.join(known)}"
            )
        missing = [name for name in required if name not in header]
        if missing:
            raise ValueError(f"{path.name}: missing required column(s) {', '.join(missing)}")
        rows = []
        for data in reader:
            if None in data:
                raise ValueError(
                    f"{path.name} line {reader.line_num}: more values than columns "
                    "(quote any text that contains a comma)"
                )
            rows.append(_Row(path.name, reader.line_num, data))
        return rows


# --- loaders ---------------------------------------------------------------


def _load_meters(path: Path) -> list[Meter]:
    return [
        Meter(
            meter_id=row.required("meter_id"),
            fuel=row.required("fuel"),
            label=row.text("label"),
            notes=row.text("notes"),
        )
        for row in _read_rows(path, ("meter_id", "fuel", "label"), ("notes",))
    ]


def _load_readings(path: Path) -> list[Reading]:
    required = ("meter_id", "period_end_month", "quantity", "unit", "data_quality")
    optional = (
        "period_start",
        "period_end",
        "peak_kw",
        "generation_kwh",
        "export_kwh",
        "uncertainty_pct",
        "note",
    )
    return [
        Reading(
            meter_id=row.required("meter_id"),
            period_end_month=row.month("period_end_month"),
            quantity=row.number("quantity"),
            unit=row.required("unit"),
            data_quality=row.required("data_quality"),
            period_start=row.optional_date("period_start"),
            period_end=row.optional_date("period_end"),
            peak_kw=row.optional_number("peak_kw"),
            generation_kwh=row.optional_number("generation_kwh"),
            export_kwh=row.optional_number("export_kwh"),
            uncertainty_pct=row.optional_number("uncertainty_pct"),
            note=row.text("note"),
        )
        for row in _read_rows(path, required, optional)
    ]


def _load_milestones(path: Path, spanish: bool = True) -> list[Milestone]:
    """`spanish` False is the English-only site: the `label_es` column may be left out or left empty."""
    required = ("date", "kind", "label_en", "label_es") if spanish else ("date", "kind", "label_en")
    optional = ("precision",) if spanish else ("label_es", "precision")
    return [
        Milestone(
            date=row.required_date("date"),
            kind=row.required("kind"),
            label_en=row.required("label_en"),
            label_es=row.required("label_es") if spanish else row.text("label_es"),
            precision=row.text("precision") or "day",
        )
        for row in _read_rows(path, required, optional)
    ]


def _load_factors(path: Path) -> list[Factor]:
    columns = ("factor_id", "value", "unit", "gases", "source", "edition", "revision", "url", "retrieved")
    return [
        Factor(
            factor_id=row.required("factor_id"),
            value=row.number("value"),
            unit=row.required("unit"),
            gases=row.text("gases"),
            source=row.text("source"),
            edition=row.text("edition"),
            revision=row.text("revision"),
            url=row.text("url"),
            retrieved=row.text("retrieved"),
            edition_es=row.text("edition_es"),
            revision_es=row.text("revision_es"),
        )
        for row in _read_rows(path, columns, ("edition_es", "revision_es"))
    ]


def _load_vehicles(path: Path) -> list[Vehicle]:
    columns = ("vehicle_id", "label", "kwh_per_mile", "charging_place", "charging_grid_region")
    return [
        Vehicle(
            vehicle_id=row.required("vehicle_id"),
            label=row.text("label"),
            kwh_per_mile=row.number("kwh_per_mile"),
            charging_place=row.required("charging_place"),
            charging_grid_region=row.required("charging_grid_region"),
            kwh_per_mile_source=row.text("kwh_per_mile_source"),
            note=row.text("note"),
        )
        for row in _read_rows(path, columns, ("kwh_per_mile_source", "note"))
    ]


def _load_commute(path: Path) -> list[CommuteMonth]:
    return [
        CommuteMonth(
            vehicle_id=row.required("vehicle_id"),
            period_end_month=row.month("period_end_month"),
            miles=row.number("miles"),
            data_quality=row.required("data_quality"),
            note=row.text("note"),
        )
        for row in _read_rows(path, ("vehicle_id", "period_end_month", "miles", "data_quality"), ("note",))
    ]


def load_dataset(data_dir: Path, settings=None) -> Dataset:
    """Read meters, readings, milestones, factors, vehicles and commute from `data_dir`.

    `settings` is the parish's `carbon.settings.Settings`. With it, the dataset carries the campus's grid
    subregion and baseline year; an English-only site may leave out the Spanish milestone labels; and with
    travel switched off `vehicles.csv` and `commute.csv` are not read at all, so they may be missing.
    Without it (None) every file is read and Spanish labels are required, as for a bilingual site with travel.

    The files that are read must exist; the factors, vehicles and commute files may be header-only.
    `factors.csv` may hold any number of `electricity_<subregion>` rows; all are loaded, and the validator
    and the calculation pick the one the settings name. Each file may contain only its known columns (a typo
    such as `period_strt` is an error, not silently ignored data), and never a forbidden one.
    """
    data_dir = Path(data_dir)
    spanish = settings is None or "es" in settings.languages
    travel = settings is None or settings.travel_enabled
    extra = {} if settings is None else {"grid_subregion": settings.egrid_subregion, "baseline_year": settings.baseline_year}
    return Dataset(
        meters=_load_meters(data_dir / "meters.csv"),
        readings=_load_readings(data_dir / "readings.csv"),
        milestones=_load_milestones(data_dir / "milestones.csv", spanish),
        factors=_load_factors(data_dir / "factors.csv"),
        vehicles=_load_vehicles(data_dir / "vehicles.csv") if travel else [],
        commute=_load_commute(data_dir / "commute.csv") if travel else [],
        **extra,
    )
