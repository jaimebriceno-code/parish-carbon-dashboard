from dataclasses import replace

import pytest

from carbon.calc import (
    CommuteResult,
    MonthEmissions,
    Period,
    commute_results,
    factor_value,
    monthly_emissions,
    select_baseline,
    twelve_month_periods,
)
from carbon.calc import available_subregions, factors_in_use
from carbon.models import CommuteMonth, Dataset, Factor, Meter, Reading, Vehicle
from tests.support import SAMPLE_VEHICLE, sample_dataset

ELECTRIC = "electric-main"
GAS = "gas-main"


def _factor(factor_id, value, unit):
    return Factor(
        factor_id=factor_id,
        value=value,
        unit=unit,
        gases="",
        source="test",
        edition="test",
        revision="test",
        url="",
        retrieved="2026-01-01",
    )


# The brief's test factors: 0.4 kg per kWh, 5.3 per therm, 0.4 per mile.
FACTORS = [
    _factor("electricity_rfcw", 0.4, "kg CO2e/kWh"),
    _factor("natural_gas", 5.3, "kg CO2e/therm"),
    _factor("gasoline_car_per_mile", 0.4, "kg CO2e/mile"),
]

METERS = [
    Meter(ELECTRIC, "electricity", "Campus electricity"),
    Meter(GAS, "natural_gas", "Campus natural gas"),
]


def _months(first, count):
    """`count` consecutive YYYY-MM strings starting at `first` (independent of carbon.calc)."""
    year, month = int(first[:4]), int(first[5:7])
    out = []
    for _ in range(count):
        out.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def _reading(meter_id, month, quantity, quality="bill"):
    unit = "kWh" if meter_id.startswith("electric") else "therm"
    return Reading(meter_id=meter_id, period_end_month=month, quantity=quantity, unit=unit, data_quality=quality)


def _series(meter_id, first, count, quantity, quality="bill", skip=()):
    return [_reading(meter_id, m, quantity, quality) for m in _months(first, count) if m not in skip]


def _dataset(readings, *, meters=None, factors=None, vehicles=(), commute=(), grid_subregion="RFCW", baseline_year=2025):
    return Dataset(
        meters=list(METERS if meters is None else meters),
        readings=list(readings),
        factors=list(FACTORS if factors is None else factors),
        vehicles=list(vehicles),
        commute=list(commute),
        grid_subregion=grid_subregion,
        baseline_year=baseline_year,
    )


def _both(first, count, kwh=1000, therms=100, quality="bill"):
    return _series(ELECTRIC, first, count, kwh, quality) + _series(GAS, first, count, therms, quality)


# --- factor_value and monthly emissions -------------------------------------


def test_factor_value_returns_the_value_and_rejects_an_unknown_id():
    ds = _dataset([])
    assert factor_value(ds, "natural_gas") == 5.3
    with pytest.raises(ValueError, match="no_such_factor"):
        factor_value(ds, "no_such_factor")


def test_monthly_emissions_multiplies_by_factor():
    ds = _dataset(
        [
            _reading(ELECTRIC, "2026-01", 1000),
            _reading(GAS, "2026-01", 100, quality="chart_measured"),
        ]
    )

    rows = monthly_emissions(ds)

    assert rows == [
        MonthEmissions(
            meter_id=ELECTRIC,
            fuel="electricity",
            period_end_month="2026-01",
            quantity=1000,
            unit="kWh",
            tco2e=pytest.approx(0.4),
            data_quality="bill",
            factor_id="electricity_rfcw",
        ),
        MonthEmissions(
            meter_id=GAS,
            fuel="natural_gas",
            period_end_month="2026-01",
            quantity=100,
            unit="therm",
            tco2e=pytest.approx(0.53),
            data_quality="chart_measured",
            factor_id="natural_gas",
        ),
    ]


def test_monthly_emissions_names_a_reading_whose_meter_is_unknown():
    ds = _dataset([_reading("mystery-meter", "2026-01", 5)])
    with pytest.raises(ValueError, match="mystery-meter"):
        monthly_emissions(ds)


