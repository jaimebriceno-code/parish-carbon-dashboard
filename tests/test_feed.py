"""Tests for the public JSON feed (version 1): carbon.feed, schema/*.schema.json, static/*.

The feed's shape is a promise to outside readers, so these tests pin the paths, the schemas, the labels
and the things that must never appear (dollars, long digit runs, free-text notes, offset claims). Who the
parish is, whether it has a travel line, its grid subregion and the sample-data flag all come from the settings.
"""

import csv
import io
import json
import re
from dataclasses import replace
from datetime import date, timedelta

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from carbon.calc import commute_results, select_baseline
from carbon.feed import build_feed
from carbon.models import CommuteMonth, Dataset, Factor, Meter, Milestone, Reading, Vehicle
from tests.support import CONTACT, ORG_NAME, ORG_SLUG, ROOT, SAMPLE_MILES_PER_DAY, SAMPLE_VEHICLE, SETTINGS, sample_dataset, settings

GENERATED_AT = "2026-11-17"
API = "api/v1/"

EXPECTED_PATHS = {
    API + name
    for name in (
        "index.json",
        "organization.json",
        "monthly.json",
        "monthly.csv",
        "annual.json",
        "travel.json",
        "travel.csv",
        "factors.json",
        "milestones.json",
        "openapi.json",
        "CHANGELOG.md",
        "schema/index.schema.json",
        "schema/organization.schema.json",
        "schema/monthly.schema.json",
        "schema/annual.schema.json",
        "schema/travel.schema.json",
        "schema/factors.schema.json",
        "schema/milestones.schema.json",
    )
}

AVOIDED_NOTE = "Avoided emissions compared with a gasoline car; not an offset and not subtracted from campus totals."

# Free text that must never reach the feed. Each value is planted in the synthetic dataset below.
SENTINELS = (
    "SENTINEL-METER-NOTE",
    "SENTINEL-READING-NOTE",
    "SENTINEL-TRAVEL-NOTE",
    "SENTINEL-VEHICLE-LABEL",
)


# --- fixtures ------------------------------------------------------------------


def _factor(factor_id, value, unit):
    return Factor(
        factor_id=factor_id,
        value=value,
        unit=unit,
        gases="CO2, CH4, N2O",
        source="test source",
        edition="test edition",
        revision="test revision",
        url="https://example.org/factors",
        retrieved="2026-01-01",
    )


FACTORS = [
    _factor("electricity_rfcw", 0.4, "kg CO2e/kWh"),
    _factor("natural_gas", 5.3, "kg CO2e/therm"),
    _factor("gasoline_car_per_mile", 0.4, "kg CO2e/mile"),
]

METERS = [
    Meter("electric-main", "electricity", "Campus electricity", "SENTINEL-METER-NOTE"),
    Meter("gas-main", "natural_gas", "Campus natural gas"),
]


def _months(first, count):
    year, month = int(first[:4]), int(first[5:7])
    out = []
    for _ in range(count):
        out.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def _synthetic(*, vehicles=True, month_count=14, drop=()):
    """Two meters over `month_count` billing months from 2025-09, with estimates in the oldest months."""
    readings = []
    for i, month in enumerate(_months("2025-09", month_count)):
        if ("electric-main", month) not in drop:
            readings.append(
                Reading(
                    "electric-main",
                    month,
                    1000.0 + 10 * i,
                    "kWh",
                    "chart_estimate" if i < 2 else "bill",
                    note="SENTINEL-READING-NOTE" if i == 0 else "",
                )
            )
        if ("gas-main", month) not in drop:
            measured = i < 5
            readings.append(
                Reading(
                    "gas-main",
                    month,
                    100.0 + i * 3.5,
                    "therm",
                    "chart_measured" if measured else "bill",
                    uncertainty_pct=5.0 if measured else None,
                )
            )
    # Listed out of order on purpose: the feed sorts by meter id, then month.
    readings.reverse()

    vehicle_rows, commute_rows = [], []
    if vehicles:
        vehicle_rows = [
            Vehicle("ev-home", "SENTINEL-VEHICLE-LABEL", 0.3, "home", "RFCW"),
            Vehicle("ev-church", "SENTINEL-VEHICLE-LABEL", 0.25, "church", "RFCW"),
        ]
        commute_rows = [
            CommuteMonth("ev-home", m, 400.0, "estimate", "SENTINEL-TRAVEL-NOTE") for m in _months("2025-11", 12)
        ] + [CommuteMonth("ev-church", m, 300.0, "odometer") for m in _months("2026-08", 3)]

    return Dataset(
        meters=list(METERS),
        readings=readings,
        milestones=[Milestone(date(2021, 1, 1), "boiler", "Boiler installed (2021)", "Caldera instalada (2021)")],
        factors=list(FACTORS),
        vehicles=vehicle_rows,
        commute=commute_rows,
        grid_subregion="RFCW",
    )


@pytest.fixture(scope="module")
def seed_dataset():
    """The frozen sample parish (the fixture name is kept from when the template was one church's seed data)."""
    return sample_dataset()


@pytest.fixture(scope="module")
def seed_feed(seed_dataset):
    return build_feed(seed_dataset, GENERATED_AT, SETTINGS)


@pytest.fixture(scope="module")
def synthetic_feed():
    return build_feed(_synthetic(), GENERATED_AT, SETTINGS)


def _load(feed, name):
    return json.loads(feed[API + name])


def _schema(feed, name):
    return json.loads(feed[f"{API}schema/{name}.schema.json"])


def _validate_every_json_file(feed):
    checked = []
    for path, text in feed.items():
        if not path.endswith(".json") or path.startswith(API + "schema/") or path == API + "openapi.json":
            continue
        name = path[len(API) : -len(".json")]
        schema = _schema(feed, name)
        Draft202012Validator.check_schema(schema)
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(json.loads(text)))
        assert not errors, f"{path}: " + "; ".join(f"{list(e.absolute_path)}: {e.message}" for e in errors[:3])
        checked.append(name)
    return checked


def _csv_rows(text):
    return list(csv.DictReader(io.StringIO(text)))


