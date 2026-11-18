"""Tests on the emission factors.

Two files are looked at. `data/factors.csv` is the repository's real factors file: it holds a row for natural
gas, one for the gasoline-car comparison and one `electricity_<subregion>` row for each eGRID subregion that has
been added, and the checks on it must hold however many subregions it holds. The sample fixture's copy
(`tests/fixtures/sample/factors.csv`) is the three rows the template first shipped with, and pins the wording
the site tests rely on.
"""

import re
from dataclasses import replace
from datetime import date

import pytest

from carbon.calc import GASOLINE_FACTOR_ID, GRID_FACTOR_PREFIX, NATURAL_GAS_FACTOR_ID
from carbon.models import load_dataset
from carbon.validate import validate_dataset
from tests.support import ROOT, SAMPLE_DIR, settings

DATA_DIR = ROOT / "data"

# factor_id -> (unit, lowest plausible value, highest plausible value).
# The ranges only catch unit slips (lb for kg, MWh for kWh, mmBtu for therm, km for mile);
# the exact values come from EPA and are recorded in data/factors.csv.
REQUIRED = {
    NATURAL_GAS_FACTOR_ID: ("kg CO2e/therm", 5.2, 5.5),
    # 0.25 still rejects the usual slips (per km would be about 0.19, lb per mile about 0.66).
    GASOLINE_FACTOR_ID: ("kg CO2e/mile", 0.25, 0.5),
}
# Every electricity_<subregion> row: kilograms per kWh. A rate typed in lb/MWh would be in the hundreds, one in
# lb/kWh about twice too high for most grids and one in tonnes/MWh identical, so the range is only a net for the
# first slip; the source text in each row is what a reviewer checks the value against.
ELECTRICITY_UNIT = "kg CO2e/kWh"
ELECTRICITY_RANGE = (0.01, 2.0)


def _repo_factors():
    return load_dataset(DATA_DIR, settings(travel_enabled=False)).factors


def _check_provenance(factor, url_prefix="https://www.epa.gov"):
    where = factor.factor_id
    assert factor.value > 0, where
    assert factor.gases, f"{where}: gases must name the gases and the global warming potentials"
    assert factor.source, f"{where}: source (with the unit conversion) must not be empty"
    assert factor.edition, f"{where}: edition must not be empty"
    assert factor.revision, f"{where}: revision must not be empty"
    assert factor.url.startswith(url_prefix) and "epa.gov" in factor.url, f"{where}: url must be an EPA page"
    assert factor.retrieved, f"{where}: retrieved must not be empty"
    date.fromisoformat(factor.retrieved)


def test_the_two_national_factors_are_present_with_provenance():
    factors = {f.factor_id: f for f in _repo_factors()}
    for factor_id, (unit, low, high) in REQUIRED.items():
        assert factor_id in factors, f"{factor_id} is missing from factors.csv"
        factor = factors[factor_id]
        assert factor.unit == unit
        assert low <= factor.value <= high, f"{factor_id} = {factor.value} is outside {low} to {high}"
        _check_provenance(factor)


def test_every_electricity_row_is_a_subregion_factor_in_kg_per_kwh_with_provenance():
    grid = [f for f in _repo_factors() if f.factor_id.startswith(GRID_FACTOR_PREFIX)]
    assert grid, "factors.csv needs at least one electricity_<subregion> row"
    low, high = ELECTRICITY_RANGE
    for factor in grid:
        code = factor.factor_id[len(GRID_FACTOR_PREFIX):]
        assert re.fullmatch(r"[a-z0-9]{2,8}", code), f"{factor.factor_id}: the subregion code is written in lower case"
        assert factor.unit == ELECTRICITY_UNIT, factor.factor_id
        assert low <= factor.value <= high, f"{factor.factor_id} = {factor.value} is outside {low} to {high}"
        _check_provenance(factor, url_prefix="https://")


def test_the_factors_file_lists_no_factor_id_twice():
    ids = [f.factor_id for f in _repo_factors()]
    assert sorted(ids) == sorted(set(ids)), "a factor_id is listed twice"


def test_a_spanish_edition_is_never_just_the_english_text_copied():
    """A row may leave the Spanish columns empty (the Spanish page then shows the English, and the build warns
    a parish that uses that row), but a filled one must really be Spanish."""
    for factor in _repo_factors():
        if factor.edition_es.strip():
            assert factor.edition_es != factor.edition, f"{factor.factor_id}: edition_es is just the English text"
        if factor.revision_es.strip():
            assert factor.revision_es != factor.revision, f"{factor.factor_id}: revision_es is just the English text"


