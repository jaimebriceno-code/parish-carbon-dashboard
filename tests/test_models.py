import csv
from datetime import date, timedelta
from pathlib import Path

import pytest

from carbon.models import MILESTONE_PRECISIONS, Dataset, Factor, Milestone, load_dataset
from tests.support import SAMPLE_DIR, SAMPLE_MILES_PER_DAY, SAMPLE_VEHICLE, sample_data_copy, settings


def _quantities(ds, meter_id, first="2025-11", last="2026-10"):
    return {
        r.period_end_month: r.quantity
        for r in ds.readings
        if r.meter_id == meter_id and first <= r.period_end_month <= last
    }


def test_load_dataset_reads_the_sample_files():
    ds = load_dataset(SAMPLE_DIR)
    assert isinstance(ds, Dataset)

    electric = _quantities(ds, "electric-main")
    gas = _quantities(ds, "gas-main")
    assert len(electric) == 12
    assert len(gas) == 12
    assert sum(electric.values()) == 33430
    assert sum(gas.values()) == 6029

    gas_oct = next(r for r in ds.readings if r.meter_id == "gas-main" and r.period_end_month == "2026-10")
    assert gas_oct.quantity == 176
    assert gas_oct.unit == "therm"
    assert gas_oct.data_quality == "bill"


def test_sample_has_fourteen_billing_months_for_each_meter():
    ds = load_dataset(SAMPLE_DIR)
    assert [m.meter_id for m in ds.meters] == ["electric-main", "gas-main"]
    for meter_id in ("electric-main", "gas-main"):
        months = sorted(r.period_end_month for r in ds.readings if r.meter_id == meter_id)
        assert len(months) == 14 and months[0] == "2025-09" and months[-1] == "2026-10", meter_id


def test_sample_bill_rows_carry_exact_period_dates_and_the_estimates_do_not():
    ds = load_dataset(SAMPLE_DIR)
    by_key = {(r.meter_id, r.period_end_month): r for r in ds.readings}

    electric = by_key[("electric-main", "2026-10")]
    assert electric.period_start == date(2026, 9, 23)
    assert electric.period_end == date(2026, 10, 22)
    assert electric.peak_kw == 9.8
    assert electric.quantity == 2390
    assert electric.data_quality == "bill"

    gas = by_key[("gas-main", "2026-10")]
    assert gas.period_start == date(2026, 9, 9)
    assert gas.period_end == date(2026, 10, 8)

    for r in ds.readings:
        if r.data_quality == "bill":
            assert r.period_start is not None and r.period_end is not None, (r.meter_id, r.period_end_month)
            assert 25 <= (r.period_end - r.period_start).days <= 35
        else:
            assert r.period_start is None and r.period_end is None


def test_sample_has_one_estimate_of_each_kind_so_the_estimated_labels_show():
    ds = load_dataset(SAMPLE_DIR)
    estimates = {(r.meter_id, r.period_end_month): r for r in ds.readings if r.data_quality != "bill"}
    assert set(estimates) == {("electric-main", "2025-11"), ("gas-main", "2025-12")}
    printed = estimates[("electric-main", "2025-11")]
    assert printed.data_quality == "chart_estimate" and printed.uncertainty_pct is None
    measured = estimates[("gas-main", "2025-12")]
    assert measured.data_quality == "chart_measured" and measured.uncertainty_pct == 5


def test_sample_has_a_seasonal_shape():
    # Plausible, not real: gas peaks in winter and all but vanishes in summer; electricity peaks in summer.
    ds = load_dataset(SAMPLE_DIR)
    gas = _quantities(ds, "gas-main")
    electric = _quantities(ds, "electric-main")
    assert max(gas, key=gas.get) == "2026-01" and gas["2026-07"] < gas["2026-01"] / 20
    assert max(electric, key=electric.get) == "2026-07"


def test_sample_milestone_is_a_boiler_known_only_by_year():
    ds = load_dataset(SAMPLE_DIR)
    assert len(ds.milestones) == 1
    m = ds.milestones[0]
    assert m.kind == "boiler"
    assert m.date == date(2019, 1, 1)
    assert m.label_en and m.label_es
    # Only the year is known: the 1 January date is a placeholder, so the row says so.
    assert m.precision == "year"