# --- paths and format ----------------------------------------------------------


def test_feed_paths_are_exactly_the_published_list(seed_feed, synthetic_feed):
    assert set(seed_feed) == EXPECTED_PATHS
    assert set(synthetic_feed) == EXPECTED_PATHS
    assert list(seed_feed) == sorted(seed_feed)  # stable order too


def test_json_files_are_sorted_indented_utf8_with_trailing_newline(synthetic_feed):
    for path, text in synthetic_feed.items():
        if not path.endswith(".json") or path.startswith(API + "schema/"):
            continue
        assert text.endswith("\n") and not text.endswith("\n\n"), path
        assert text == json.dumps(json.loads(text), sort_keys=True, indent=2, ensure_ascii=False) + "\n", path
    # ensure_ascii=False: the Spanish milestone label is written as is, not as \u escapes.
    assert "Caldera instalada (2021)" in synthetic_feed[API + "milestones.json"]


def test_feed_is_reproducible_and_only_generated_at_varies(seed_dataset):
    first = build_feed(seed_dataset, GENERATED_AT, SETTINGS)
    assert build_feed(seed_dataset, GENERATED_AT, SETTINGS) == first
    later = build_feed(seed_dataset, "2026-12-18", SETTINGS)
    assert {path for path in first if first[path] != later[path]} == {API + "index.json"}
    assert json.loads(later[API + "index.json"])["generated_at"] == "2026-12-18"


@pytest.mark.parametrize("bad", ["17 Nov 2026", "2026-13-01", "2026-11-17T00:00:00", "", "20261117"])
def test_generated_at_must_be_a_real_iso_date(bad):
    with pytest.raises(ValueError):
        build_feed(Dataset(), bad, SETTINGS)


def test_monthly_rows_are_sorted_by_meter_then_month_and_csv_matches_json(synthetic_feed):
    rows = _load(synthetic_feed, "monthly.json")["rows"]
    keys = [(r["meter_id"], r["period_end_month"]) for r in rows]
    assert keys == sorted(keys)
    assert len(rows) == 28
    csv_rows = _csv_rows(synthetic_feed[API + "monthly.csv"])
    assert [(r["meter_id"], r["period_end_month"]) for r in csv_rows] == keys
    assert [float(r["tco2e"]) for r in csv_rows] == [r["tco2e"] for r in rows]
    assert [float(r["quantity"]) for r in csv_rows] == [r["quantity"] for r in rows]
    header = synthetic_feed[API + "monthly.csv"].splitlines()[0]
    assert header == (
        "meter_id,fuel,scope,period_end_month,quantity,unit,tco2e,data_quality,estimated,uncertainty_pct,factor_id"
    )


def test_monthly_rows_carry_the_quality_scope_and_factor(synthetic_feed):
    rows = {(r["meter_id"], r["period_end_month"]): r for r in _load(synthetic_feed, "monthly.json")["rows"]}
    first_gas = rows[("gas-main", "2025-09")]
    assert first_gas["scope"] == 1 and first_gas["unit"] == "therm"
    assert first_gas["data_quality"] == "chart_measured" and first_gas["estimated"] is True
    assert first_gas["uncertainty_pct"] == 5.0
    assert first_gas["factor_id"] == "natural_gas"
    assert first_gas["tco2e"] == pytest.approx(100.0 * 5.3 / 1000, abs=1e-9)
    latest_electric = rows[("electric-main", "2026-10")]
    assert latest_electric["scope"] == 2 and latest_electric["unit"] == "kWh"
    assert latest_electric["data_quality"] == "bill" and latest_electric["estimated"] is False
    assert latest_electric["uncertainty_pct"] is None
    assert latest_electric["factor_id"] == "electricity_rfcw"


# --- schemas -------------------------------------------------------------------


def test_every_json_file_validates_against_its_schema(seed_feed, synthetic_feed):
    assert sorted(_validate_every_json_file(seed_feed)) == sorted(
        ["index", "organization", "monthly", "annual", "travel", "factors", "milestones"]
    )
    _validate_every_json_file(synthetic_feed)
    # Every schema is itself a valid draft 2020-12 schema.
    for path, text in synthetic_feed.items():
        if path.startswith(API + "schema/"):
            Draft202012Validator.check_schema(json.loads(text))


def _add_unknown_fields(node):
    if isinstance(node, dict):
        for value in list(node.values()):
            _add_unknown_fields(value)
        node["x_future_field"] = {"anything": [1, 2, 3]}
    elif isinstance(node, list):
        for item in node:
            _add_unknown_fields(item)


def test_extra_unknown_field_still_validates(synthetic_feed):
    """Schemas use additionalProperties: true everywhere, so an additive change cannot break a consumer."""
    for path, text in synthetic_feed.items():
        if not path.endswith(".json") or path.startswith(API + "schema/") or path == API + "openapi.json":
            continue
        name = path[len(API) : -len(".json")]
        document = json.loads(text)
        _add_unknown_fields(document)
        errors = list(Draft202012Validator(_schema(synthetic_feed, name)).iter_errors(document))
        assert not errors, f"{path}: {errors[0].message}"


def test_schemas_never_forbid_additional_properties(synthetic_feed):
    def walk(node, where):
        if isinstance(node, dict):
            assert node.get("additionalProperties") is not False, where
            if "properties" in node:
                assert node.get("additionalProperties") is True, f"{where}: object schemas state additionalProperties"
            for key, value in node.items():
                walk(value, f"{where}/{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{where}[{i}]")

    for path, text in synthetic_feed.items():
        if path.startswith(API + "schema/"):
            walk(json.loads(text), path)


def test_a_document_missing_a_promised_field_does_not_validate(synthetic_feed):
    """The schemas still say something: dropping a required field is an error."""
    annual = _load(synthetic_feed, "annual.json")
    del annual["campus"]
    assert list(Draft202012Validator(_schema(synthetic_feed, "annual")).iter_errors(annual))
    monthly = _load(synthetic_feed, "monthly.json")
    del monthly["rows"][0]["tco2e"]
    assert list(Draft202012Validator(_schema(synthetic_feed, "monthly")).iter_errors(monthly))


