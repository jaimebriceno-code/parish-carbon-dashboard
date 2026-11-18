from dataclasses import replace
from datetime import date

import pytest

from carbon.models import CommuteMonth, Dataset, Factor, Meter, Milestone, Reading, Vehicle, load_dataset
from carbon.validate import Issue, validate_dataset
from tests.support import SAMPLE_DIR, SETTINGS, sample_dataset, settings

DATA_DIR = SAMPLE_DIR  # the frozen sample parish, never the repository's own data folder

METERS = [
    Meter("electric-main", "electricity", "Campus electricity", ""),
    Meter("gas-main", "natural_gas", "Campus natural gas", ""),
]


def reading(meter_id="electric-main", month="2026-01", quantity=1000.0, unit=None, quality="bill", **kw):
    if unit is None:
        unit = "kWh" if meter_id.startswith("electric") else "therm"
    return Reading(
        meter_id=meter_id,
        period_end_month=month,
        quantity=quantity,
        unit=unit,
        data_quality=quality,
        **kw,
    )


def factor(**kw):
    """A factor with its English and Spanish edition and revision text, which validates clean."""
    fields = dict(
        factor_id="natural_gas",
        value=5.3,
        unit="kg CO2e/therm",
        gases="CO2, CH4, N2O",
        source="test source",
        edition="Test Hub (January 2025)",
        revision="Last Modified 15 January 2025",
        url="https://example.org/hub.pdf",
        retrieved="2026-11-17",
        edition_es="Hub de prueba (enero de 2025)",
        revision_es="Última modificación el 15 de enero de 2025",
    )
    fields.update(kw)
    return Factor(**fields)


def dataset(*readings, meters=METERS, **others):
    return Dataset(meters=list(meters), readings=list(readings), **others)


def codes(issues, level=None):
    return [i.code for i in issues if level is None or i.level == level]


def test_issue_is_frozen_with_level_code_message():
    issue = Issue("error", "X", "message")
    assert (issue.level, issue.code, issue.message) == ("error", "X", "message")
    with pytest.raises(AttributeError):
        issue.level = "warning"


def test_sample_data_has_no_errors():
    issues = validate_dataset(load_dataset(DATA_DIR))
    assert [i for i in issues if i.level == "error"] == []


def test_sample_has_only_two_meters():
    ds = load_dataset(DATA_DIR)
    assert {m.meter_id: m.fuel for m in ds.meters} == {
        "electric-main": "electricity",
        "gas-main": "natural_gas",
    }


def test_empty_optional_files_need_no_rows():
    assert validate_dataset(dataset(reading())) == []


def test_duplicate_reading_is_error():
    issues = validate_dataset(dataset(reading(month="2026-01"), reading(month="2026-01", quantity=1100.0)))
    dups = [i for i in issues if i.code == "DUPLICATE_READING"]
    assert len(dups) == 1 and dups[0].level == "error"
    assert "electric-main" in dups[0].message and "2026-01" in dups[0].message


def test_same_month_on_different_meters_is_not_duplicate():
    issues = validate_dataset(dataset(reading("electric-main", "2026-01"), reading("gas-main", "2026-01")))
    assert "DUPLICATE_READING" not in codes(issues)


def test_unit_mismatch_is_error():
    issues = validate_dataset(dataset(reading("gas-main", "2026-01", unit="kWh")))
    mismatch = [i for i in issues if i.code == "UNIT_MISMATCH"]
    assert len(mismatch) == 1 and mismatch[0].level == "error"


def test_correct_units_pass():
    issues = validate_dataset(dataset(reading("electric-main", "2026-01"), reading("gas-main", "2026-01")))
    assert "UNIT_MISMATCH" not in codes(issues)


def test_unknown_meter_is_error():
    issues = validate_dataset(dataset(reading("steam-main", "2026-01", unit="kWh")))
    unknown = [i for i in issues if i.code == "UNKNOWN_METER"]
    assert len(unknown) == 1 and unknown[0].level == "error"
    assert "steam-main" in unknown[0].message


def test_invalid_enum_values_are_errors():
    bad_quality = reading(quality="guess")
    bad_fuel = Dataset(meters=[Meter("electric-main", "coal", "x", "")], readings=[reading()])
    assert "INVALID_VALUE" in codes(validate_dataset(dataset(bad_quality)), "error")
    assert "INVALID_VALUE" in codes(validate_dataset(bad_fuel), "error")