def _weekdays_in(month: str) -> int:
    """Monday to Friday calendar days in a YYYY-MM month, counted one day at a time (no holiday adjustment)."""
    year, mon = int(month[:4]), int(month[5:7])
    day, count = date(year, mon, 1), 0
    while day.month == mon:
        count += day.weekday() < 5
        day += timedelta(days=1)
    return count


def test_sample_travel_is_25_miles_per_weekday():
    # The sample travel line for the twelve billing months of the baseline, as an estimate: 25 miles for every
    # Monday-to-Friday day of the calendar month.
    ds = load_dataset(SAMPLE_DIR)
    rows = [c for c in ds.commute if c.vehicle_id == SAMPLE_VEHICLE]
    assert [c.period_end_month for c in rows] == [
        "2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04",
        "2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10",
    ]
    for row in rows:
        assert row.data_quality == "estimate", row.period_end_month
        assert row.miles == SAMPLE_MILES_PER_DAY * _weekdays_in(row.period_end_month), row.period_end_month
    assert sum(_weekdays_in(c.period_end_month) for c in rows) == 260
    assert sum(c.miles for c in rows) == 6500
    assert len(ds.commute) == 12, "no other vehicle or month has been entered"


@pytest.fixture
def data_copy(tmp_path):
    return sample_data_copy(tmp_path)


# --- what the settings change about loading ---------------------------------------------------------------


def test_with_settings_the_dataset_carries_the_grid_subregion_and_the_baseline_year():
    ds = load_dataset(SAMPLE_DIR, settings(egrid_subregion="ZZZT", baseline_year=2027))
    assert (ds.grid_subregion, ds.baseline_year) == ("ZZZT", 2027)
    plain = load_dataset(SAMPLE_DIR)
    assert (plain.grid_subregion, plain.baseline_year) == ("", 2025)


def test_factors_file_with_many_electricity_rows_loads_them_all(data_copy):
    with (data_copy / "factors.csv").open("a", encoding="utf-8", newline="") as handle:
        handle.write("electricity_zzzt,0.9,kg CO2e/kWh,test,test only,test,test,https://example.org/,2026-01-01,prueba,prueba\n")
        handle.write("electricity_aaat,0.1,kg CO2e/kWh,test,test only,test,test,https://example.org/,2026-01-01,prueba,prueba\n")
    ds = load_dataset(data_copy, settings(egrid_subregion="ZZZT"))
    assert [f.factor_id for f in ds.factors] == [
        "electricity_rfcw", "natural_gas", "gasoline_car_per_mile", "electricity_zzzt", "electricity_aaat",
    ]


def test_with_travel_switched_off_the_travel_files_are_not_read_and_may_be_missing(data_copy):
    off = settings(travel_enabled=False)
    assert load_dataset(data_copy, off).vehicles == [] and load_dataset(data_copy, off).commute == []

    (data_copy / "vehicles.csv").unlink()
    (data_copy / "commute.csv").unlink()
    ds = load_dataset(data_copy, off)
    assert (ds.vehicles, ds.commute) == ([], []) and len(ds.readings) == 28

    with pytest.raises(OSError):
        load_dataset(data_copy, settings(travel_enabled=True))