# --- annual: campus totals ----------------------------------------------------


def _check_period_against_monthly(period, monthly_rows):
    inside = [r for r in monthly_rows if r["period_end_month"] in period["months"]]
    assert len(period["months"]) == 12
    assert len(inside) == 24  # two meters, twelve months each
    # Monthly rows are rounded to six places, so the sums agree to a few millionths of a tonne.
    assert period["tco2e_total"] == pytest.approx(sum(r["tco2e"] for r in inside), abs=2e-5)
    for fuel in ("electricity", "natural_gas"):
        mine = [r for r in inside if r["fuel"] == fuel]
        assert period[fuel]["tco2e"] == pytest.approx(sum(r["tco2e"] for r in mine), abs=2e-5)
        assert period[fuel]["quantity"] == pytest.approx(sum(r["quantity"] for r in mine), abs=1e-6)
    assert period["tco2e_total"] == pytest.approx(period["electricity"]["tco2e"] + period["natural_gas"]["tco2e"], abs=2e-6)
    assert period["electricity"]["scope"] == 2 and period["electricity"]["unit"] == "kWh"
    assert period["natural_gas"]["scope"] == 1 and period["natural_gas"]["unit"] == "therm"


def test_annual_campus_total_equals_sum_of_monthly_rows(seed_feed, synthetic_feed):
    for feed in (seed_feed, synthetic_feed):
        annual = _load(feed, "annual.json")
        monthly_rows = _load(feed, "monthly.json")["rows"]
        campus = annual["campus"]
        assert campus["periods"], "the data has at least one complete twelve-month period"
        for period in campus["periods"]:
            _check_period_against_monthly(period, monthly_rows)
        _check_period_against_monthly(campus["baseline"], monthly_rows)
        assert campus["baseline"]["label"] == campus["periods"][-1]["label"]


def test_baseline_matches_the_calc_engine_and_is_labelled(seed_dataset, seed_feed):
    baseline = _load(seed_feed, "annual.json")["campus"]["baseline"]
    expected = select_baseline(seed_dataset)
    assert baseline["label"] == expected.label == "2025-11 to 2026-10"
    assert baseline["months"] == list(expected.months)
    assert baseline["provisional"] is True
    assert baseline["basis"] == "latest_twelve_billing_months"
    assert baseline["includes_estimates"] is True  # the seed gas figures are chart readings
    assert 0 < baseline["estimated_share"] <= 1
    assert baseline["tco2e_total"] == pytest.approx(expected.tco2e_total, abs=1e-5)


def test_provisional_baseline_without_estimates_is_not_called_estimated(synthetic_feed):
    campus = _load(synthetic_feed, "annual.json")["campus"]
    assert [p["label"] for p in campus["periods"]] == [
        "2025-09 to 2026-08",
        "2025-10 to 2026-09",
        "2025-11 to 2026-10",
    ]
    older, _, newest = campus["periods"]
    assert older["includes_estimates"] is True and older["provisional"] is True
    # Gas months 2025-09 to 2026-01 are chart_measured, so even the newest window still holds estimates.
    assert newest["includes_estimates"] is True

    # With every figure an exact bill the baseline is still provisional (it is not calendar 2025),
    # but it is not estimated.
    ds = _synthetic(vehicles=False)
    bills = [replace(r, data_quality="bill", uncertainty_pct=None) for r in ds.readings]
    baseline = _load(build_feed(replace(ds, readings=bills), GENERATED_AT, SETTINGS), "annual.json")["campus"]["baseline"]
    assert baseline["provisional"] is True
    assert baseline["includes_estimates"] is False
    assert baseline["estimated_share"] == 0.0


def test_calendar_2025_baseline_is_used_when_complete():
    ds = _synthetic(vehicles=False)
    ds = replace(
        ds,
        readings=[
            Reading("electric-main", m, 1000.0, "kWh", "bill") for m in _months("2025-01", 12)
        ]
        + [Reading("gas-main", m, 100.0, "therm", "bill") for m in _months("2025-01", 12)]
        + [Reading("electric-main", "2026-01", 900.0, "kWh", "bill"), Reading("gas-main", "2026-01", 90.0, "therm", "bill")],
    )
    campus = _load(build_feed(ds, GENERATED_AT, SETTINGS), "annual.json")["campus"]
    assert campus["baseline"]["label"] == "2025"
    assert campus["baseline"]["basis"] == "calendar_2025"
    assert campus["baseline"]["provisional"] is False
    assert campus["baseline"]["includes_estimates"] is False


def test_feed_without_a_complete_baseline_is_valid_and_null(seed_dataset):
    short = _synthetic(month_count=11, vehicles=True)
    gaps = _synthetic(month_count=14, drop={("gas-main", "2026-03")})
    for ds in (short, gaps, Dataset(), replace(seed_dataset, readings=[])):
        feed = build_feed(ds, GENERATED_AT, SETTINGS)
        annual = _load(feed, "annual.json")
        assert annual["campus"]["baseline"] is None
        assert annual["campus"]["periods"] == []
        assert annual["staff_travel"]["scope"] == 3
        _validate_every_json_file(feed)


# --- staff travel (scope 3) ---------------------------------------------------


def test_staff_travel_never_in_campus_totals(synthetic_feed):
    with_travel = _load(synthetic_feed, "annual.json")
    plain = _load(build_feed(replace(_synthetic(), commute=[], vehicles=[]), GENERATED_AT, SETTINGS), "annual.json")
    assert with_travel["campus"] == plain["campus"]
    assert with_travel["staff_travel"]["vehicles"], "the travel block is populated"
    assert with_travel["staff_travel"]["included_in_campus_totals"] is False
    # The campus monthly rows hold meters only; the vehicles never appear among them.
    rows = _load(synthetic_feed, "monthly.json")["rows"]
    assert {r["meter_id"] for r in rows} == {"electric-main", "gas-main"}
    assert "ev-home" not in synthetic_feed[API + "monthly.json"]
    assert "ev-home" not in json.dumps(with_travel["campus"])
    # And none of the travel miles or emissions leak into a campus period's numbers.
    newest = with_travel["campus"]["periods"][-1]
    assert newest["tco2e_total"] == plain["campus"]["periods"][-1]["tco2e_total"]