@pytest.mark.parametrize("days", [25, 32, 35])
def test_period_length_in_range_is_fine(days):
    start = date(2026, 1, 1)
    end = date.fromordinal(start.toordinal() + days)
    r = reading(month=f"{end.year}-{end.month:02d}", period_start=start, period_end=end)
    assert "PERIOD_LENGTH" not in codes(validate_dataset(dataset(r)))


@pytest.mark.parametrize("days", [9, 24, 36, 60])
def test_period_length_outside_25_to_35_days_is_error(days):
    start = date(2026, 1, 1)
    end = date.fromordinal(start.toordinal() + days)
    r = reading(month=f"{end.year}-{end.month:02d}", period_start=start, period_end=end)
    issues = validate_dataset(dataset(r))
    found = [i for i in issues if i.code == "PERIOD_LENGTH"]
    assert len(found) == 1 and found[0].level == "error"


def test_period_length_only_checked_when_both_dates_given():
    only_end = reading(period_end=date(2026, 1, 27))
    only_start = reading(period_start=date(2026, 1, 1))
    neither = reading()
    for r in (only_end, only_start, neither):
        assert "PERIOD_LENGTH" not in codes(validate_dataset(dataset(r)))


def test_one_period_date_without_the_other_is_error():
    for r in (reading(period_end=date(2026, 1, 27)), reading(period_start=date(2026, 1, 1))):
        found = [i for i in validate_dataset(dataset(r)) if i.code == "PERIOD_PARTIAL"]
        assert len(found) == 1 and found[0].level == "error"


def test_gap_in_months_is_warning():
    issues = validate_dataset(dataset(reading(month="2026-01"), reading(month="2026-04")))
    gaps = [i for i in issues if i.code == "GAP"]
    assert len(gaps) == 1 and gaps[0].level == "warning"
    assert "2026-02" in gaps[0].message and "2026-03" in gaps[0].message


def test_gap_across_year_boundary_and_contiguous_months():
    contiguous = dataset(reading(month="2025-11"), reading(month="2025-12"), reading(month="2026-01"))
    assert "GAP" not in codes(validate_dataset(contiguous))
    gapped = dataset(reading(month="2025-12"), reading(month="2026-02"))
    gap = [i for i in validate_dataset(gapped) if i.code == "GAP"]
    assert len(gap) == 1 and "2026-01" in gap[0].message


def test_gaps_are_judged_per_meter():
    # Gas starts later than electricity; that is not a gap in either series.
    issues = validate_dataset(
        dataset(
            reading("electric-main", "2025-06"),
            reading("electric-main", "2025-07"),
            reading("gas-main", "2025-07"),
        )
    )
    assert "GAP" not in codes(issues)


def test_change_over_40_percent_vs_same_month_last_year_is_warning():
    up = dataset(reading(month="2025-06", quantity=1000), reading(month="2026-06", quantity=1500))
    found = [i for i in validate_dataset(up) if i.code == "PLAUSIBILITY"]
    assert len(found) == 1 and found[0].level == "warning"
    assert "2026-06" in found[0].message

    down = dataset(reading(month="2025-06", quantity=1000), reading(month="2026-06", quantity=500))
    assert "PLAUSIBILITY" in codes(validate_dataset(down), "warning")


def test_change_of_exactly_40_percent_or_less_is_fine():
    at_limit = dataset(reading(month="2025-06", quantity=1000), reading(month="2026-06", quantity=1400))
    below = dataset(reading(month="2025-06", quantity=1000), reading(month="2026-06", quantity=700))
    for ds in (at_limit, below):
        assert "PLAUSIBILITY" not in codes(validate_dataset(ds))


def test_plausibility_needs_a_prior_year_reading_of_the_same_meter():
    no_prior = dataset(reading(month="2026-06", quantity=9999))
    other_meter = dataset(reading("gas-main", "2025-06", quantity=10), reading("electric-main", "2026-06", quantity=9999))
    zero_prior = dataset(reading(month="2025-06", quantity=0), reading(month="2026-06", quantity=50))
    for ds in (no_prior, other_meter, zero_prior):
        assert "PLAUSIBILITY" not in codes(validate_dataset(ds))


def test_chart_measured_without_uncertainty_is_warning():
    missing = dataset(reading("gas-main", "2026-01", quality="chart_measured"))
    found = [i for i in validate_dataset(missing) if i.code == "MISSING_UNCERTAINTY"]
    assert len(found) == 1 and found[0].level == "warning"

    present = dataset(reading("gas-main", "2026-01", quality="chart_measured", uncertainty_pct=2.0))
    assert "MISSING_UNCERTAINTY" not in codes(validate_dataset(present))