def test_the_repositorys_factors_validate_for_every_subregion_they_hold():
    for factor in _repo_factors():
        if not factor.factor_id.startswith(GRID_FACTOR_PREFIX):
            continue
        code = factor.factor_id[len(GRID_FACTOR_PREFIX):].upper()
        chosen = settings(egrid_subregion=code, travel_enabled=False)
        ds = replace(load_dataset(SAMPLE_DIR, chosen), factors=_repo_factors())
        errors = [i for i in validate_dataset(ds, chosen) if i.level == "error"]
        assert errors == [], code


# --- the sample fixture's three rows ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def sample_factors():
    return {f.factor_id: f for f in load_dataset(SAMPLE_DIR).factors}


def test_the_sample_factors_are_the_three_the_template_shipped_with(sample_factors):
    assert set(sample_factors) == {"electricity_rfcw", *REQUIRED}
    assert sample_factors["electricity_rfcw"].unit == ELECTRICITY_UNIT
    for factor in sample_factors.values():
        _check_provenance(factor)


def test_every_sample_factor_has_its_own_spanish_edition_and_revision(sample_factors):
    for where, factor in sample_factors.items():
        assert factor.edition_es.strip(), f"{where}: edition_es is empty, so /es/methodology/ would show English"
        assert factor.revision_es.strip(), f"{where}: revision_es is empty, so /es/methodology/ would show English"
        assert factor.edition_es != factor.edition, f"{where}: edition_es is just the English text"
        assert factor.revision_es != factor.revision, f"{where}: revision_es is just the English text"


def test_the_spanish_text_keeps_the_proper_names_dates_and_revision_numbers_of_the_english(sample_factors):
    egrid = sample_factors["electricity_rfcw"]
    assert "eGRID2023" in egrid.edition_es
    for date_text in ("2025-01-15",):
        assert date_text in egrid.edition_es
    for date_text in ("2025-06-12", "2025-01-17"):
        assert date_text in egrid.revision_es
    for number in ("2", "1"):
        assert re.search(rf"\b{number}\b", egrid.revision_es), f"revision number {number} is missing"
    for factor_id in REQUIRED:
        factor = sample_factors[factor_id]
        assert "GHG Emission Factors Hub" in factor.edition_es and "2025" in factor.edition_es
        assert "enero de 2025" in factor.edition_es
        # Dates only (the day, the year and the ISO date); the surrounding Spanish wording is not pinned.
        assert "15" in factor.revision_es and "2025" in factor.revision_es and "2026-01-12" in factor.revision_es
    # No English sentence fragments are left behind.
    for factor in sample_factors.values():
        for text in (factor.edition_es, factor.revision_es):
            for english in ("released", "Revision", "Last Modified", "January", "listed", "page last", "data,", "printed"):
                assert english not in text, f"{factor.factor_id}: {text!r} still has {english!r}"


# The 27 subregions of EPA eGRID2023 (Table 1 of the summary tables). A parish anywhere in the US, Alaska,
# Hawaii or Puerto Rico must find its own code in data/factors.csv.
EGRID_SUBREGIONS = (
    "AKGD AKMS AZNM CAMX ERCT FRCC HIMS HIOA MROE MROW NEWE NWPP NYCW NYLI NYUP PRMS RFCE RFCM RFCW RMPA "
    "SPNO SPSO SRMV SRMW SRSO SRTV SRVC"
).split()


def test_the_factors_file_holds_every_egrid_subregion():
    ids = {f.factor_id for f in _repo_factors()}
    missing = [s for s in EGRID_SUBREGIONS if f"{GRID_FACTOR_PREFIX}{s.lower()}" not in ids]
    assert not missing, f"no electricity row for {missing}"
    assert len(EGRID_SUBREGIONS) == 27


def test_every_electricity_value_is_the_conversion_of_the_rate_printed_in_its_own_source():
    """Each row quotes EPA's CO2e rate in lb/MWh; the stored kg/kWh value must be that rate times 0.45359237 / 1000."""
    from decimal import ROUND_HALF_UP, Decimal

    rows = [f for f in _repo_factors() if f.factor_id.startswith(GRID_FACTOR_PREFIX)]
    assert len(rows) >= 27
    for f in rows:
        sub = f.factor_id[len(GRID_FACTOR_PREFIX):].upper()
        m = re.search(rf"{sub} \([^)]+\): CO2e ([\d.]+) lb/MWh as printed", f.source)
        assert m, f"{f.factor_id}: the source text does not quote the {sub} rate in lb/MWh"
        expected = (Decimal(m.group(1)) * Decimal("0.45359237") / 1000).quantize(Decimal("0.0000001"), rounding=ROUND_HALF_UP)
        assert Decimal(str(f.value)).quantize(Decimal("0.0000001")) == expected, f"{f.factor_id}: {f.value} is not {expected}"