def test_staff_travel_block_is_scope_3_business_travel_with_the_avoided_note(synthetic_feed, seed_feed):
    for feed in (synthetic_feed, seed_feed):
        annual = _load(feed, "annual.json")
        block = annual["staff_travel"]
        assert "staff_commute" not in annual
        assert block["scope"] == 3
        assert block["ghg_category"] == "business_travel"
        assert block["avoided_note"] == AVOIDED_NOTE
        travel = _load(feed, "travel.json")
        assert travel["scope"] == 3
        assert travel["ghg_category"] == "business_travel"
        assert travel["avoided_note"] == AVOIDED_NOTE
        assert travel["included_in_campus_totals"] is False


def test_travel_rows_count_home_charging_here_and_church_charging_in_campus(synthetic_feed):
    travel = _load(synthetic_feed, "travel.json")
    rows = travel["rows"]
    keys = [(r["vehicle_id"], r["period_end_month"]) for r in rows]
    assert keys == sorted(keys)
    home = [r for r in rows if r["vehicle_id"] == "ev-home"]
    church = [r for r in rows if r["vehicle_id"] == "ev-church"]
    assert len(home) == 12 and len(church) == 3

    # Home: 400 mi x 0.3 kWh/mi x 0.4 kg/kWh = 48 kg; a gasoline car would emit 400 x 0.4 = 160 kg.
    first = home[0]
    assert first["miles"] == 400.0
    assert first["counted_in_campus"] is False
    assert first["tco2e_counted_here"] == pytest.approx(0.048, abs=1e-9)
    assert first["avoided_tco2e"] == pytest.approx(0.112, abs=1e-9)
    assert first["data_quality"] == "estimate" and first["estimated"] is True

    # Church: the charging electricity is already in the campus meter, so nothing is counted here.
    only = church[0]
    assert only["counted_in_campus"] is True
    assert only["tco2e_counted_here"] is None
    assert only["avoided_tco2e"] == pytest.approx(300 * 0.4 / 1000, abs=1e-9)
    assert only["data_quality"] == "odometer" and only["estimated"] is False
    assert "already counted in campus electricity" in travel["counted_in_campus_note"].lower()

    csv_rows = _csv_rows(synthetic_feed[API + "travel.csv"])
    assert [(r["vehicle_id"], r["period_end_month"]) for r in csv_rows] == keys
    church_csv = [r for r in csv_rows if r["vehicle_id"] == "ev-church"][0]
    assert church_csv["tco2e_counted_here"] == "" and church_csv["counted_in_campus"] == "true"
    assert synthetic_feed[API + "travel.csv"].splitlines()[0] == (
        "vehicle_id,period_end_month,miles,tco2e_counted_here,counted_in_campus,avoided_tco2e,data_quality,estimated"
    )


def test_annual_travel_block_rolls_up_by_vehicle_over_the_campus_periods(synthetic_feed):
    block = _load(synthetic_feed, "annual.json")["staff_travel"]
    assert [v["vehicle_id"] for v in block["vehicles"]] == ["ev-church", "ev-home"]
    church, home = block["vehicles"]
    assert church["counted_in_campus"] is True and home["counted_in_campus"] is False

    by_label = {p["label"]: p for p in home["periods"]}
    full = by_label["2025-11 to 2026-10"]
    assert full["months_reported"] == 12 and full["complete"] is True
    assert full["miles"] == 4800.0
    assert full["tco2e_counted_here"] == pytest.approx(12 * 0.048, abs=1e-6)
    assert full["avoided_tco2e"] == pytest.approx(12 * 0.112, abs=1e-6)
    assert full["includes_estimates"] is True
    partial = by_label["2025-09 to 2026-08"]  # the vehicle only has ten of these twelve months
    assert partial["months_reported"] == 10 and partial["complete"] is False and partial["miles"] == 4000.0

    newest_church = {p["label"]: p for p in church["periods"]}["2025-11 to 2026-10"]
    assert newest_church["months_reported"] == 3 and newest_church["complete"] is False
    assert newest_church["tco2e_counted_here"] is None
    assert newest_church["avoided_tco2e"] == pytest.approx(3 * 0.12, abs=1e-6)
    assert newest_church["includes_estimates"] is False


def test_empty_travel_feed_is_valid(seed_dataset):
    # The sample holds a travel line, so the empty case is the sample without it.
    feed = build_feed(replace(seed_dataset, vehicles=[], commute=[]), GENERATED_AT, SETTINGS)
    assert _validate_every_json_file(feed)
    travel = _load(feed, "travel.json")
    assert travel["rows"] == []
    assert travel["scope"] == 3
    block = _load(feed, "annual.json")["staff_travel"]
    assert block["vehicles"] == [] and block["scope"] == 3
    # The CSV is just its header row.
    assert feed[API + "travel.csv"].splitlines() == [
        "vehicle_id,period_end_month,miles,tco2e_counted_here,counted_in_campus,avoided_tco2e,data_quality,estimated"
    ]


SEED_TRAVEL_MONTHS = [f"2025-{m:02d}" for m in (11, 12)] + [f"2026-{m:02d}" for m in range(1, 11)]


def _weekdays_in(month):
    year, number = int(month[:4]), int(month[5:7])
    day, count = date(year, number, 1), 0
    while day.month == number:
        count += day.weekday() < 5
        day += timedelta(days=1)
    return count