def test_other_qualities_do_not_need_uncertainty():
    for quality in ("bill", "chart_estimate", "utility_export"):
        assert "MISSING_UNCERTAINTY" not in codes(validate_dataset(dataset(reading(quality=quality))))


def test_overlapping_periods_is_error():
    a = reading(month="2026-09", period_start=date(2026, 8, 25), period_end=date(2026, 9, 25))
    b = reading(month="2026-10", period_start=date(2026, 9, 20), period_end=date(2026, 10, 20))
    found = [i for i in validate_dataset(dataset(a, b)) if i.code == "PERIOD_OVERLAP"]
    assert len(found) == 1 and found[0].level == "error"
    assert "electric-main" in found[0].message
    assert "2026-09" in found[0].message and "2026-10" in found[0].message


def test_overlap_by_two_shared_days_is_error():
    a = reading(month="2026-09", period_start=date(2026, 8, 25), period_end=date(2026, 9, 25))
    b = reading(month="2026-10", period_start=date(2026, 9, 24), period_end=date(2026, 10, 25))
    assert "PERIOD_OVERLAP" in codes(validate_dataset(dataset(a, b)), "error")


def test_adjacent_periods_sharing_one_boundary_day_is_ok():
    a = reading("gas-main", "2026-10", period_start=date(2026, 9, 4), period_end=date(2026, 10, 6))
    b = reading("gas-main", "2026-11", period_start=date(2026, 10, 6), period_end=date(2026, 11, 5))
    assert validate_dataset(dataset(a, b)) == []
    # A one-day gap between periods is not an overlap either.
    c = reading("gas-main", "2026-11", period_start=date(2026, 10, 7), period_end=date(2026, 11, 5))
    assert "PERIOD_OVERLAP" not in codes(validate_dataset(dataset(a, c)))


def test_overlap_needs_both_dates_on_both_readings_and_the_same_meter():
    dated = reading(month="2026-09", period_start=date(2026, 8, 25), period_end=date(2026, 9, 25))
    undated = reading(month="2026-10")
    half = reading(month="2026-10", period_start=date(2026, 9, 1))
    other_meter = reading("gas-main", "2026-10", period_start=date(2026, 9, 1), period_end=date(2026, 10, 1))
    for other in (undated, half, other_meter):
        assert "PERIOD_OVERLAP" not in codes(validate_dataset(dataset(dated, other)))


# --- Task 1b: enumerations, transcription guards, free-text guards ---------


def milestone(kind="boiler", **kw):
    fields = dict(date=date(2021, 1, 1), kind=kind, label_en="Boiler installed (2021)", label_es="Caldera instalada (2021)")
    fields.update(kw)
    return Milestone(**fields)


def commute_month(quality="estimate", miles=400.0):
    return CommuteMonth("rector", "2026-01", miles, quality)


def vehicle(kwh_per_mile=0.3):
    return Vehicle("rector", "Rector", kwh_per_mile, "home", "RFCW")


def invalid_values(ds):
    return [i for i in validate_dataset(ds) if i.code == "INVALID_VALUE"]


def test_sample_data_still_validates_with_no_issues():
    assert validate_dataset(load_dataset(DATA_DIR)) == []


def test_invalid_milestone_kind_is_error():
    found = invalid_values(dataset(reading(), milestones=[milestone("refurbished")]))
    assert len(found) == 1 and found[0].level == "error"
    assert "refurbished" in found[0].message
    for kind in ("boiler", "solar_connected", "other"):
        assert invalid_values(dataset(reading(), milestones=[milestone(kind)])) == []


def test_invalid_milestone_precision_is_error():
    found = invalid_values(dataset(reading(), milestones=[milestone(precision="week")]))
    assert len(found) == 1 and found[0].level == "error"
    assert "week" in found[0].message and "precision" in found[0].message
    for precision in ("year", "month", "day"):
        assert invalid_values(dataset(reading(), milestones=[milestone(precision=precision)])) == []


def solar_label_errors(*milestones):
    return [i for i in validate_dataset(dataset(reading(), milestones=list(milestones))) if i.code == "SOLAR_LABEL"]


