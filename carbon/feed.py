"""The public, read-only JSON feed (version 1) served under `/api/v1/`.

`build_feed(ds, generated_at, settings)` turns a dataset that has already passed `carbon.validate` into a
mapping of relative paths to file text. It touches no disk except to read the repository's own schema files
(`schema/*.schema.json`) and changelog (`static/api-changelog.md`), which are published verbatim.

The shape of these files is a promise to outside readers, such as a diocese adding up its parishes. Every
parish built from this template publishes the same shape, so one reader works for all of them. Within v1 only
additive changes are allowed: new files, new fields, new rows. A breaking change creates `/api/v2/`.

Who the parish is (name, place, slug, contact) comes from `parish.toml`. So do three things a reader can see:
`sample_data` (true while the site still shows the template's made-up numbers: a reader adding parishes up
must skip those), `languages`, and whether there is a travel line at all. With travel switched off the two
travel files, their index entries, their schema and the `staff_travel` block of `annual.json` are all absent.

Rules this module enforces by construction (the tests pin them):

- Output is reproducible. JSON is written with sorted keys, two-space indent, no ASCII escaping and a
  trailing newline. Rows are ordered (meter or vehicle id, then month). `generated_at`, in `index.json`
  only, is the sole time-dependent value, and it is passed in.
- Free-text `note` and `notes` fields from the CSVs never appear. Neither do dollar amounts or account or
  meter numbers.
- Only the emission factors this parish uses are published (`carbon.calc.factors_in_use`), however many
  subregions the factors file holds.
- Staff travel (church-related driving, scope 3, GHG category business travel) lives in its own
  block and its own files. It is never added to a campus total or the baseline, and the avoided figure is
  labelled as not an offset. Vehicle labels and efficiency are not published, only the vehicle slug.
- Emissions are rounded to six decimal places (grams), quantities to three, so float noise never reaches
  a reader and monthly rows sum to the annual totals within a few millionths of a tonne.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from carbon.calc import (
    EXACT_QUALITIES,
    CommuteResult,
    MonthEmissions,
    Period,
    commute_results,
    factors_in_use,
    monthly_emissions,
    select_baseline,
    twelve_month_periods,
)
from carbon.models import Dataset
from carbon.settings import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = REPO_ROOT / "schema"
CHANGELOG_PATH = REPO_ROOT / "static" / "api-changelog.md"

API_VERSION = "v1"
API_DIR = "api/v1"

BOUNDARY_STATEMENT = (
    "The boundary is the campus: the utility meters listed under meters, and nothing else. "
    "Scope 1 is natural gas combustion. Scope 2 is purchased electricity, location-based."
)
BOUNDARY_TRAVEL = (
    " Staff travel (church-related driving by clergy and staff in an electric vehicle) is reported separately as "
    "scope 3 and is never added to the campus totals or the baseline."
)

LICENCE_NAME = "CC BY 4.0"
LICENCE_URL = "https://creativecommons.org/licenses/by/4.0/"

# The only wording about offsetting anywhere in the feed. Do not add other uses of the word.
AVOIDED_NOTE = "Avoided emissions compared with a gasoline car; not an offset and not subtracted from campus totals."
COUNTED_IN_CAMPUS_NOTE = (
    "Where a vehicle charges at the church, its electricity is already counted in campus electricity, "
    "so no emissions are counted here and tco2e_counted_here is null."
)
EMISSIONS_UNIT = "tCO2e"  # metric tonnes CO2e

SCOPE_FOR_FUEL = {"electricity": 2, "natural_gas": 1}
BASIS_CALENDAR_PREFIX = "calendar_"  # plus the baseline year: calendar_2025
BASIS_LATEST = "latest_twelve_billing_months"
TRAVEL_GHG_CATEGORY = "business_travel"

TONNE_PLACES = 6
QUANTITY_PLACES = 3
SHARE_PLACES = 4

SCHEMA_NAMES = ("index", "organization", "monthly", "annual", "travel", "factors", "milestones")
TRAVEL_FILE_IDS = ("travel", "travel_csv")  # absent, with schema/travel.schema.json, when travel is switched off


@dataclass(frozen=True)
class FeedFile:
    file_id: str
    path: str  # relative to /api/v1/
    media_type: str
    schema: str | None  # schema name under schema/, or None
    description: str


FILES: tuple[FeedFile, ...] = (
    FeedFile("index", "index.json", "application/json", "index",
             "Discovery document: version, last updated, links to every file, licence and contact."),
    FeedFile("organization", "organization.json", "application/json", "organization",
             "Stable organization slug, name, location, the campus boundary and the meters included."),
    FeedFile("monthly", "monthly.json", "application/json", "monthly",
             "One row per meter per billing month: quantity, unit, tonnes CO2e, data quality and factor id."),
    FeedFile("monthly_csv", "monthly.csv", "text/csv", None, "The rows of monthly.json as CSV."),
    FeedFile("annual", "annual.json", "application/json", "annual",
             "The baseline and every complete twelve-month period, with campus totals by fuel (scope 1 and 2). "
             "Where staff travel is tracked it is a separate block (scope 3), never added to the campus totals."),
    FeedFile("travel", "travel.json", "application/json", "travel",
             "Staff travel by vehicle slug and billing month: miles, emissions where counted here and the "
             "avoided figure (scope 3)."),
    FeedFile("travel_csv", "travel.csv", "text/csv", None, "The rows of travel.json as CSV."),
    FeedFile("factors", "factors.json", "application/json", "factors",
             "Every emission factor with value, unit, source, edition, revision, URL and retrieval date."),
    FeedFile("milestones", "milestones.json", "application/json", "milestones",
             "Dated milestones such as a boiler installation, labelled in each language of the site."),
    FeedFile("openapi", "openapi.json", "application/json", None, "OpenAPI description of these read-only paths."),
    FeedFile("changelog", "CHANGELOG.md", "text/markdown", None, "Changes to the feed, newest first."),
)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def feed_files(settings: Settings) -> tuple[FeedFile, ...]:
    """The files this parish's feed holds: all of `FILES`, less the travel ones when travel is switched off."""
    if settings.travel_enabled:
        return FILES
    return tuple(f for f in FILES if f.file_id not in TRAVEL_FILE_IDS)