def test_seed_travel_feed_has_the_twelve_months_and_the_totals(seed_dataset, seed_feed):
    results = commute_results(seed_dataset)
    counted = sum(r.tco2e_counted_here for r in results)
    avoided = sum(r.avoided_tco2e for r in results)

    travel = _load(seed_feed, "travel.json")
    assert _validate_every_json_file(seed_feed)
    assert travel["included_in_campus_totals"] is False and travel["scope"] == 3
    rows = travel["rows"]
    assert [(r["vehicle_id"], r["period_end_month"]) for r in rows] == [(SAMPLE_VEHICLE, m) for m in SEED_TRAVEL_MONTHS]
    for row in rows:
        assert row["miles"] == SAMPLE_MILES_PER_DAY * _weekdays_in(row["period_end_month"])
        assert (row["data_quality"], row["estimated"], row["counted_in_campus"]) == ("estimate", True, False)
    assert sum(r["miles"] for r in rows) == 6500
    assert sum(r["tco2e_counted_here"] for r in rows) == pytest.approx(counted, abs=1e-4)
    assert sum(r["avoided_tco2e"] for r in rows) == pytest.approx(avoided, abs=1e-4)

    # The same twelve rows as CSV.
    csv_rows = _csv_rows(seed_feed[API + "travel.csv"])
    assert [(r["vehicle_id"], r["period_end_month"], float(r["miles"])) for r in csv_rows] == [
        (r["vehicle_id"], r["period_end_month"], r["miles"]) for r in rows
    ]
    assert sum(float(r["tco2e_counted_here"]) for r in csv_rows) == pytest.approx(counted, abs=1e-4)
    assert {r["counted_in_campus"] for r in csv_rows} == {"false"} and {r["estimated"] for r in csv_rows} == {"true"}

    # The annual file rolls the twelve months up over the campus period (the baseline's twelve billing months).
    block = _load(seed_feed, "annual.json")["staff_travel"]
    assert block["avoided_note"] == AVOIDED_NOTE
    (vehicle,) = block["vehicles"]
    assert vehicle["vehicle_id"] == SAMPLE_VEHICLE and vehicle["counted_in_campus"] is False
    period = {p["label"]: p for p in vehicle["periods"]}["2025-11 to 2026-10"]
    assert period["months_reported"] == 12 and period["complete"] is True and period["includes_estimates"] is True
    assert period["miles"] == 6500
    assert period["tco2e_counted_here"] == pytest.approx(counted, abs=1e-4)
    assert period["avoided_tco2e"] == pytest.approx(avoided, abs=1e-4)
    assert 0 < counted < avoided

    # index.json only lists the files (no figures live there); it links the two travel files and says nothing
    # about the vehicle, and the vehicle's label, efficiency and source stay out of every file.
    index = _load(seed_feed, "index.json")
    assert index["files"]["travel"]["path"] == "travel.json" and index["files"]["travel_csv"]["path"] == "travel.csv"
    assert "staff car" not in json.dumps(index).lower()
    vehicle_row = seed_dataset.vehicles[0]
    for path, text in seed_feed.items():
        for private in (vehicle_row.kwh_per_mile_source, vehicle_row.note, vehicle_row.label, "fueleconomy", "kwh_per_mile"):
            assert private not in text, f"{path} publishes {private!r}"


def test_seed_travel_leaves_the_campus_totals_and_the_baseline_unchanged(seed_dataset, seed_feed):
    plain = build_feed(replace(seed_dataset, vehicles=[], commute=[]), GENERATED_AT, SETTINGS)
    for name in ("annual.json",):
        with_travel, without = _load(seed_feed, name), _load(plain, name)
        assert with_travel["campus"] == without["campus"]
    for path in (API + "monthly.json", API + "monthly.csv", API + "organization.json", API + "factors.json"):
        assert seed_feed[path] == plain[path], path
    baseline = _load(seed_feed, "annual.json")["campus"]["baseline"]
    assert baseline["tco2e_total"] == pytest.approx(select_baseline(seed_dataset).tco2e_total, abs=1e-5)
    assert baseline["electricity"]["quantity"] == 33430 and baseline["natural_gas"]["quantity"] == 6029
    assert baseline["label"] == "2025-11 to 2026-10" and baseline["provisional"] is True


# --- index, organization, factors, milestones --------------------------------


def test_index_declares_v1_licence_and_contact(seed_feed):
    index = _load(seed_feed, "index.json")
    assert index["version"] == "v1"
    assert index["generated_at"] == GENERATED_AT
    assert index["licence"]["name"] == "CC BY 4.0"
    assert index["licence"]["url"] == "https://creativecommons.org/licenses/by/4.0/"
    assert index["contact"] == CONTACT
    assert index["organization"]["zip"] == "62701"
    assert index["organization"]["slug"] == ORG_SLUG
    organization = _load(seed_feed, "organization.json")
    assert organization["version"] == "v1"
    assert organization["slug"] == ORG_SLUG
    assert organization["name"] == ORG_NAME
    assert (organization["city"], organization["state"], organization["zip"]) == ("Springfield", "IL", "62701")


def test_who_the_parish_is_comes_from_the_settings_and_nowhere_else(seed_dataset):
    other = settings(
        name="Grace Chapel", slug="grace-chapel-rivertown-oh", city="Rivertown", state="OH", zip="44000",
        contact_email="green@grace.example", website="https://grace.example/",
    )
    feed = build_feed(seed_dataset, GENERATED_AT, other)
    assert _validate_every_json_file(feed)
    index, organization = _load(feed, "index.json"), _load(feed, "organization.json")
    expected = {"slug": "grace-chapel-rivertown-oh", "name": "Grace Chapel", "city": "Rivertown", "state": "OH", "zip": "44000"}
    assert index["organization"] == expected
    assert {key: organization[key] for key in expected} == expected
    assert index["contact"] == "green@grace.example"
    assert organization["campus"] == "The campus of Grace Chapel in Rivertown, OH."
    assert organization["website"] == "https://grace.example/"
    assert _load(feed, "openapi.json")["info"]["title"] == "Grace Chapel carbon data feed"
    assert _load(feed, "openapi.json")["info"]["contact"]["email"] == "green@grace.example"
    for path, text in feed.items():
        if "/schema/" not in path and not path.endswith("CHANGELOG.md"):
            assert ORG_NAME not in text and "Springfield" not in text and CONTACT not in text, path
    # Nothing else moved: the figures are the data's.
    for path in (API + "monthly.json", API + "monthly.csv", API + "factors.json", API + "milestones.json", API + "travel.csv"):
        assert feed[path] == build_feed(seed_dataset, GENERATED_AT, SETTINGS)[path], path