@pytest.mark.parametrize("kind", ["boiler", "other"])
@pytest.mark.parametrize(
    "english, spanish",
    [
        ("Solar panels quoted", "Cotización de paneles"),
        ("Boiler serviced", "Revisión de la caldera, energía solar"),
        ("SOLAR walk-through", "Visita"),
        ("Non-solar upgrade", "Mejora"),
        ("Visit", "Se instala SOLAR"),
    ],
)
def test_solar_in_the_label_of_a_milestone_that_is_not_solar_connected_is_an_error(kind, english, spanish):
    found = solar_label_errors(milestone(kind, label_en=english, label_es=spanish))
    assert len(found) == 1 and found[0].level == "error"
    assert "solar_connected" in found[0].message
    assert english not in found[0].message and spanish not in found[0].message  # the label is not echoed


def test_solar_in_both_labels_is_one_error_per_label():
    assert len(solar_label_errors(milestone("other", label_en="Solar plan", label_es="Plan solar"))) == 2


def test_solar_connected_milestones_may_say_solar_and_other_words_are_not_flagged():
    assert solar_label_errors(milestone("solar_connected", label_en="Solar system connected", label_es="Sistema solar conectado")) == []
    assert solar_label_errors(milestone("other", label_en="Solarium repainted", label_es="Se pintó el solárium")) == []
    assert solar_label_errors(milestone()) == []


def test_invalid_commute_quality_is_error():
    found = invalid_values(dataset(reading(), commute=[commute_month("guess")]))
    assert len(found) == 1 and found[0].level == "error"
    assert "guess" in found[0].message
    for quality in ("estimate", "odometer"):
        assert invalid_values(dataset(reading(), commute=[commute_month(quality)])) == []
    # The campus-reading qualities are not commute qualities.
    assert invalid_values(dataset(reading(), commute=[commute_month("bill")]))


def test_reading_period_end_in_a_different_month_than_the_label_is_error():
    mismatch = reading(month="2026-10", period_start=date(2026, 9, 30), period_end=date(2026, 11, 1))
    found = [i for i in validate_dataset(dataset(mismatch)) if i.code == "PERIOD_MONTH_MISMATCH"]
    assert len(found) == 1 and found[0].level == "error"
    assert "2026-10" in found[0].message and "2026-11-01" in found[0].message

    matching = reading(month="2026-10", period_start=date(2026, 9, 25), period_end=date(2026, 10, 27))
    assert "PERIOD_MONTH_MISMATCH" not in codes(validate_dataset(dataset(matching)))
    # A reading with only period_end is still checked against its month label.
    only_end = reading(month="2026-10", period_end=date(2026, 11, 1))
    assert "PERIOD_MONTH_MISMATCH" in codes(validate_dataset(dataset(only_end)), "error")
    # No period_end, nothing to compare.
    assert "PERIOD_MONTH_MISMATCH" not in codes(validate_dataset(dataset(reading(month="2026-10"))))


def test_negative_quantity_is_invalid_value():
    found = invalid_values(dataset(reading(quantity=-5)))
    assert len(found) == 1 and found[0].level == "error"
    assert invalid_values(dataset(reading(quantity=0))) == []


def test_uncertainty_pct_outside_0_to_100_is_invalid_value():
    for bad in (-0.1, 100.5, 250):
        assert invalid_values(dataset(reading(uncertainty_pct=bad))), bad
    for fine in (0, 2, 100):
        assert invalid_values(dataset(reading(uncertainty_pct=fine))) == [], fine
    assert invalid_values(dataset(reading(uncertainty_pct=None))) == []


def test_peak_kw_must_be_above_zero_when_present():
    for bad in (0, -1.5):
        assert invalid_values(dataset(reading(peak_kw=bad))), bad
    assert invalid_values(dataset(reading(peak_kw=9.68))) == []
    assert invalid_values(dataset(reading(peak_kw=None))) == []


def test_negative_commute_miles_is_invalid_value():
    assert invalid_values(dataset(reading(), commute=[commute_month(miles=-1)]))
    for fine in (0, 400):
        assert invalid_values(dataset(reading(), commute=[commute_month(miles=fine)])) == []


def test_vehicle_kwh_per_mile_must_be_above_zero():
    for bad in (0, -0.3):
        assert invalid_values(dataset(reading(), vehicles=[vehicle(bad)])), bad
    assert invalid_values(dataset(reading(), vehicles=[vehicle(0.3)])) == []
    assert invalid_values(dataset(reading(), vehicles=[])) == []


