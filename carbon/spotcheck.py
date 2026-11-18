"""The spot-check sheet: a second reader compares transcribed figures against the original bills.

    .venv/bin/python -m carbon.spotcheck 2026-10 2026-08 2026-06

prints a Markdown table with one row per meter per requested month: the value in `readings.csv`, its data quality,
and an empty column for what the reader sees on the bill. A meter with no reading for a month gets a row that says
so, because a missing month is something the reader should see. Bill PDFs themselves never enter the repository.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

from carbon.models import Dataset, load_dataset
from carbon.settings import SettingsError, load_settings

COLUMNS = ("Meter", "Month", "Transcribed value", "Quality", "Second reader's reading of the bill")
NO_READING = "no reading entered"

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _quantity(value: float) -> str:
    """1,961 and 25.22: thousands separators as on a bill, decimals only when there are some."""
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _meter_ids(ds: Dataset) -> list[str]:
    """The meters in meters.csv order, then any meter id that only the readings mention."""
    listed = [m.meter_id for m in ds.meters]
    return listed + sorted({r.meter_id for r in ds.readings} - set(listed))


def spotcheck_table(ds: Dataset, months: list[str]) -> str:
    """A Markdown table of meter, month, transcribed value, quality and a blank column for the reader.

    Rows follow the order of `months`, then the order of the meters. Raises ValueError for a month that is
    not YYYY-MM.
    """
    for month in months:
        if not _MONTH_RE.match(month):
            raise ValueError(f"months must be written YYYY-MM, got {month!r}")

    lines = [
        "| " + " | ".join(COLUMNS) + " |",
        "|" + "|".join("---" for _ in COLUMNS) + "|",
    ]
    for month in months:
        for meter_id in _meter_ids(ds):
            matches = [r for r in ds.readings if r.meter_id == meter_id and r.period_end_month == month]
            if not matches:
                lines.append(f"| {meter_id} | {month} | {NO_READING} |  |  |")
            for reading in matches:
                value = f"{_quantity(reading.quantity)} {reading.unit}"
                lines.append(f"| {meter_id} | {month} | {value} | {reading.data_quality} |  |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m carbon.spotcheck",
        description="Print a Markdown table of transcribed readings for a second reader to check against the bills.",
    )
    parser.add_argument("months", nargs="+", metavar="YYYY-MM", help="billing months (the month the period ends)")
    parser.add_argument("--data", default="data", help="folder with the CSV data files (default: data)")
    parser.add_argument("--settings", default="parish.toml", help="the parish settings file (default: parish.toml)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2

    try:
        settings = load_settings(Path(args.settings))
    except SettingsError as exc:
        for line in exc.problems:
            print(f"{Path(args.settings).name}: {line}", file=sys.stderr)
        return 1
    try:
        ds = load_dataset(Path(args.data), settings)
    except (OSError, ValueError, csv.Error) as exc:
        print(f"Cannot read the data in {args.data}: {exc}", file=sys.stderr)
        return 1
    try:
        table = spotcheck_table(ds, args.months)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(table, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