def test_a_blank_slug_is_made_from_the_name_city_and_state(seed_dataset):
    assert SETTINGS.slug == ORG_SLUG == "st-example-church-springfield-il"
    assert settings(name="Église Sainte-Anne", city="Baton Rouge", state="LA", slug="").slug == "eglise-sainte-anne-baton-rouge-la"
    assert "website" not in _load(build_feed(seed_dataset, GENERATED_AT, SETTINGS), "organization.json")


def test_index_links_every_file(seed_feed):
    index = _load(seed_feed, "index.json")
    linked = set()
    for entry in index["files"].values():
        linked.add(API + entry["path"])
        if "schema" in entry:
            linked.add(API + entry["schema"])
    assert linked == EXPECTED_PATHS
    assert {"organization", "monthly", "monthly_csv", "annual", "travel", "travel_csv", "factors", "milestones"} <= set(
        index["files"]
    )
    assert "commute" not in json.dumps(index).lower()


def test_organization_lists_only_the_campus_meters_by_generic_id(synthetic_feed):
    organization = _load(synthetic_feed, "organization.json")
    assert organization["meters"] == [
        {"meter_id": "electric-main", "fuel": "electricity", "label": "Campus electricity"},
        {"meter_id": "gas-main", "fuel": "natural_gas", "label": "Campus natural gas"},
    ]
    assert organization["boundary"].startswith("The boundary is the campus: the utility meters listed under meters")
    assert "never added to the campus totals" in organization["boundary"]
    assert organization["campus"] == f"The campus of {ORG_NAME} in Springfield, IL."
    assert organization["egrid_subregion"] == "RFCW"


def test_factors_and_milestones_are_published_in_full(seed_dataset, seed_feed):
    factors = _load(seed_feed, "factors.json")["factors"]
    assert {f["factor_id"] for f in factors} == {f.factor_id for f in seed_dataset.factors}
    for published in factors:
        source = next(f for f in seed_dataset.factors if f.factor_id == published["factor_id"])
        for key in ("value", "unit", "source", "edition", "revision", "url", "retrieved"):
            assert published[key] == getattr(source, key)
    milestones = _load(seed_feed, "milestones.json")["milestones"]
    assert milestones == [
        {
            "date": "2019-01-01",
            "date_precision": "year",
            "kind": "boiler",
            "label_en": "Boiler replaced (2019)",
            "label_es": "Caldera reemplazada (2019)",
        }
    ]


def test_the_factors_feed_stays_english_and_unchanged_by_the_spanish_site_text(seed_feed):
    """The Spanish edition and revision text is for the Spanish Methodology page only; v1 of the feed is unchanged."""
    factors = _load(seed_feed, "factors.json")["factors"]
    assert factors
    expected_keys = {"factor_id", "value", "unit", "gases", "source", "edition", "revision", "url", "retrieved"}
    for published in factors:
        assert set(published) == expected_keys, published["factor_id"]
        assert "Revisión" not in published["revision"] and "enero" not in published["edition"] + published["revision"]
    egrid = next(f for f in factors if f["factor_id"] == "electricity_rfcw")
    assert egrid["edition"] == "eGRID2023 (2023 data, released 2025-01-15)"
    assert egrid["revision"] == "Revision 2, released 2025-06-12 (Revision 1 was 2025-01-17)"


def test_milestones_publish_date_precision_and_keep_date_as_it_was():
    ds = replace(
        _synthetic(),
        milestones=[
            Milestone(date(2021, 1, 1), "boiler", "Boiler installed (2021)", "Caldera instalada (2021)", "year"),
            Milestone(date(2027, 3, 1), "other", "Roof work", "Obra del techo", "month"),
            Milestone(date(2027, 5, 9), "solar_connected", "Solar connected", "Energía solar conectada"),
        ],
    )
    feed = build_feed(ds, GENERATED_AT, SETTINGS)
    published = _load(feed, "milestones.json")["milestones"]
    assert [(m["date"], m["date_precision"]) for m in published] == [
        ("2021-01-01", "year"),
        ("2027-03-01", "month"),
        ("2027-05-09", "day"),  # the default when a row gives no precision
    ]
    assert _validate_every_json_file(feed)  # the feed still validates against its schemas


def test_milestones_schema_describes_date_precision_as_optional_and_additive(synthetic_feed):
    schema = _schema(synthetic_feed, "milestones")
    item = schema["properties"]["milestones"]["items"]
    assert item["properties"]["date_precision"]["enum"] == ["year", "month", "day"]
    assert "date_precision" not in item["required"]  # optional: older documents without it stay valid
    assert item["additionalProperties"] is True and schema["additionalProperties"] is True
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    base = {"date": "2021-01-01", "kind": "boiler", "label_en": "a", "label_es": "b"}
    assert not list(validator.iter_errors({"version": "v1", "milestones": [base]}))
    assert not list(validator.iter_errors({"version": "v1", "milestones": [{**base, "date_precision": "month"}]}))
    assert list(validator.iter_errors({"version": "v1", "milestones": [{**base, "date_precision": "week"}]}))


def test_changelog_records_the_date_precision_addition(synthetic_feed):
    assert "date_precision" in synthetic_feed[API + "CHANGELOG.md"]