def test_zero_quantity_is_warning():
    issues = validate_dataset(dataset(reading(quantity=0)))
    found = [i for i in issues if i.code == "ZERO_QUANTITY"]
    assert len(found) == 1 and found[0].level == "warning"
    assert "ZERO_QUANTITY" not in codes(validate_dataset(dataset(reading(quantity=1))))


def vehicle_issues(code, **others):
    return [i for i in validate_dataset(dataset(reading(), **others)) if i.code == code]


def test_duplicate_vehicle_is_error():
    twice = [vehicle(0.3), vehicle(0.4)]
    found = vehicle_issues("DUPLICATE_VEHICLE", vehicles=twice)
    assert len(found) == 1 and found[0].level == "error"
    assert "rector" in found[0].message and "2" in found[0].message
    assert vehicle_issues("DUPLICATE_VEHICLE", vehicles=[vehicle(0.3)]) == []


def test_commute_row_for_an_unknown_vehicle_is_error():
    found = vehicle_issues("UNKNOWN_VEHICLE", vehicles=[vehicle()], commute=[CommuteMonth("spare-car", "2026-01", 400.0, "estimate")])
    assert len(found) == 1 and found[0].level == "error"
    assert "spare-car" in found[0].message and "2026-01" in found[0].message
    # A row for a listed vehicle is fine, and so is no commute at all.
    assert vehicle_issues("UNKNOWN_VEHICLE", vehicles=[vehicle()], commute=[commute_month()]) == []
    assert vehicle_issues("UNKNOWN_VEHICLE", vehicles=[vehicle()]) == []


def test_duplicate_commute_month_is_error():
    twice = [commute_month(miles=400.0), commute_month("odometer", miles=450.0)]
    found = vehicle_issues("DUPLICATE_COMMUTE", vehicles=[vehicle()], commute=twice)
    assert len(found) == 1 and found[0].level == "error"
    assert "rector" in found[0].message and "2026-01" in found[0].message
    # The same month for another vehicle, or another month for the same vehicle, is not a duplicate.
    other_vehicle = [vehicle(), Vehicle("spare", "Spare", 0.3, "home", "RFCW")]
    other_month = [commute_month(), CommuteMonth("rector", "2026-02", 400.0, "estimate")]
    assert vehicle_issues("DUPLICATE_COMMUTE", vehicles=other_vehicle, commute=[commute_month(), CommuteMonth("spare", "2026-01", 400.0, "estimate")]) == []
    assert vehicle_issues("DUPLICATE_COMMUTE", vehicles=[vehicle()], commute=other_month) == []


def test_the_sample_has_no_duplicate_or_unknown_vehicle_rows():
    found = {i.code for i in validate_dataset(load_dataset(DATA_DIR))}
    assert not found & {"DUPLICATE_VEHICLE", "UNKNOWN_VEHICLE", "DUPLICATE_COMMUTE"}


def test_duplicate_meter_is_error():
    twice = METERS + [Meter("electric-main", "electricity", "Again", "")]
    found = [i for i in validate_dataset(dataset(reading(), meters=twice)) if i.code == "DUPLICATE_METER"]
    assert len(found) == 1 and found[0].level == "error"
    assert "electric-main" in found[0].message
    assert "DUPLICATE_METER" not in codes(validate_dataset(dataset(reading())))


FREE_TEXT_FIELDS = [
    "meter.label",
    "meter.notes",
    "reading.note",
    "milestone.label_en",
    "milestone.label_es",
    "vehicle.label",
    "vehicle.kwh_per_mile_source",
    "vehicle.note",
    "commute.note",
    "factor.edition",
    "factor.revision",
    "factor.edition_es",
    "factor.revision_es",
]


def with_text(field, text):
    """A clean dataset with `text` placed in one free-text field."""
    parts = {
        "meter": Meter("electric-main", "electricity", "Campus electricity", ""),
        "reading": reading(),
        "milestone": milestone(),
        "vehicle": vehicle(),
        "commute": commute_month(),
        "factor": factor(),
    }
    obj, name = field.split(".")
    parts[obj] = replace(parts[obj], **{name: text})
    return Dataset(
        meters=[parts["meter"]],
        readings=[parts["reading"]],
        milestones=[parts["milestone"]],
        vehicles=[parts["vehicle"]],
        commute=[parts["commute"]],
        factors=[parts["factor"]],
    )


