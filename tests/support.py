"""Shared pieces for the tests: the frozen sample parish, its settings, and ways to change either.

The tests never read the repository's own `data/` folder or `parish.toml` for their expectations: a parish
replaces both with its own, and its publishing workflow runs this suite on every change. Everything a test
expects comes from `tests/fixtures/sample/` (a frozen copy of the template's fictional sample parish, with a
copy of the three factor rows the template ships) or from data the test builds itself. Only the handful of
tests that check the repository as it stands today (it builds, it validates, its factors are complete) look
at `data/` and `parish.toml`, and those hold for any valid parish.
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

from carbon.models import Dataset, load_dataset
from carbon.settings import Settings, load_settings

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = ROOT / "tests" / "fixtures" / "sample"
SAMPLE_SETTINGS_PATH = SAMPLE_DIR / "parish.toml"

# The sample parish as most tests want it: its real settings, without the sample-data banner. Tests about the
# banner switch it back on.
SETTINGS: Settings = replace(load_settings(SAMPLE_SETTINGS_PATH), sample_data=False)

# The same parish as `write_settings` writes it with no changes: the sample-data banner is on.
FILE_SETTINGS: Settings = replace(SETTINGS, sample_data=True)

# What the sample is called, for assertions.
ORG_NAME = "St. Example Church"
ORG_SLUG = "st-example-church-springfield-il"
CONTACT = "office@example.org"
TRAVEL_EN = "Clergy and staff church travel"
TRAVEL_ES = "Viajes del clero y del personal por la iglesia"
SAMPLE_VEHICLE = "staff-ev"
SAMPLE_MILES_PER_DAY = 25


def settings(**changes) -> Settings:
    """The sample settings with some fields changed."""
    return replace(SETTINGS, **changes)


def sample_dataset(which: Settings = SETTINGS) -> Dataset:
    return load_dataset(SAMPLE_DIR, which)


def sample_data_copy(tmp_path: Path, name: str = "data") -> Path:
    """A writable copy of the sample data folder (the CSV files only)."""
    target = tmp_path / name
    target.mkdir(parents=True)
    for source in sorted(SAMPLE_DIR.glob("*.csv")):
        shutil.copyfile(source, target / source.name)
    return target


def toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_settings(folder: Path, changes: dict | None = None, *, name: str = "parish.toml") -> Path:
    """Write a `parish.toml` into `folder`: the sample's, with `changes` applied.

    `changes` maps "section.key" (or "sample_data") to a new value; None removes the key. The file says
    `sample_data = true`, as the sample's own does: with the sample's name, `false` is refused. `FILE_SETTINGS`
    is what the file loads as.
    """
    values: dict[str, dict[str, object]] = {
        "": {"sample_data": True},
        "organization": {
            "name": ORG_NAME,
            "slug": "",
            "city": "Springfield",
            "state": "IL",
            "zip": "62701",
            "contact_email": CONTACT,
            "website": "",
            "campus_en": "our church campus",
            "campus_es": "el campus de nuestra iglesia",
        },
        "site": {"base_url": "", "languages": ["en", "es"]},
        "brand": {"primary_color": "#1F6B4A", "accent_color": "#F2C14E", "logo": ""},
        "grid": {"egrid_subregion": "RFCW"},
        "baseline": {"calendar_year": 2025},
        "travel": {"enabled": True, "label_en": TRAVEL_EN, "label_es": TRAVEL_ES},
        "privacy": {"forbidden_words": []},
    }
    for dotted, value in (changes or {}).items():
        section, _, key = dotted.rpartition(".")
        if value is None:
            values.get(section, {}).pop(key, None)
        else:
            values.setdefault(section, {})[key] = value
    lines = [f"{key} = {toml_value(value)}" for key, value in values.pop("").items()]
    for section, keys in values.items():
        lines.append(f"\n[{section}]")
        lines.extend(f"{key} = {toml_value(value)}" for key, value in keys.items())
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