def _campus_description(settings: Settings) -> str:
    return f"The campus of {settings.name} in {settings.city}, {settings.state}."


# --- formatting ----------------------------------------------------------------


def _round(value: float, places: int) -> float:
    return round(value, places) + 0.0  # + 0.0 turns -0.0 into 0.0


def _tonnes(value: float) -> float:
    return _round(value, TONNE_PLACES)


def _quantity(value: float) -> float:
    return _round(value, QUANTITY_PLACES)


def _json(document: object) -> str:
    return json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value)


def _csv(columns: tuple[str, ...], rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_cell(row[column]) for column in columns])
    return buffer.getvalue()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --- campus --------------------------------------------------------------------

MONTHLY_COLUMNS = (
    "meter_id",
    "fuel",
    "scope",
    "period_end_month",
    "quantity",
    "unit",
    "tco2e",
    "data_quality",
    "estimated",
    "uncertainty_pct",
    "factor_id",
)


def _monthly_rows(ds: Dataset, priced: list[MonthEmissions]) -> list[dict]:
    uncertainty = {(r.meter_id, r.period_end_month): r.uncertainty_pct for r in ds.readings}
    rows = []
    for row in sorted(priced, key=lambda r: (r.meter_id, r.period_end_month)):
        pct = uncertainty.get((row.meter_id, row.period_end_month))
        rows.append(
            {
                "meter_id": row.meter_id,
                "fuel": row.fuel,
                "scope": SCOPE_FOR_FUEL[row.fuel],
                "period_end_month": row.period_end_month,
                "quantity": _quantity(row.quantity),
                "unit": row.unit,
                "tco2e": _tonnes(row.tco2e),
                "data_quality": row.data_quality,
                "estimated": row.data_quality not in EXACT_QUALITIES,
                "uncertainty_pct": None if pct is None else _round(pct, 2),
                "factor_id": row.factor_id,
            }
        )
    return rows