# --- twelve-month periods ----------------------------------------------------


def test_twelve_month_periods_sums_each_fuel_and_orders_windows_oldest_first():
    # 14 months of both meters give three windows, ending in the 12th, 13th and 14th months.
    ds = _dataset(_both("2025-01", 14, kwh=1000, therms=100))

    periods = twelve_month_periods(ds)

    assert [p.months[-1] for p in periods] == ["2025-12", "2026-01", "2026-02"]
    first = periods[0]
    assert isinstance(first, Period)
    assert len(first.months) == 12
    assert first.months[0] == "2025-01"
    assert first.electricity_kwh == pytest.approx(12000)
    assert first.gas_therms == pytest.approx(1200)
    assert first.tco2e_electricity == pytest.approx(4.8)
    assert first.tco2e_gas == pytest.approx(6.36)
    assert first.tco2e_total == pytest.approx(11.16)
    assert first.estimated_share == 0.0
    assert first.provisional is False


def test_period_label_is_the_year_for_calendar_2025_otherwise_first_to_last_month():
    ds = _dataset(_both("2025-01", 24))

    labels = {p.months[-1]: p.label for p in twelve_month_periods(ds)}

    assert labels["2025-12"] == "2025"
    assert labels["2026-10"] == "2025-11 to 2026-10"
    assert labels["2026-12"] == "2026-01 to 2026-12"


def test_window_with_a_missing_month_is_not_complete():
    missing = "2025-08"
    ds = _dataset(_series(ELECTRIC, "2025-01", 24, 1000) + _series(GAS, "2025-01", 24, 100, skip=(missing,)))

    periods = twelve_month_periods(ds)

    # Windows ending 2025-12 to 2026-07 all contain the gap. The first complete one ends 2026-08.
    assert [p.months[-1] for p in periods] == ["2026-08", "2026-09", "2026-10", "2026-11", "2026-12"]
    assert all(missing not in p.months for p in periods)
    # No zero row was invented for the gap: only the electricity reading exists for that month.
    assert [r.meter_id for r in monthly_emissions(ds) if r.period_end_month == missing] == [ELECTRIC]
    # Each complete window sums only real readings: 12 months of 100 therms.
    assert all(p.gas_therms == pytest.approx(1200) for p in periods)


def test_window_needs_every_meter_in_the_dataset():
    # Electricity is complete but gas has only 11 months: no window at all.
    ds = _dataset(_series(ELECTRIC, "2025-01", 12, 1000) + _series(GAS, "2025-02", 11, 100))
    assert twelve_month_periods(ds) == []

    # A listed meter that has no readings at all also blocks every window.
    third = METERS + [Meter("electric-annex", "electricity", "Annex")]
    ds = _dataset(_both("2025-01", 12), meters=third)
    assert twelve_month_periods(ds) == []


def test_dataset_with_only_electricity_has_no_complete_window():
    # Twelve full months of electricity, but no gas meter at all: a baseline would understate the footprint.
    electric_only = [METERS[0]]
    ds = _dataset(_series(ELECTRIC, "2025-01", 12, 1000), meters=electric_only)

    assert twelve_month_periods(ds) == []
    assert select_baseline(ds) is None


def test_dataset_with_only_gas_has_no_complete_window():
    gas_only = [METERS[1]]
    ds = _dataset(_series(GAS, "2025-01", 12, 100), meters=gas_only)

    assert twelve_month_periods(ds) == []
    assert select_baseline(ds) is None


def test_window_sums_every_meter_of_a_fuel():
    meters = METERS + [Meter("electric-annex", "electricity", "Annex")]
    readings = _both("2025-01", 12) + _series("electric-annex", "2025-01", 12, 500)

    (period,) = twelve_month_periods(_dataset(readings, meters=meters))

    assert period.electricity_kwh == pytest.approx(12 * 1500)