def test_openapi_describes_only_read_only_paths_for_every_data_file(seed_feed):
    spec = _load(seed_feed, "openapi.json")
    assert spec["openapi"].startswith("3.")
    assert spec["info"]["license"]["name"] == "CC BY 4.0"
    assert spec["info"]["contact"]["email"] == CONTACT
    assert spec["servers"] == [{"url": "/api/v1", "description": spec["servers"][0]["description"]}]
    for path, item in spec["paths"].items():
        assert set(item) == {"get"}, path
        assert "200" in item["get"]["responses"]
    expected = {"/index.json", "/organization.json", "/monthly.json", "/monthly.csv", "/annual.json", "/travel.json",
                "/travel.csv", "/factors.json", "/milestones.json", "/openapi.json", "/CHANGELOG.md"}
    assert set(spec["paths"]) == expected
    travel_ref = spec["paths"]["/travel.json"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert travel_ref == {"$ref": "schema/travel.schema.json"}


# --- things that must never appear -------------------------------------------


def test_feed_has_no_dollars_or_long_digit_runs(seed_dataset, seed_feed, synthetic_feed):
    forbidden = {
        "a dollar amount": re.compile(r"\$\s?\d"),
        "a run of 9 or more digits": re.compile(r"\b\d{9,}\b"),
        "the house": re.compile(r"\bhouse\b", re.IGNORECASE),
        "commute or commuting (the driving is church travel)": re.compile(r"\bcommut\w*", re.IGNORECASE),
    }
    # Whole-word matches: legitimate text such as "greenhouse gas" must not trip the house check.
    assert forbidden["the house"].search("greenhouse gas emissions, Greenhouse Gas Protocol") is None
    assert forbidden["the house"].search("the House meter") is not None
    assert forbidden["commute or commuting (the driving is church travel)"].search("Employee Commuting") is not None
    for feed, labels in (
        (seed_feed, [m.label for m in seed_dataset.meters]),
        (synthetic_feed, [m.label for m in METERS]),
    ):
        for path, text in feed.items():
            for what, pattern in forbidden.items():
                match = pattern.search(text)
                assert match is None, f"{path} contains {what}: {text[max(0, match.start() - 30) : match.end() + 30]!r}"
        # The meter labels are published, so they get the same scan.
        organization = feed[API + "organization.json"]
        for label in labels:
            assert label in organization
            for what, pattern in forbidden.items():
                assert pattern.search(label) is None, f"meter label {label!r} contains {what}"


def test_feed_never_emits_free_text_notes(synthetic_feed):
    for path, text in synthetic_feed.items():
        for sentinel in SENTINELS:
            assert sentinel not in text, f"{path} leaks {sentinel}"

    def keys(node):
        if isinstance(node, dict):
            for key, value in node.items():
                yield key
                yield from keys(value)
        elif isinstance(node, list):
            for item in node:
                yield from keys(item)

    for path, text in synthetic_feed.items():
        if path.endswith(".json") and not path.startswith(API + "schema/") and not path.endswith("openapi.json"):
            assert not {"note", "notes"} & set(keys(json.loads(text))), path
        if path.endswith(".csv"):
            assert not {"note", "notes"} & set(text.splitlines()[0].split(",")), path


def test_feed_makes_no_offset_or_net_zero_claims(seed_feed, synthetic_feed):
    for feed in (seed_feed, synthetic_feed):
        for path, text in feed.items():
            assert len(re.findall("offset", text, re.IGNORECASE)) == len(re.findall("not an offset", text)), path
            assert not re.search(r"net[- ]zero|carbon[- ]neutral", text, re.IGNORECASE), path
    # The one place the word belongs: the avoided figure is labelled as not an offset.
    assert AVOIDED_NOTE in seed_feed[API + "travel.json"]


# --- the files published verbatim --------------------------------------------


def test_changelog_and_schemas_are_the_repo_files_verbatim(seed_feed):
    assert seed_feed[API + "CHANGELOG.md"] == (ROOT / "static" / "api-changelog.md").read_text(encoding="utf-8")
    for name in ("index", "organization", "monthly", "annual", "travel", "factors", "milestones"):
        assert seed_feed[f"{API}schema/{name}.schema.json"] == (ROOT / "schema" / f"{name}.schema.json").read_text(
            encoding="utf-8"
        )
    assert "v2" in seed_feed[API + "CHANGELOG.md"]  # the additive-only rule names what a breaking change does


# --- what parish.toml switches: travel, the grid subregion, languages, the sample flag, the base path ----------

TRAVEL_PATHS = {API + "travel.json", API + "travel.csv", API + "schema/travel.schema.json"}


@pytest.fixture(scope="module")
def no_travel_feed():
    return build_feed(_synthetic(), GENERATED_AT, settings(travel_enabled=False))


def test_with_travel_switched_off_the_feed_has_no_travel_files_block_or_links(no_travel_feed, synthetic_feed):
    assert set(no_travel_feed) == EXPECTED_PATHS - TRAVEL_PATHS
    assert _validate_every_json_file(no_travel_feed)

    index = _load(no_travel_feed, "index.json")
    assert "travel" not in index["files"] and "travel_csv" not in index["files"]
    annual = _load(no_travel_feed, "annual.json")
    assert "staff_travel" not in annual
    assert "/travel.json" not in _load(no_travel_feed, "openapi.json")["paths"]
    assert "/travel.csv" not in _load(no_travel_feed, "openapi.json")["paths"]
    organization = _load(no_travel_feed, "organization.json")
    assert "travel" not in organization["boundary"].lower()
    assert "gasoline_car_per_mile" not in {f["factor_id"] for f in _load(no_travel_feed, "factors.json")["factors"]}

    # The dataset still holds vehicles and miles (it was built by hand); none of it is published.
    for path, text in no_travel_feed.items():
        if "/schema/" in path or path.endswith("CHANGELOG.md"):
            continue
        assert "ev-home" not in text and "ev-church" not in text and "avoided" not in text.lower(), path

    # The campus figures are exactly those of the feed with travel.
    assert annual["campus"] == _load(synthetic_feed, "annual.json")["campus"]
    for name in ("monthly.json", "monthly.csv", "milestones.json"):
        assert no_travel_feed[API + name] == synthetic_feed[API + name]


def test_the_schemas_allow_a_feed_without_travel_but_still_describe_it():
    index_schema = json.loads((ROOT / "schema" / "index.schema.json").read_text(encoding="utf-8"))
    annual_schema = json.loads((ROOT / "schema" / "annual.schema.json").read_text(encoding="utf-8"))
    required_files = index_schema["properties"]["files"]["required"]
    assert "travel" not in required_files and "travel_csv" not in required_files
    assert {"travel", "travel_csv"} <= set(index_schema["properties"]["files"]["properties"])
    assert "staff_travel" not in annual_schema["required"] and "staff_travel" in annual_schema["properties"]
    assert {"index", "organization", "monthly", "monthly_csv", "annual", "factors", "milestones"} <= set(required_files)


FAKE_GRID = Factor("electricity_zzzt", 0.9, "kg CO2e/kWh", "test", "test only: a made-up subregion", "test", "test", "https://example.org/", "2026-01-01")
OTHER_GRID = Factor("electricity_aaat", 0.1, "kg CO2e/kWh", "test", "test only: a made-up subregion", "test", "test", "https://example.org/", "2026-01-01")


def test_a_different_subregion_prices_the_electricity_and_is_the_only_grid_factor_published():
    many = replace(_synthetic(vehicles=False), factors=[*FACTORS, FAKE_GRID, OTHER_GRID])
    here = settings(egrid_subregion="ZZZT")
    feed = build_feed(replace(many, grid_subregion="ZZZT"), GENERATED_AT, here)
    assert _validate_every_json_file(feed)

    published = {f["factor_id"]: f["value"] for f in _load(feed, "factors.json")["factors"]}
    assert set(published) == {"electricity_zzzt", "natural_gas", "gasoline_car_per_mile"}
    assert published["electricity_zzzt"] == 0.9

    rows = _load(feed, "monthly.json")["rows"]
    electric = [r for r in rows if r["fuel"] == "electricity"]
    assert electric and {r["factor_id"] for r in electric} == {"electricity_zzzt"}
    for row in electric:
        assert row["tco2e"] == pytest.approx(row["quantity"] * 0.9 / 1000, abs=1e-6)
    assert {r["factor_id"] for r in rows if r["fuel"] == "natural_gas"} == {"natural_gas"}
    assert _load(feed, "organization.json")["egrid_subregion"] == "ZZZT"

    # The same data in the sample's own subregion: only the electricity moves.
    home = build_feed(replace(many, grid_subregion="RFCW"), GENERATED_AT, SETTINGS)
    assert {f["factor_id"] for f in _load(home, "factors.json")["factors"]} == {"electricity_rfcw", "natural_gas", "gasoline_car_per_mile"}
    gas = lambda feed: [r for r in _load(feed, "monthly.json")["rows"] if r["fuel"] == "natural_gas"]
    assert gas(feed) == gas(home)


def test_the_grid_a_vehicle_charges_on_elsewhere_is_published_too_and_no_other():
    away = Vehicle("ev-away", "x", 0.3, "home", "AAAT")
    ds = replace(
        _synthetic(vehicles=False),
        factors=[*FACTORS, FAKE_GRID, OTHER_GRID],
        vehicles=[away],
        commute=[CommuteMonth("ev-away", "2026-01", 100.0, "odometer")],
    )
    feed = build_feed(ds, GENERATED_AT, SETTINGS)
    assert {f["factor_id"] for f in _load(feed, "factors.json")["factors"]} == {
        "electricity_rfcw", "electricity_aaat", "natural_gas", "gasoline_car_per_mile",
    }
    (row,) = _load(feed, "travel.json")["rows"]
    assert row["tco2e_counted_here"] == pytest.approx(100 * 0.3 * 0.1 / 1000, abs=1e-6)


@pytest.mark.parametrize("flag", [True, False])
def test_the_sample_data_flag_is_carried_by_the_index_the_organization_and_the_annual_file(flag):
    feed = build_feed(_synthetic(), GENERATED_AT, settings(sample_data=flag))
    assert _validate_every_json_file(feed)
    for name in ("index.json", "organization.json", "annual.json"):
        assert _load(feed, name)["sample_data"] is flag, name


def test_the_sample_flag_changes_nothing_else_in_the_feed():
    on = build_feed(_synthetic(), GENERATED_AT, settings(sample_data=True))
    off = build_feed(_synthetic(), GENERATED_AT, settings(sample_data=False))
    assert {path for path in on if on[path] != off[path]} == {API + "index.json", API + "organization.json", API + "annual.json"}


def test_the_index_says_which_languages_the_site_is_built_in():
    assert _load(build_feed(_synthetic(), GENERATED_AT, SETTINGS), "index.json")["languages"] == ["en", "es"]
    english = build_feed(_synthetic(), GENERATED_AT, settings(languages=("en",)))
    assert _load(english, "index.json")["languages"] == ["en"]
    assert _validate_every_json_file(english)


def test_an_english_only_feed_keeps_the_spanish_label_field_and_leaves_it_empty():
    ds = replace(_synthetic(), milestones=[Milestone(date(2019, 1, 1), "boiler", "Boiler replaced (2019)", "", "year")])
    feed = build_feed(ds, GENERATED_AT, settings(languages=("en",)))
    assert _validate_every_json_file(feed)
    assert _load(feed, "milestones.json")["milestones"][0]["label_es"] == ""


@pytest.mark.parametrize(
    "base_url, server",
    [("", "/api/v1"), ("https://carbon.example.org", "/api/v1"), ("https://someone.github.io/parish-carbon", "/parish-carbon/api/v1")],
)
def test_the_openapi_server_address_includes_the_sites_base_path(base_url, server):
    feed = build_feed(_synthetic(), GENERATED_AT, settings(base_url=base_url))
    assert _load(feed, "openapi.json")["servers"][0]["url"] == server
    # Every other address in the feed is relative to the index, so it works under any path.
    index = _load(feed, "index.json")
    for entry in index["files"].values():
        assert not entry["path"].startswith(("/", "http")) and not entry.get("schema", "").startswith(("/", "http"))


def test_the_baseline_basis_names_the_parishs_own_baseline_year():
    ds = replace(_synthetic(month_count=28), baseline_year=2026)
    baseline = _load(build_feed(ds, GENERATED_AT, settings(baseline_year=2026)), "annual.json")["campus"]["baseline"]
    assert (baseline["label"], baseline["basis"]) == ("2026", "calendar_2026")
    default = _load(build_feed(_synthetic(month_count=28), GENERATED_AT, SETTINGS), "annual.json")["campus"]["baseline"]
    assert default["basis"] == "latest_twelve_billing_months"  # the data starts in September 2025, so 2025 is not complete