def test_with_travel_switched_off_header_only_travel_files_are_fine_too(data_copy):
    for name in ("vehicles.csv", "commute.csv"):
        path = data_copy / name
        path.write_text(path.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")
    ds = load_dataset(data_copy, settings(travel_enabled=False))
    assert (ds.vehicles, ds.commute) == ([], [])


def test_an_english_only_site_needs_no_spanish_milestone_labels(data_copy):
    english = settings(languages=("en",))
    path = data_copy / "milestones.csv"

    path.write_text("date,kind,label_en,precision\n2019-01-01,boiler,Boiler replaced (2019),year\n", encoding="utf-8")
    assert load_dataset(data_copy, english).milestones == [
        Milestone(date(2019, 1, 1), "boiler", "Boiler replaced (2019)", "", "year")
    ]
    with pytest.raises(ValueError, match="label_es"):
        load_dataset(data_copy, settings(languages=("en", "es")))

    path.write_text("date,kind,label_en,label_es,precision\n2019-01-01,boiler,Boiler replaced (2019),,year\n", encoding="utf-8")
    assert load_dataset(data_copy, english).milestones[0].label_es == ""
    with pytest.raises(ValueError, match="label_es"):
        load_dataset(data_copy)


def test_header_only_factors_file_loads_as_empty(data_copy):
    path = data_copy / "factors.csv"
    path.write_text(path.read_text().splitlines()[0] + "\n")
    assert load_dataset(data_copy).factors == []


def test_factors_load_their_optional_spanish_edition_and_revision(data_copy):
    ds = load_dataset(data_copy)
    assert len(ds.factors) == 3
    for factor in ds.factors:
        assert factor.edition_es and factor.revision_es, factor.factor_id
        assert factor.edition_es != factor.edition and factor.revision_es != factor.revision, factor.factor_id


def test_factors_file_without_the_spanish_columns_still_loads_with_empty_defaults(data_copy):
    path = data_copy / "factors.csv"
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    keep = [name for name in rows[0] if name not in ("edition_es", "revision_es")]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keep, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    factors = load_dataset(data_copy).factors
    assert len(factors) == 3
    assert all(f.edition_es == "" and f.revision_es == "" for f in factors)
    assert all(f.edition and f.revision for f in factors)  # the English text is untouched


def test_factor_spanish_fields_default_to_empty_when_built_in_code():
    factor = Factor("f", 1.0, "kg CO2e/kWh", "g", "s", "e", "r", "https://example.org", "2026-11-17")
    assert factor.edition_es == "" and factor.revision_es == ""


def test_an_unknown_spanish_style_column_is_still_rejected(data_copy):
    _add_column(data_copy / "factors.csv", "edition_fr")
    with pytest.raises(ValueError) as caught:
        load_dataset(data_copy)
    assert "factors.csv" in str(caught.value) and "edition_fr" in str(caught.value) and "unexpected" in str(caught.value)


def test_header_only_vehicles_and_commute_files_load_as_empty(data_copy):
    # The sample holds a vehicle and its miles, so the header-only case is made from a copy.
    for name in ("vehicles.csv", "commute.csv"):
        path = data_copy / name
        path.write_text(path.read_text().splitlines()[0] + "\n")
    ds = load_dataset(data_copy)
    assert ds.vehicles == []
    assert ds.commute == []
    assert len(ds.readings) > 0 and len(ds.factors) == 3  # the other files are untouched


def test_sample_vehicle_is_marked_as_a_made_up_figure_and_points_to_the_epa_label():
    ds = load_dataset(SAMPLE_DIR)
    assert len(ds.vehicles) == 1
    vehicle = ds.vehicles[0]
    assert vehicle.vehicle_id == SAMPLE_VEHICLE
    assert vehicle.kwh_per_mile == 0.3
    assert vehicle.charging_place == "home"
    assert vehicle.charging_grid_region == "RFCW"
    # The efficiency is a placeholder, and says so, with where the real one comes from.
    assert "made up" in vehicle.kwh_per_mile_source and "fueleconomy.gov" in vehicle.kwh_per_mile_source


def _vehicles_file(data_copy, header, *rows):
    path = data_copy / "vehicles.csv"
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")


def test_vehicle_optional_source_and_note_load_with_and_without_values(data_copy):
    full = "vehicle_id,label,kwh_per_mile,charging_place,charging_grid_region,kwh_per_mile_source,note"
    # Both optional columns filled in (the second value is quoted because it holds a comma).
    _vehicles_file(data_copy, full, 'ev-a,A,0.3,home,RFCW,EPA label 30 kWh per 100 miles,"Charged elsewhere, counted here"')
    (a,) = load_dataset(data_copy).vehicles
    assert a.kwh_per_mile_source == "EPA label 30 kWh per 100 miles"
    assert a.note == "Charged elsewhere, counted here"

    # Both columns present but empty on one row, and the columns missing altogether in a file with the old header.
    _vehicles_file(data_copy, full, "ev-b,B,0.3,home,RFCW,,")
    (b,) = load_dataset(data_copy).vehicles
    assert (b.kwh_per_mile_source, b.note) == ("", "")
    _vehicles_file(data_copy, "vehicle_id,label,kwh_per_mile,charging_place,charging_grid_region", "ev-c,C,0.3,church,RFCW")
    (c,) = load_dataset(data_copy).vehicles
    assert (c.kwh_per_mile_source, c.note) == ("", "")
    assert (c.vehicle_id, c.kwh_per_mile, c.charging_place) == ("ev-c", 0.3, "church")

    # Only one of the two.
    _vehicles_file(data_copy, "vehicle_id,label,kwh_per_mile,charging_place,charging_grid_region,note", "ev-d,D,0.3,home,RFCW,just a note")
    (d,) = load_dataset(data_copy).vehicles
    assert (d.kwh_per_mile_source, d.note) == ("", "just a note")


def test_vehicle_columns_are_still_checked_after_the_optional_two_were_added(data_copy):
    # The optional columns are exactly these two: a near miss is rejected, not read as a new field.
    for typo in ("kwh_per_mile_sorce", "notes", "source"):
        _vehicles_file(
            data_copy,
            f"vehicle_id,label,kwh_per_mile,charging_place,charging_grid_region,{typo}",
            "ev-a,A,0.3,home,RFCW,x",
        )
        with pytest.raises(ValueError) as caught:
            load_dataset(data_copy)
        message = str(caught.value)
        assert "vehicles.csv" in message and repr(typo) in message and "unexpected" in message
    # A required column is still required.
    _vehicles_file(data_copy, "vehicle_id,label,charging_place,charging_grid_region,note", "ev-a,A,home,RFCW,x")
    with pytest.raises(ValueError, match="kwh_per_mile"):
        load_dataset(data_copy)


def _add_column(path: Path, name: str, value: str = "x"):
    with path.open(newline="") as f:
        rows = list(csv.reader(f))
    rows[0].append(name)
    for row in rows[1:]:
        row.append(value)
    with path.open("w", newline="") as f:
        csv.writer(f).writerows(rows)


@pytest.mark.parametrize("column", ["account_number", "amount_usd", "cost"])
def test_forbidden_columns_rejected(data_copy, column):
    _add_column(data_copy / "readings.csv", column)
    with pytest.raises(ValueError, match=column):
        load_dataset(data_copy)


def test_forbidden_columns_rejected_in_other_files(data_copy):
    _add_column(data_copy / "meters.csv", "account_number")
    with pytest.raises(ValueError, match="account_number"):
        load_dataset(data_copy)


def test_missing_required_column_rejected(data_copy):
    path = data_copy / "readings.csv"
    text = path.read_text().replace("quantity", "qty", 1)
    path.write_text(text)
    with pytest.raises(ValueError, match="quantity"):
        load_dataset(data_copy)


def test_malformed_number_rejected(data_copy):
    path = data_copy / "readings.csv"
    with path.open(newline="") as f:
        rows = list(csv.reader(f))
    rows[1][rows[0].index("quantity")] = "lots"
    with path.open("w", newline="") as f:
        csv.writer(f).writerows(rows)
    with pytest.raises(ValueError, match="quantity"):
        load_dataset(data_copy)


# --- Task 1b: strict columns, forbidden-name variants, loader error paths ---

@pytest.mark.parametrize(
    "file, column",
    [
        ("readings.csv", "period_strt"),
        ("readings.csv", "bill_total"),
        ("meters.csv", "meter_slug"),
        ("milestones.csv", "kindd"),
        ("milestones.csv", "precison"),
        ("factors.csv", "valeu"),
        ("vehicles.csv", "type"),
        ("commute.csv", "mils"),
    ],
)
def test_unexpected_column_raises_with_file_and_column(data_copy, file, column):
    _add_column(data_copy / file, column)
    with pytest.raises(ValueError) as caught:
        load_dataset(data_copy)
    message = str(caught.value)
    assert file in message and column in message
    assert "unexpected" in message and "forbidden" not in message


@pytest.mark.parametrize(
    "file, column",
    [
        ("meters.csv", "Account_Number"),
        ("meters.csv", "meter_number"),
        ("readings.csv", " cost "),
        ("readings.csv", "Amount_USD"),
        ("milestones.csv", "AMOUNT_USD"),
        ("factors.csv", " Price "),
        ("vehicles.csv", "Meter_Number"),
        ("commute.csv", "COST"),
        ("commute.csv", "  account_number"),
    ],
)
def test_forbidden_column_variants_raise(data_copy, file, column):
    _add_column(data_copy / file, column)
    with pytest.raises(ValueError) as caught:
        load_dataset(data_copy)
    message = str(caught.value)
    assert file in message and column.strip().lower() in message.lower()
    assert "forbidden" in message and "unexpected" not in message


def test_loader_keeps_duplicate_meter_rows_for_the_validator(data_copy):
    path = data_copy / "meters.csv"
    with path.open(newline="") as f:
        rows = list(csv.reader(f))
    rows.append(rows[1])
    with path.open("w", newline="") as f:
        csv.writer(f).writerows(rows)
    ds = load_dataset(data_copy)
    assert [m.meter_id for m in ds.meters] == ["electric-main", "gas-main", "electric-main"]


def test_sample_text_says_it_is_a_sample_and_names_no_utility():
    ds = load_dataset(SAMPLE_DIR)
    assert all("sample" in m.notes.lower() for m in ds.meters)
    notes = [r.note for r in ds.readings if r.note] + [c.note for c in ds.commute]
    assert notes and all("sample" in note.lower() for note in notes)


def _set_cell(path: Path, row_index: int, column: str, value: str):
    """Set one cell; row_index 1 is the first data row, which is line 2 of the file."""
    with path.open(newline="") as f:
        rows = list(csv.reader(f))
    rows[row_index][rows[0].index(column)] = value
    with path.open("w", newline="") as f:
        csv.writer(f, lineterminator="\n").writerows(rows)


def test_row_with_more_values_than_columns_names_file_and_line(data_copy):
    path = data_copy / "readings.csv"
    with path.open(newline="") as f:
        rows = list(csv.reader(f))
    rows[2].append("stray")  # file line 3
    with path.open("w", newline="") as f:
        csv.writer(f, lineterminator="\n").writerows(rows)
    with pytest.raises(ValueError, match=r"readings\.csv line 3\b.*more values than columns"):
        load_dataset(data_copy)


def test_blank_required_value_names_file_line_and_column(data_copy):
    _set_cell(data_copy / "readings.csv", 2, "unit", "")  # file line 3
    with pytest.raises(ValueError, match=r"readings\.csv line 3\b.*'unit'.*required"):
        load_dataset(data_copy)


def test_malformed_date_names_file_and_line(data_copy):
    _set_cell(data_copy / "milestones.csv", 1, "date", "01/01/2021")  # file line 2
    with pytest.raises(ValueError, match=r"milestones\.csv line 2\b.*'date'.*YYYY-MM-DD"):
        load_dataset(data_copy)
    # An optional date column is parsed too.
    _set_cell(data_copy / "readings.csv", 4, "period_start", "2026-13-45")  # file line 5
    _set_cell(data_copy / "milestones.csv", 1, "date", "2021-01-01")
    with pytest.raises(ValueError, match=r"readings\.csv line 5\b.*'period_start'.*YYYY-MM-DD"):
        load_dataset(data_copy)


@pytest.mark.parametrize("bad", ["Oct 2026", "2026-13", "2026-1", "202610"])
def test_malformed_month_names_file_and_line(data_copy, bad):
    _set_cell(data_copy / "readings.csv", 3, "period_end_month", bad)  # file line 4
    with pytest.raises(ValueError, match=r"readings\.csv line 4\b.*'period_end_month'.*YYYY-MM"):
        load_dataset(data_copy)


# --- milestone date precision ----------------------------------------------------------


def test_milestone_precision_values_and_default():
    assert MILESTONE_PRECISIONS == ("year", "month", "day")
    assert Milestone(date(2021, 1, 1), "boiler", "Boiler installed (2021)", "Caldera instalada (2021)").precision == "day"


def _write_milestones(data_copy, header, *rows):
    (data_copy / "milestones.csv").write_text("\n".join([header, *rows]) + "\n")


@pytest.mark.parametrize("precision", ["year", "month", "day"])
def test_loader_reads_the_optional_precision_column(data_copy, precision):
    _write_milestones(data_copy, "date,kind,label_en,label_es,precision", f"2021-01-01,boiler,Boiler,Caldera,{precision}")
    assert load_dataset(data_copy).milestones[0].precision == precision


def test_loader_defaults_precision_to_day_when_blank_or_absent(data_copy):
    _write_milestones(data_copy, "date,kind,label_en,label_es,precision", "2021-01-01,boiler,Boiler,Caldera,")
    assert load_dataset(data_copy).milestones[0].precision == "day"
    _write_milestones(data_copy, "date,kind,label_en,label_es", "2021-01-01,boiler,Boiler,Caldera")
    assert load_dataset(data_copy).milestones[0].precision == "day"


def test_loader_passes_an_unknown_precision_through_for_the_validator_to_report(data_copy):
    _write_milestones(data_copy, "date,kind,label_en,label_es,precision", "2021-01-01,boiler,Boiler,Caldera,week")
    assert load_dataset(data_copy).milestones[0].precision == "week"