def test_duplicate_readings_for_one_meter_and_month_are_rejected_not_summed():
    readings = _both("2025-01", 12) + [_reading(ELECTRIC, "2025-06", 999)]
    with pytest.raises(ValueError, match="2025-06"):
        twelve_month_periods(_dataset(readings))


def test_no_readings_means_no_periods():
    assert twelve_month_periods(_dataset([])) == []
    assert twelve_month_periods(Dataset()) == []


# --- baseline ----------------------------------------------------------------


def test_select_baseline_prefers_complete_calendar_2025():
    # Data runs well past 2025, so a later complete window exists; calendar 2025 still wins.
    ds = _dataset(_both("2025-01", 24))

    baseline = select_baseline(ds)

    assert baseline is not None
    assert baseline.label == "2025"
    assert baseline.months[0] == "2025-01"
    assert baseline.months[-1] == "2025-12"
    assert baseline.provisional is False


def test_select_baseline_calendar_2025_is_provisional_while_any_reading_is_estimated():
    readings = _both("2025-01", 24)
    readings[3] = _reading(ELECTRIC, "2025-04", 1000, quality="chart_estimate")

    baseline = select_baseline(_dataset(readings))

    assert baseline.label == "2025"
    assert baseline.provisional is True
    assert baseline.estimated_share == pytest.approx(0.4 / 11.16)


def test_exact_utility_export_data_is_not_provisional():
    ds = _dataset(_series(ELECTRIC, "2025-01", 12, 1000, "utility_export") + _series(GAS, "2025-01", 12, 100, "bill"))
    assert select_baseline(ds).provisional is False


def test_select_baseline_falls_back_to_latest_twelve_months_provisional():
    # Electricity from 2025-06, gas from 2025-10, as in the real seed; every reading is a bill.
    ds = _dataset(_series(ELECTRIC, "2025-06", 17, 1000) + _series(GAS, "2025-10", 13, 100))

    baseline = select_baseline(ds)

    assert baseline is not None
    assert baseline.label == "2025-11 to 2026-10"
    assert baseline.months[0] == "2025-11"
    assert baseline.months[-1] == "2026-10"
    assert baseline.provisional is True  # the fallback is provisional even when every reading is a bill
    assert baseline.estimated_share == 0.0
    # The same window computed directly is not provisional: only the fallback sets it.
    direct = [p for p in twelve_month_periods(ds) if p.label == baseline.label][0]
    assert direct.provisional is False


def test_select_baseline_none_when_no_complete_window():
    assert select_baseline(_dataset(_both("2025-01", 11))) is None
    assert select_baseline(_dataset([])) is None
    assert select_baseline(Dataset()) is None


def test_estimated_share_is_emissions_weighted():
    readings = (
        # Electricity: 11 bill months of 1000 kWh (4.4 t) and one estimate of 2000 kWh (0.8 t).
        [_reading(ELECTRIC, m, 2000 if m == "2026-01" else 1000, "chart_estimate" if m == "2026-01" else "bill")
         for m in _months("2025-03", 12)]
        # Gas: 11 months of 100 therms plus one measured month of 300 therms (1.59 t).
        + [_reading(GAS, m, 300 if m == "2026-02" else 100, "chart_measured" if m == "2026-02" else "bill")
           for m in _months("2025-03", 12)]
    )

    baseline = select_baseline(_dataset(readings))

    total = 5.2 + (1100 + 300) * 5.3 / 1000  # 12.62 t
    estimated = 0.8 + 300 * 5.3 / 1000  # 2.39 t
    assert baseline.tco2e_total == pytest.approx(total)
    assert baseline.estimated_share == pytest.approx(estimated / total)
    assert baseline.estimated_share != pytest.approx(2 / 24)  # not a share of readings
    assert baseline.provisional is True


def test_zero_total_baseline_does_not_raise():
    readings = _both("2025-01", 12, kwh=0, therms=0, quality="chart_estimate")

    baseline = select_baseline(_dataset(readings))

    assert baseline is not None
    assert baseline.tco2e_total == 0
    assert baseline.estimated_share == 0.0
    assert baseline.provisional is True