def _period_json(period: Period, priced: list[MonthEmissions]) -> dict:
    months = set(period.months)
    includes_estimates = any(r.period_end_month in months and r.data_quality not in EXACT_QUALITIES for r in priced)
    return {
        "label": period.label,
        "months": list(period.months),
        "electricity": {
            "scope": SCOPE_FOR_FUEL["electricity"],
            "quantity": _quantity(period.electricity_kwh),
            "unit": "kWh",
            "tco2e": _tonnes(period.tco2e_electricity),
        },
        "natural_gas": {
            "scope": SCOPE_FOR_FUEL["natural_gas"],
            "quantity": _quantity(period.gas_therms),
            "unit": "therm",
            "tco2e": _tonnes(period.tco2e_gas),
        },
        "tco2e_total": _tonnes(period.tco2e_total),
        "estimated_share": _round(period.estimated_share, SHARE_PLACES),
        "includes_estimates": includes_estimates,
        "provisional": period.provisional,
    }


def _campus_json(baseline: Period | None, periods: list[Period], priced: list[MonthEmissions], baseline_year: int) -> dict:
    baseline_json = None
    if baseline is not None:
        baseline_json = _period_json(baseline, priced)
        calendar = baseline.label == str(baseline_year)
        baseline_json["basis"] = f"{BASIS_CALENDAR_PREFIX}{baseline_year}" if calendar else BASIS_LATEST
    return {
        "baseline": baseline_json,
        "periods": [_period_json(period, priced) for period in periods],
    }


# --- staff travel (scope 3) ----------------------------------------------------

TRAVEL_COLUMNS = (
    "vehicle_id",
    "period_end_month",
    "miles",
    "tco2e_counted_here",
    "counted_in_campus",
    "avoided_tco2e",
    "data_quality",
    "estimated",
)


def _travel_rows(results: list[CommuteResult]) -> list[dict]:
    rows = []
    for r in sorted(results, key=lambda r: (r.vehicle_id, r.period_end_month)):
        rows.append(
            {
                "vehicle_id": r.vehicle_id,
                "period_end_month": r.period_end_month,
                "miles": _round(r.miles, QUANTITY_PLACES),
                # Null, not zero: a car that charges at the church has its electricity in the campus meter already.
                "tco2e_counted_here": None if r.counted_in_campus else _tonnes(r.tco2e_counted_here),
                "counted_in_campus": r.counted_in_campus,
                "avoided_tco2e": _tonnes(r.avoided_tco2e),
                "data_quality": r.data_quality,
                "estimated": r.data_quality != "odometer",
            }
        )
    return rows