def test_clean_free_text_has_no_issues():
    assert validate_dataset(with_text("reading.note", "")) == []


@pytest.mark.parametrize("field", FREE_TEXT_FIELDS)
def test_dollar_amount_in_text_is_error(field):
    for text in ("paid $142", "costs $ 1,200 a month"):
        found = [i for i in validate_dataset(with_text(field, text)) if i.code == "DOLLAR_IN_TEXT"]
        assert len(found) == 1 and found[0].level == "error", (field, text)
        assert field.split(".")[1] in found[0].message
        assert "142" not in found[0].message and "1,200" not in found[0].message  # never echo the text


@pytest.mark.parametrize("field", FREE_TEXT_FIELDS)
def test_long_digit_run_in_text_is_error(field):
    found = [i for i in validate_dataset(with_text(field, "acct 123456789")) if i.code == "LONG_DIGITS_IN_TEXT"]
    assert len(found) == 1 and found[0].level == "error", field
    assert "123456789" not in found[0].message  # never echo the number


@pytest.mark.parametrize("field", FREE_TEXT_FIELDS)
def test_ordinary_numbers_in_text_are_fine(field):
    text = "24 CCF at a 1.051 BTU factor equals 25.22 therms; 2,416 vs 2,415; installed 2021; 12345678 and $ per unit"
    issues = validate_dataset(with_text(field, text))
    assert "DOLLAR_IN_TEXT" not in codes(issues) and "LONG_DIGITS_IN_TEXT" not in codes(issues)


# --- factors: Spanish edition and revision (Task 9b, F4) ---


def test_a_factor_with_both_spanish_texts_is_clean():
    assert validate_dataset(dataset(reading(), factors=[factor()])) == []


@pytest.mark.parametrize("missing", ["edition_es", "revision_es"])
def test_a_factor_without_its_spanish_text_is_a_warning_that_names_the_factor(missing):
    issues = validate_dataset(dataset(reading(), factors=[factor(**{missing: ""})]))
    found = [i for i in issues if i.code == "FACTOR_NO_SPANISH"]
    assert len(found) == 1 and found[0].level == "warning"
    assert "natural_gas" in found[0].message and missing in found[0].message
    assert codes(issues, "error") == []  # the build carries on; it only asks a person to add the Spanish


def test_the_sample_factors_all_have_spanish_text():
    issues = validate_dataset(load_dataset(DATA_DIR))
    assert [i.message for i in issues if i.code == "FACTOR_NO_SPANISH"] == []


# --- checks that depend on the parish settings ---------------------------------------------------------------

GRID = factor(factor_id="electricity_rfcw", value=0.4, unit="kg CO2e/kWh")
FAKE_GRID = factor(factor_id="electricity_zzzt", value=0.9, unit="kg CO2e/kWh")  # a made-up subregion, test only
GAS = factor()
GASOLINE = factor(factor_id="gasoline_car_per_mile", value=0.3, unit="kg CO2e/mile")


def test_the_sample_validates_clean_against_its_own_settings():
    assert validate_dataset(sample_dataset(), SETTINGS) == []


def test_an_unknown_subregion_is_an_error_that_lists_the_ones_the_factors_file_has():
    ds = dataset(reading(), factors=[GRID, FAKE_GRID, GAS])
    issues = validate_dataset(ds, settings(egrid_subregion="NOPE"))
    found = [i for i in issues if i.code == "UNKNOWN_SUBREGION"]
    assert len(found) == 1 and found[0].level == "error"
    message = found[0].message
    assert '"NOPE"' in message and "electricity_nope" in message
    assert "RFCW, ZZZT" in message, "the available subregions are listed, in capitals, sorted"
    assert "egrid_subregion" in message and "Power Profiler" in message


def test_a_factors_file_with_no_electricity_rows_says_none_are_available():
    issues = validate_dataset(dataset(reading(), factors=[GAS]), settings(egrid_subregion="RFCW"))
    assert [i.message for i in issues if i.code == "UNKNOWN_SUBREGION"][0].count("none") == 1


@pytest.mark.parametrize("subregion", ["RFCW", "ZZZT"])
def test_a_subregion_that_has_its_row_is_fine_however_many_other_rows_there_are(subregion):
    ds = dataset(reading(), factors=[GRID, FAKE_GRID, GAS, GASOLINE])
    assert validate_dataset(ds, settings(egrid_subregion=subregion)) == []