def test_sample_baseline_is_nov_2025_to_oct_2026_provisional():
    ds = sample_dataset()

    baseline = select_baseline(ds)

    assert baseline is not None
    assert baseline.label == "2025-11 to 2026-10"
    assert len(baseline.months) == 12
    assert baseline.months[0] == "2025-11"
    assert baseline.months[-1] == "2026-10"
    assert baseline.provisional is True
    assert 0.0 < baseline.estimated_share < 1.0  # the sample has one estimated month for each fuel
    # The totals agree with the quantities and the EPA factors recorded in the factors file.
    assert baseline.electricity_kwh == 33430
    assert baseline.gas_therms == 6029
    assert baseline.tco2e_electricity == pytest.approx(
        baseline.electricity_kwh * factor_value(ds, "electricity_rfcw") / 1000
    )
    assert baseline.tco2e_gas == pytest.approx(baseline.gas_therms * factor_value(ds, "natural_gas") / 1000)
    assert baseline.tco2e_total == pytest.approx(baseline.tco2e_electricity + baseline.tco2e_gas)


def test_sample_travel_line_prices_6500_estimated_miles_with_the_recorded_factors():
    # The sample car: 25 miles on each of the 260 weekdays from Nov 2025 to Oct 2026, charged away from the
    # campus meters. Priced by the calc functions with whatever the factors file says.
    ds = sample_dataset()
    results = commute_results(ds)
    assert [r.period_end_month for r in results] == _months("2025-11", 12)
    assert sum(r.miles for r in results) == 6500
    assert all(r.data_quality == "estimate" and not r.counted_in_campus for r in results)

    # The same miles in one entry give the same totals, so the twelve rows lose nothing to rounding or ordering.
    whole = commute_results(replace(ds, commute=[CommuteMonth(SAMPLE_VEHICLE, "2026-10", 6500, "estimate")]))[0]
    counted = sum(r.tco2e_counted_here for r in results)
    avoided = sum(r.avoided_tco2e for r in results)
    assert counted == pytest.approx(whole.tco2e_counted_here)
    assert avoided == pytest.approx(whole.avoided_tco2e)
    assert sum(r.ev_kwh for r in results) == pytest.approx(whole.ev_kwh)

    # Worked out again from the factors file and the vehicle row: kWh, then t CO2e, then the gasoline comparison.
    vehicle = ds.vehicles[0]
    kwh = 6500 * vehicle.kwh_per_mile
    assert whole.ev_kwh == pytest.approx(kwh) == pytest.approx(1950)
    assert counted == pytest.approx(kwh * factor_value(ds, "electricity_rfcw") / 1000)
    gasoline = 6500 * factor_value(ds, "gasoline_car_per_mile") / 1000
    assert avoided == pytest.approx(gasoline - counted)
    assert counted < gasoline


def test_sample_travel_never_changes_the_campus_baseline():
    ds = sample_dataset()
    assert ds.commute, "the sample has travel entries for this to mean anything"
    baseline = select_baseline(ds)
    assert baseline == select_baseline(replace(ds, vehicles=[], commute=[]))


# --- the grid subregion and the baseline year come from the settings, by way of the dataset -------------


def test_electricity_is_priced_with_the_factor_of_the_datasets_own_subregion():
    # A clearly made-up subregion, present only in this test's factors.
    factors = FACTORS + [_factor("electricity_zzzt", 0.9, "kg CO2e/kWh")]
    readings = [_reading(ELECTRIC, "2026-01", 1000), _reading(GAS, "2026-01", 100)]

    here = monthly_emissions(_dataset(readings, factors=factors, grid_subregion="ZZZT"))
    there = monthly_emissions(_dataset(readings, factors=factors, grid_subregion="RFCW"))

    assert (here[0].factor_id, here[0].tco2e) == ("electricity_zzzt", pytest.approx(0.9))
    assert (there[0].factor_id, there[0].tco2e) == ("electricity_rfcw", pytest.approx(0.4))
    assert here[1] == there[1]  # natural gas has one factor everywhere