def _staff_travel_block(rows: list[dict], periods: list[Period]) -> dict:
    """Per vehicle, per campus period: the travel months that fall inside it, summed.

    A period the vehicle only partly covers is reported with `complete` false rather than skipped or padded.
    """
    by_vehicle: dict[str, list[dict]] = {}
    for row in rows:
        by_vehicle.setdefault(row["vehicle_id"], []).append(row)

    vehicles = []
    for vehicle_id in sorted(by_vehicle):
        vehicle_rows = by_vehicle[vehicle_id]
        counted_in_campus = vehicle_rows[0]["counted_in_campus"]
        vehicle_periods = []
        for period in periods:
            inside = [r for r in vehicle_rows if r["period_end_month"] in period.months]
            if not inside:
                continue
            vehicle_periods.append(
                {
                    "label": period.label,
                    "months_reported": len(inside),
                    "complete": len(inside) == len(period.months),
                    "miles": _round(sum(r["miles"] for r in inside), QUANTITY_PLACES),
                    "tco2e_counted_here": None
                    if counted_in_campus
                    else _tonnes(sum(r["tco2e_counted_here"] for r in inside)),
                    "avoided_tco2e": _tonnes(sum(r["avoided_tco2e"] for r in inside)),
                    "includes_estimates": any(r["estimated"] for r in inside),
                }
            )
        vehicles.append({"vehicle_id": vehicle_id, "counted_in_campus": counted_in_campus, "periods": vehicle_periods})

    return {
        "scope": 3,
        "ghg_category": TRAVEL_GHG_CATEGORY,
        "included_in_campus_totals": False,
        "emissions_unit": EMISSIONS_UNIT,
        "avoided_note": AVOIDED_NOTE,
        "counted_in_campus_note": COUNTED_IN_CAMPUS_NOTE,
        "vehicles": vehicles,
    }


# --- the small documents -------------------------------------------------------


def _organization_block(settings: Settings) -> dict:
    return {
        "slug": settings.slug,
        "name": settings.name,
        "city": settings.city,
        "state": settings.state,
        "zip": settings.zip,
    }


def _organization_json(ds: Dataset, settings: Settings) -> dict:
    document = {
        "version": API_VERSION,
        **_organization_block(settings),
        "campus": _campus_description(settings),
        "boundary": BOUNDARY_STATEMENT + (BOUNDARY_TRAVEL if settings.travel_enabled else ""),
        "egrid_subregion": settings.egrid_subregion,
        "sample_data": settings.sample_data,
        "meters": [
            {"meter_id": m.meter_id, "fuel": m.fuel, "label": m.label}
            for m in sorted(ds.meters, key=lambda m: m.meter_id)
        ],
    }
    if settings.website:
        document["website"] = settings.website
    return document


def _index_json(generated_at: str, settings: Settings) -> dict:
    files = {}
    for f in feed_files(settings):
        entry = {"path": f.path, "media_type": f.media_type, "description": f.description}
        if f.schema is not None:
            entry["schema"] = f"schema/{f.schema}.schema.json"
        files[f.file_id] = entry
    return {
        "version": API_VERSION,
        "generated_at": generated_at,
        "organization": _organization_block(settings),
        "licence": {"name": LICENCE_NAME, "url": LICENCE_URL},
        "contact": settings.contact_email,
        "sample_data": settings.sample_data,
        "languages": list(settings.languages),
        "files": files,
    }


def _factors_json(ds: Dataset, settings: Settings) -> dict:
    return {
        "version": API_VERSION,
        "factors": [
            {
                "factor_id": f.factor_id,
                "value": f.value,
                "unit": f.unit,
                "gases": f.gases,
                "source": f.source,
                "edition": f.edition,
                "revision": f.revision,
                "url": f.url,
                "retrieved": f.retrieved,
            }
            for f in sorted(factors_in_use(ds, travel=settings.travel_enabled), key=lambda f: f.factor_id)
        ],
    }


def _milestones_json(ds: Dataset) -> dict:
    return {
        "version": API_VERSION,
        "milestones": [
            {
                "date": m.date.isoformat(),
                "date_precision": m.precision,
                "kind": m.kind,
                "label_en": m.label_en,
                "label_es": m.label_es,
            }
            for m in sorted(ds.milestones, key=lambda m: (m.date, m.kind, m.label_en))
        ],
    }