def test_without_settings_no_subregion_is_required():
    assert "UNKNOWN_SUBREGION" not in codes(validate_dataset(dataset(reading(), factors=[GAS])))


def test_a_missing_natural_gas_factor_is_an_error_when_there_is_a_gas_meter():
    issues = validate_dataset(dataset(reading(), factors=[GRID]), SETTINGS)
    assert [i.code for i in issues if i.level == "error"] == ["MISSING_FACTOR"]
    assert "natural_gas" in issues[0].message
    electric_only = [METERS[0]]
    assert validate_dataset(dataset(reading(), meters=electric_only, factors=[GRID]), SETTINGS) == []


def test_travel_rows_need_the_gasoline_factor_and_the_grid_row_where_the_vehicle_charges():
    car = Vehicle("ev", "Test EV", 0.3, "home", "AAAT")
    miles = [CommuteMonth("ev", "2026-01", 100, "estimate")]
    issues = validate_dataset(dataset(reading(), factors=[GRID, GAS], vehicles=[car], commute=miles), SETTINGS)
    found = {i.code: i.message for i in issues if i.level == "error"}
    assert set(found) == {"MISSING_FACTOR", "UNKNOWN_SUBREGION"}
    assert "gasoline_car_per_mile" in found["MISSING_FACTOR"]
    assert "electricity_aaat" in found["UNKNOWN_SUBREGION"] and "vehicle ev" in found["UNKNOWN_SUBREGION"]

    # A vehicle with no miles, or one that charges at the church, needs no row of its own.
    parked = dataset(reading(), factors=[GRID, GAS], vehicles=[car])
    assert validate_dataset(parked, SETTINGS) == []
    at_church = replace(car, charging_place="church")
    ok = dataset(reading(), factors=[GRID, GAS, GASOLINE], vehicles=[at_church], commute=miles)
    assert validate_dataset(ok, SETTINGS) == []


def test_a_factor_id_listed_twice_is_an_error():
    issues = validate_dataset(dataset(reading(), factors=[GRID, GAS, replace(GRID, value=0.5)]), SETTINGS)
    found = [i for i in issues if i.code == "DUPLICATE_FACTOR"]
    assert len(found) == 1 and found[0].level == "error" and "electricity_rfcw" in found[0].message


@pytest.mark.parametrize("value", [0, -0.4])
def test_a_factor_value_of_zero_or_less_is_an_error(value):
    issues = validate_dataset(dataset(reading(), factors=[replace(GRID, value=value), GAS]), SETTINGS)
    assert [i.code for i in issues if i.level == "error"] == ["INVALID_VALUE"]


def test_only_the_factors_the_parish_uses_are_checked_for_spanish_text():
    # Another subregion's row with no Spanish wording is not this parish's concern.
    other = replace(FAKE_GRID, edition_es="", revision_es="")
    assert validate_dataset(dataset(reading(), factors=[GRID, GAS, other]), SETTINGS) == []
    # Its own row is: both empty columns are named.
    here = settings(egrid_subregion="ZZZT")
    found = [i for i in validate_dataset(dataset(reading(), factors=[GRID, GAS, other]), here) if i.code == "FACTOR_NO_SPANISH"]
    assert len(found) == 2 and all("electricity_zzzt" in i.message for i in found)


def test_an_english_only_site_does_not_ask_for_spanish_factor_text_or_spanish_labels():
    english = settings(languages=("en",))
    bare = replace(GRID, edition_es="", revision_es="")
    milestone = Milestone(date(2019, 1, 1), "boiler", "Boiler replaced (2019)", "", "year")
    ds = dataset(reading(), factors=[bare, replace(GAS, edition_es="", revision_es="")], milestones=[milestone])
    assert validate_dataset(ds, english) == []

    issues = validate_dataset(ds, SETTINGS)
    assert codes(issues, "warning").count("FACTOR_NO_SPANISH") == 4
    assert codes(issues, "error") == ["MISSING_SPANISH"]


def test_unused_free_text_in_another_subregions_row_is_not_judged():
    other = replace(FAKE_GRID, edition="Edition 123456789")  # would be LONG_DIGITS_IN_TEXT if it were shown
    assert validate_dataset(dataset(reading(), factors=[GRID, GAS, other]), SETTINGS) == []
    assert "LONG_DIGITS_IN_TEXT" in codes(validate_dataset(dataset(reading(), factors=[GRID, GAS, other]), settings(egrid_subregion="ZZZT")))