def test_the_subregion_code_is_matched_whatever_its_capitals():
    readings = [_reading(ELECTRIC, "2026-01", 1000)]
    assert monthly_emissions(_dataset(readings, grid_subregion="rfcw"))[0].factor_id == "electricity_rfcw"


def test_a_dataset_that_names_no_subregion_cannot_price_electricity_and_says_why():
    with pytest.raises(ValueError, match="egrid_subregion"):
        monthly_emissions(_dataset([_reading(ELECTRIC, "2026-01", 1000)], grid_subregion=""))
    # Gas alone needs no subregion.
    assert monthly_emissions(_dataset([_reading(GAS, "2026-01", 100)], grid_subregion=""))[0].factor_id == "natural_gas"


def test_a_subregion_with_no_factor_row_is_named_and_never_replaced_by_another():
    with pytest.raises(ValueError, match="electricity_zzzt"):
        monthly_emissions(_dataset([_reading(ELECTRIC, "2026-01", 1000)], grid_subregion="ZZZT"))


def test_available_subregions_lists_every_electricity_row_in_capitals():
    factors = FACTORS + [_factor("electricity_zzzt", 0.9, "kg CO2e/kWh"), _factor("electricity_aaat", 0.1, "kg CO2e/kWh")]
    assert available_subregions(_dataset([], factors=factors)) == ["AAAT", "RFCW", "ZZZT"]
    assert available_subregions(Dataset()) == []


def test_factors_in_use_are_the_campus_grid_gas_and_only_with_travel_the_gasoline_comparison():
    factors = FACTORS + [_factor("electricity_zzzt", 0.9, "kg CO2e/kWh"), _factor("electricity_aaat", 0.1, "kg CO2e/kWh")]
    ds = _dataset([], factors=factors, grid_subregion="ZZZT")

    assert [f.factor_id for f in factors_in_use(ds, travel=False)] == ["natural_gas", "electricity_zzzt"]
    assert [f.factor_id for f in factors_in_use(ds)] == ["natural_gas", "gasoline_car_per_mile", "electricity_zzzt"]


def test_factors_in_use_include_the_grid_a_driven_vehicle_charges_on_away_from_the_campus():
    factors = FACTORS + [_factor("electricity_zzzt", 0.9, "kg CO2e/kWh"), _factor("electricity_aaat", 0.1, "kg CO2e/kWh")]
    away = Vehicle("ev-away", "Test EV", 0.3, "home", "AAAT")
    parked = Vehicle("ev-parked", "Test EV", 0.3, "home", "ZZZT")  # listed, but it has no miles
    ds = _dataset([], factors=factors, vehicles=[away, parked], commute=[CommuteMonth("ev-away", "2026-01", 100, "estimate")])

    assert {f.factor_id for f in factors_in_use(ds)} == {"electricity_rfcw", "natural_gas", "gasoline_car_per_mile", "electricity_aaat"}
    assert "electricity_aaat" not in {f.factor_id for f in factors_in_use(ds, travel=False)}


def test_the_baseline_year_is_the_datasets_not_a_fixed_2025():
    readings = _both("2025-01", 36)

    by_default = select_baseline(_dataset(readings))
    later = select_baseline(_dataset(readings, baseline_year=2026))

    assert by_default.label == "2025" and by_default.months[0] == "2025-01"
    assert later.label == "2026" and later.months == tuple(_months("2026-01", 12))
    assert later.provisional is False
    # With 2026 as the baseline year, calendar 2025 is just another twelve-month window.
    labels = {p.months[-1]: p.label for p in twelve_month_periods(_dataset(readings, baseline_year=2026))}
    assert labels["2025-12"] == "2025-01 to 2025-12" and labels["2026-12"] == "2026"