def _openapi_json(settings: Settings) -> dict:
    cors = {
        "Access-Control-Allow-Origin": {
            "description": "Always *: any site may read the feed.",
            "schema": {"type": "string"},
        }
    }
    paths = {}
    for f in feed_files(settings):
        if f.media_type == "application/json" and f.schema is not None:
            body = {"$ref": f"schema/{f.schema}.schema.json"}
        elif f.media_type == "application/json":
            body = {"type": "object"}
        else:
            body = {"type": "string"}
        paths[f"/{f.path}"] = {
            "get": {
                "operationId": f"get_{f.file_id}",
                "summary": f.description,
                "responses": {
                    "200": {
                        "description": "The file.",
                        "headers": cors,
                        "content": {f.media_type: {"schema": body}},
                    }
                },
            }
        }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": f"{settings.name} carbon data feed",
            "version": API_VERSION,
            "description": (
                "Read-only, static files. Within v1 only additive changes are made; a breaking change "
                "creates v2 and leaves v1 up."
            ),
            "license": {"name": LICENCE_NAME, "url": LICENCE_URL},
            "contact": {"email": settings.contact_email},
        },
        "servers": [
            {"url": f"{settings.path_prefix}/{API_DIR}", "description": "The same host that serves this document."}
        ],
        "paths": paths,
    }


# --- entry point ---------------------------------------------------------------


def _check_generated_at(generated_at: str) -> None:
    if not isinstance(generated_at, str) or not _DATE_RE.match(generated_at):
        raise ValueError(f"generated_at must be a YYYY-MM-DD date string, got {generated_at!r}")
    date.fromisoformat(generated_at)  # also rejects impossible dates such as 2026-13-01


def build_feed(ds: Dataset, generated_at: str, settings: Settings) -> dict[str, str]:
    """Build every feed file. Keys are paths relative to the site root (`api/v1/...`), values are file text.

    Assumes `ds` has passed `carbon.validate`. A dataset with no complete baseline gives a null baseline
    and no periods; an empty commute gives empty travel files. Neither raises. With travel switched off in
    the settings there are no travel files and `annual.json` has no `staff_travel` block.
    """
    _check_generated_at(generated_at)

    priced = monthly_emissions(ds)
    periods = twelve_month_periods(ds)
    baseline = select_baseline(ds)
    monthly_rows = _monthly_rows(ds, priced)

    annual = {
        "version": API_VERSION,
        "emissions_unit": EMISSIONS_UNIT,
        "sample_data": settings.sample_data,
        "campus": _campus_json(baseline, periods, priced, ds.baseline_year),
    }
    monthly = {"version": API_VERSION, "emissions_unit": EMISSIONS_UNIT, "rows": monthly_rows}

    files = {
        "index.json": _json(_index_json(generated_at, settings)),
        "organization.json": _json(_organization_json(ds, settings)),
        "monthly.json": _json(monthly),
        "monthly.csv": _csv(MONTHLY_COLUMNS, monthly_rows),
        "factors.json": _json(_factors_json(ds, settings)),
        "milestones.json": _json(_milestones_json(ds)),
        "openapi.json": _json(_openapi_json(settings)),
        "CHANGELOG.md": _read_text(CHANGELOG_PATH),
    }
    schema_names = list(SCHEMA_NAMES)
    if settings.travel_enabled:
        travel_rows = _travel_rows(commute_results(ds))
        annual["staff_travel"] = _staff_travel_block(travel_rows, periods)
        files["travel.json"] = _json(
            {
                "version": API_VERSION,
                "scope": 3,
                "ghg_category": TRAVEL_GHG_CATEGORY,
                "included_in_campus_totals": False,
                "emissions_unit": EMISSIONS_UNIT,
                "avoided_note": AVOIDED_NOTE,
                "counted_in_campus_note": COUNTED_IN_CAMPUS_NOTE,
                "rows": travel_rows,
            }
        )
        files["travel.csv"] = _csv(TRAVEL_COLUMNS, travel_rows)
    else:
        schema_names.remove("travel")
    files["annual.json"] = _json(annual)
    for name in schema_names:
        files[f"schema/{name}.schema.json"] = _read_text(SCHEMA_DIR / f"{name}.schema.json")

    return {f"{API_DIR}/{name}": text for name, text in sorted(files.items())}