def test_a_baseline_year_with_no_complete_data_falls_back_to_the_latest_window_marked_provisional():
    baseline = select_baseline(_dataset(_both("2025-01", 18), baseline_year=2027))
    assert baseline.label == "2025-07 to 2026-06"
    assert baseline.provisional is True


# --- staff commute -----------------------------------------------------------


def _vehicle(place="home", region="RFCW", kwh_per_mile=0.3):
    return Vehicle("rector", "Rector", kwh_per_mile, place, region)


def _commute(miles=400, month="2026-10", quality="estimate"):
    return CommuteMonth("rector", month, miles, quality)


def test_commute_home_charging_counts_here():
    ds = _dataset([], vehicles=[_vehicle("home")], commute=[_commute(400)])

    (result,) = commute_results(ds)

    assert isinstance(result, CommuteResult)
    assert result.vehicle_id == "rector"
    assert result.period_end_month == "2026-10"
    assert result.miles == 400
    assert result.ev_kwh == pytest.approx(120)
    assert result.tco2e_counted_here == pytest.approx(0.048)
    assert result.counted_in_campus is False
    assert result.avoided_tco2e == pytest.approx(400 * 0.4 / 1000 - 0.048)
    assert result.data_quality == "estimate"


def test_commute_church_charging_not_double_counted():
    ds = _dataset([], vehicles=[_vehicle("church")], commute=[_commute(400, quality="odometer")])

    (result,) = commute_results(ds)

    assert result.counted_in_campus is True
    assert result.tco2e_counted_here == 0
    assert result.ev_kwh == pytest.approx(120)
    assert result.avoided_tco2e == pytest.approx(400 * 0.4 / 1000)  # nothing subtracted: already in campus electricity
    assert result.data_quality == "odometer"


def test_commute_uses_the_grid_factor_for_the_charging_region_and_keeps_months_in_order():
    factors = FACTORS + [_factor("electricity_srmw", 0.7, "kg CO2e/kWh")]
    ds = _dataset(
        [],
        factors=factors,
        vehicles=[_vehicle("home", region="SRMW")],
        commute=[_commute(100, "2026-09"), _commute(200, "2026-10")],
    )

    sept, octo = commute_results(ds)

    assert (sept.period_end_month, octo.period_end_month) == ("2026-09", "2026-10")
    assert sept.tco2e_counted_here == pytest.approx(100 * 0.3 * 0.7 / 1000)  # SRMW 0.7, not RFCW 0.4
    assert octo.tco2e_counted_here == pytest.approx(200 * 0.3 * 0.7 / 1000)
    assert octo.avoided_tco2e == pytest.approx(200 * 0.4 / 1000 - octo.tco2e_counted_here)


def test_commute_home_charging_in_a_region_without_a_factor_raises_naming_the_region():
    # RFCW is present, so a silent fallback to it would "work"; it must not.
    ds = _dataset([], vehicles=[_vehicle("home", region="SRMW")], commute=[_commute()])

    with pytest.raises(ValueError, match="SRMW"):
        commute_results(ds)


def test_commute_for_an_unknown_vehicle_raises():
    ds = _dataset([], vehicles=[_vehicle()], commute=[CommuteMonth("ghost-car", "2026-10", 400, "estimate")])

    with pytest.raises(ValueError, match="ghost-car"):
        commute_results(ds)


def test_commute_with_an_unknown_charging_place_raises():
    ds = _dataset([], vehicles=[_vehicle("garage")], commute=[_commute()])

    with pytest.raises(ValueError, match="garage"):
        commute_results(ds)


def test_commute_empty_returns_empty_list():
    assert commute_results(_dataset([])) == []
    assert commute_results(Dataset()) == []


def test_commute_never_changes_the_campus_baseline():
    readings = _both("2025-01", 12)
    plain = select_baseline(_dataset(readings))
    with_commute = select_baseline(
        _dataset(readings, vehicles=[_vehicle("home")], commute=[_commute(400, m) for m in _months("2025-01", 12)])
    )

    assert with_commute == plain
