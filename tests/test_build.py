"""Tests for the build command (carbon.build), the guard it runs, and the spot-check helper (carbon.spotcheck).

`main` is called exactly as the command line would call it, into temporary folders, with the frozen sample
parish (`tests/fixtures/sample/`: its data and a `parish.toml` written for the test) or a copy of it that has
been changed to be wrong on purpose. The repository's own `data/` and `parish.toml` are built once, at the end,
only to prove that what is checked in today builds.
"""

import csv
import errno
import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import replace
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from bs4 import BeautifulSoup

import carbon.build as build
from carbon.build import MARKER_NAME, MARKER_TEXT, main
from carbon.feed import build_feed
from carbon.guard import scan_tree
from carbon.i18n import offset_terms_outside_allowed
from carbon.models import load_dataset
from carbon.spotcheck import main as spotcheck_main
from carbon.spotcheck import spotcheck_table
from carbon.validate import validate_dataset
from tests.support import (
    CONTACT,
    ORG_NAME,
    ROOT,
    SAMPLE_DIR,
    SAMPLE_VEHICLE,
    FILE_SETTINGS,
    TRAVEL_EN,
    TRAVEL_ES,
    sample_data_copy,
    sample_dataset,
    write_settings,
)

DATA_DIR = SAMPLE_DIR  # the frozen sample parish; never the repository's own data folder
GENERATED_AT = "2026-11-18"
# The sample's settings as a file, as the command reads them (sample_data = true: see FILE_SETTINGS).
SETTINGS_FILE = write_settings(Path(tempfile.mkdtemp(prefix="parish-settings-")))


def _settings_file(tmp_path, changes):
    """A parish.toml in its own folder under tmp_path: the sample's with `changes` ("section.key": value)."""
    folder = Path(tempfile.mkdtemp(prefix="settings-", dir=tmp_path))
    return write_settings(folder, changes)


def _run(out, *extra, data=DATA_DIR, generated_at=GENERATED_AT, settings=SETTINGS_FILE):
    args = ["--data", str(data), "--out", str(out), "--settings", str(settings)]
    if generated_at is not None:
        args += ["--generated-at", generated_at]
    return main(args + list(extra))


def _data_copy(tmp_path):
    return sample_data_copy(tmp_path)


def _edit(path, old, new):
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not found in {path.name}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _with_milestone_label(tmp_path, label_en, label_es="Hito de prueba", kind="other"):
    data = _data_copy(tmp_path)
    with (data / "milestones.csv").open("a", encoding="utf-8", newline="") as handle:
        handle.write(f"2027-03-09,{kind},{label_en},{label_es},day\n")
    return data


def _header_only(path):
    path.write_text(path.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")


def _with_travel(tmp_path, charging_place="home", solar=False):
    """The seed with its own vehicle taken out, plus a test vehicle (charging at `charging_place`) and twelve months of miles."""
    data = _data_copy(tmp_path)
    _header_only(data / "vehicles.csv")  # the sample holds its own vehicle and miles
    _header_only(data / "commute.csv")
    with (data / "vehicles.csv").open("a", encoding="utf-8", newline="") as handle:
        handle.write(f"test-ev,Test EV,0.35,{charging_place},RFCW\n")
    if solar:
        with (data / "milestones.csv").open("a", encoding="utf-8", newline="") as handle:
            handle.write("2027-03-09,solar_connected,Solar connected,Solar conectado,day\n")
    months = [f"2025-{m:02d}" for m in range(11, 13)] + [f"2026-{m:02d}" for m in range(1, 11)]
    with (data / "commute.csv").open("a", encoding="utf-8", newline="") as handle:
        for month in months:
            handle.write(f"test-ev,{month},1700,estimate,\n")
    return data


def _with_small_figures(tmp_path):
    """Twelve billing months of three-digit kWh and two-digit therms, all from bills."""
    data = _data_copy(tmp_path)
    columns = "meter_id,period_start,period_end,period_end_month,quantity,unit,peak_kw,generation_kwh,export_kwh,data_quality,uncertainty_pct,note".split(",")
    months = [f"2025-{m:02d}" for m in range(11, 13)] + [f"2026-{m:02d}" for m in range(1, 11)]
    with (data / "readings.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for i, month in enumerate(months):
            for meter, quantity, unit in (("electric-main", 900 + i, "kWh"), ("gas-main", 30 + i, "therm")):
                writer.writerow({"meter_id": meter, "period_end_month": month, "quantity": quantity, "unit": unit, "data_quality": "bill"})
    return data


def _files(root):
    """Every file under root, hidden ones included, as {relative posix path: sha256}."""
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture(scope="module")
def seed_build(tmp_path_factory):
    out = tmp_path_factory.mktemp("seed") / "dist"
    assert _run(out) == 0
    return out


@pytest.fixture(scope="module")
def travel_build(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("travel")
    out = tmp / "dist"
    assert _run(out, data=_with_travel(tmp)) == 0
    return out


@pytest.fixture(scope="module")
def church_solar_build(tmp_path_factory):
    """The other template branches: a vehicle that charges at the church, and the dated solar milestone."""
    tmp = tmp_path_factory.mktemp("church-solar")
    out = tmp / "dist"
    assert _run(out, data=_with_travel(tmp, charging_place="church", solar=True)) == 0
    return out


# --- what the build writes -----------------------------------------------------------------------------


def test_build_writes_the_site_the_feed_and_the_marker_and_no_server_config(seed_build):
    assert (seed_build / "index.html").is_file() and (seed_build / "es" / "index.html").is_file()
    for page in ("data", "methodology", "about"):
        assert (seed_build / page / "index.html").is_file()
        assert (seed_build / "es" / page / "index.html").is_file()
    assert (seed_build / "assets" / "site.css").is_file()
    assert (seed_build / "assets" / "fonts").is_dir()

    ds = sample_dataset(FILE_SETTINGS)
    for rel, text in build_feed(ds, GENERATED_AT, FILE_SETTINGS).items():
        assert rel.startswith("api/v1/")
        assert (seed_build / rel).read_bytes().decode("utf-8") == text, rel
    assert (seed_build / "api" / "v1" / "index.json").is_file()

    # GitHub Pages serves the feed to any site already; the build writes no web-server configuration.
    assert not (seed_build / ".htaccess").exists() and not (ROOT / "static" / "htaccess").exists()
    assert sorted(p.name for p in seed_build.iterdir() if p.name.startswith(".")) == [MARKER_NAME]
    marker = seed_build / MARKER_NAME
    assert marker.is_file() and marker.read_text(encoding="utf-8").strip()
    assert MARKER_NAME == ".carbon-build"


def test_the_feed_index_carries_the_generated_at_date(seed_build):
    index = json.loads((seed_build / "api" / "v1" / "index.json").read_text(encoding="utf-8"))
    assert index["generated_at"] == GENERATED_AT
    assert "Data last updated Nov 18, 2026" in (seed_build / "index.html").read_text(encoding="utf-8")


def test_generated_at_defaults_to_today(tmp_path):
    out = tmp_path / "dist"
    assert _run(out, generated_at=None) == 0
    index = json.loads((out / "api" / "v1" / "index.json").read_text(encoding="utf-8"))
    assert index["generated_at"] == date.today().isoformat()


def test_build_prints_a_summary_and_the_clean_guard_result(tmp_path, capsys):
    assert _run(tmp_path / "dist") == 0
    out = capsys.readouterr().out
    assert "Built" in out and "files" in out
    assert "Content guard: clean" in out


# --- validation and parity stop the build --------------------------------------------------------------


def test_build_returns_1_on_validation_error(tmp_path, capsys):
    data = _data_copy(tmp_path)
    _edit(data / "readings.csv", "3180,kWh", "3180,MWh")
    out = tmp_path / "dist"
    assert _run(out, data=data) == 1
    captured = capsys.readouterr()
    assert "UNIT_MISMATCH" in captured.out + captured.err
    assert not out.exists(), "a failed build must not leave a folder to deploy"


def test_warnings_are_printed_and_do_not_fail_the_build(tmp_path, capsys):
    data = _data_copy(tmp_path)
    _edit(data / "readings.csv", "2026-09,41,therm", "2026-09,0,therm")
    assert _run(tmp_path / "dist", data=data) == 0
    assert "ZERO_QUANTITY" in capsys.readouterr().out


def test_a_factor_without_spanish_text_warns_and_the_spanish_page_falls_back_to_the_english(tmp_path, capsys):
    data = _data_copy(tmp_path)
    _edit(data / "factors.csv", ',"eGRID2023 (datos de 2023, publicado el 2025-01-15)",', ",,")
    out = tmp_path / "dist"
    assert _run(out, data=data) == 0
    printed = capsys.readouterr().out
    assert "FACTOR_NO_SPANISH" in printed and "electricity_rfcw" in printed and "edition_es" in printed
    spanish = (out / "es" / "methodology" / "index.html").read_text(encoding="utf-8")
    assert "eGRID2023 (2023 data, released 2025-01-15)" in spanish  # the gap shows the English, not a blank cell
    assert "Revisión 2, publicada el 2025-06-12" in spanish  # the other Spanish text is still used
    assert "datos de 2023" not in spanish


def test_build_returns_1_when_the_language_tables_disagree(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(build, "parity_errors", lambda: ["key 'x' is in en but missing from es"])
    out = tmp_path / "dist"
    assert _run(out) == 1
    captured = capsys.readouterr()
    assert "key 'x' is in en but missing from es" in captured.out + captured.err
    assert not out.exists()


def test_build_returns_1_when_the_data_cannot_be_read(tmp_path, capsys):
    assert _run(tmp_path / "dist", data=tmp_path / "no-such-data") == 1
    assert capsys.readouterr().err.strip()

    data = _data_copy(tmp_path)
    _edit(data / "readings.csv", "3180,kWh", "lots,kWh")
    assert _run(tmp_path / "dist2", data=data) == 1
    assert not (tmp_path / "dist2").exists()


def test_build_returns_1_when_the_calculation_cannot_run(tmp_path, capsys):
    data = _data_copy(tmp_path)
    with (data / "commute.csv").open("a", encoding="utf-8", newline="") as handle:
        handle.write("no-such-vehicle,2026-10,100,estimate,\n")
    out = tmp_path / "dist"
    assert _run(out, data=data) == 1
    assert "no-such-vehicle" in capsys.readouterr().err
    assert not out.exists()


@pytest.mark.parametrize("bad", ["17 Nov 2026", "2026-13-01", "20261117", ""])
def test_build_rejects_a_bad_generated_at_with_code_2(tmp_path, bad, capsys):
    out = tmp_path / "dist"
    assert _run(out, generated_at=bad) == 2
    assert "generated-at" in capsys.readouterr().err
    assert not out.exists()


@pytest.mark.parametrize("bad", ["ftp://x.org", "carbon.example.org", "https://x.org/some path", "https://", "https://x.org?q=1", "/repo-name"])
def test_build_rejects_a_base_url_that_is_not_an_address_with_code_2(tmp_path, bad, capsys):
    out = tmp_path / "dist"
    assert _run(out, "--base-url", bad) == 2
    err = capsys.readouterr().err
    assert err.startswith("--base-url:") and "parish.toml" not in err
    assert not out.exists()


# --- the output folder is only ever cleared when a build owns it ---------------------------------------


def test_build_refuses_to_clear_unmarked_nonempty_directory(tmp_path, capsys):
    out = tmp_path / "precious"
    (out / "docs").mkdir(parents=True)
    (out / "docs" / "thesis.txt").write_text("keep me", encoding="utf-8")
    (out / "notes.md").write_text("keep me too", encoding="utf-8")
    before = _files(out)

    assert _run(out) == 2
    message = capsys.readouterr().err
    assert MARKER_NAME in message and "not empty" in message
    assert _files(out) == before


def test_build_refuses_when_out_is_a_file_or_the_data_folder_itself(tmp_path, capsys):
    target = tmp_path / "a-file"
    target.write_text("x", encoding="utf-8")
    assert _run(target) == 2
    assert target.read_text(encoding="utf-8") == "x"

    data = _data_copy(tmp_path)
    before = _files(data)
    assert _run(data, data=data) == 2
    assert _files(data) == before


def test_build_refuses_to_clear_a_folder_that_holds_the_data(tmp_path, capsys):
    out = tmp_path / "dist"
    assert _run(out) == 0  # a marked folder, so normally it may be cleared ...
    data = out / "inputs"
    shutil.copytree(DATA_DIR, data)
    before = _files(data)
    assert _run(out, data=data) == 2  # ... but not when the data lives inside it
    assert "data" in capsys.readouterr().err
    assert _files(data) == before


@pytest.mark.parametrize("state", ["missing", "empty"])
def test_build_writes_into_a_missing_or_empty_directory(tmp_path, state):
    out = tmp_path / "deep" / "dist"
    if state == "empty":
        out.mkdir(parents=True)
    assert _run(out) == 0
    assert (out / "index.html").is_file() and (out / MARKER_NAME).is_file()


def test_build_clears_what_a_previous_build_left_and_keeps_the_marker(tmp_path):
    out = tmp_path / "dist"
    assert _run(out) == 0
    (out / "stale.html").write_text("old", encoding="utf-8")
    (out / "old" / "deeper").mkdir(parents=True)
    (out / "old" / "deeper" / "page.html").write_text("old", encoding="utf-8")
    (out / "api" / "v1" / "retired.json").write_text("{}", encoding="utf-8")

    assert _run(out) == 0
    assert not (out / "stale.html").exists() and not (out / "old").exists()
    assert not (out / "api" / "v1" / "retired.json").exists()
    assert (out / MARKER_NAME).is_file() and (out / "index.html").is_file()


def test_a_build_that_fails_leaves_an_earlier_output_untouched(tmp_path):
    out = tmp_path / "dist"
    assert _run(out) == 0
    before = _files(out)

    bad = _with_milestone_label(tmp_path, "Offsets bought for the boiler")
    assert _run(out, data=bad) == 1
    assert _files(out) == before


def test_no_working_folder_is_left_behind(tmp_path):
    assert _run(tmp_path / "dist") == 0
    assert _run(tmp_path / "dist") == 0  # a rebuild swaps the folder, and the old one is gone
    bad = _with_milestone_label(tmp_path, "Offsets bought for the boiler")
    assert _run(tmp_path / "dist", data=bad) == 1
    assert _run(tmp_path / "dist2", data=bad) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["data", "dist"]


# --- fix round 1: a marker is only trusted when it is the one a build writes ---------------------------


def _marked(folder):
    """A folder a previous build could have written: it holds the real marker, and some other file."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / MARKER_NAME).write_bytes(MARKER_TEXT.encode("utf-8"))
    (folder / "index.html").write_text("a previous build", encoding="utf-8")
    return folder


def _precious(tmp_path):
    out = tmp_path / "precious"
    (out / "docs").mkdir(parents=True)
    (out / "docs" / "thesis.txt").write_text("keep me", encoding="utf-8")
    return out


def test_build_refuses_a_stray_empty_marker_beside_other_files(tmp_path, capsys):
    out = _precious(tmp_path)
    (out / MARKER_NAME).write_bytes(b"")
    before = _files(out)
    assert _run(out) == 2
    assert MARKER_NAME in capsys.readouterr().err
    assert _files(out) == before


@pytest.mark.parametrize(
    "content",
    [
        b"hello",
        MARKER_TEXT.encode("utf-8") + b"more\n",
        MARKER_TEXT.encode("utf-8").replace(b"\n", b"\r\n"),
        MARKER_TEXT.encode("utf-8").rstrip(b"\n"),
    ],
)
def test_build_refuses_a_marker_whose_content_is_not_exactly_the_one_a_build_writes(tmp_path, content):
    out = _precious(tmp_path)
    (out / MARKER_NAME).write_bytes(content)
    before = _files(out)
    assert _run(out) == 2
    assert _files(out) == before


def test_build_refuses_a_marker_that_is_a_symlink_even_to_the_right_content(tmp_path):
    out = _precious(tmp_path)
    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_bytes(MARKER_TEXT.encode("utf-8"))
    (out / MARKER_NAME).symlink_to(elsewhere)
    before = _files(out)
    assert _run(out) == 2
    assert _files(out) == before
    assert elsewhere.read_bytes() == MARKER_TEXT.encode("utf-8")


def test_build_refuses_a_marker_that_is_a_folder(tmp_path):
    out = _precious(tmp_path)
    (out / MARKER_NAME).mkdir()
    before = _files(out)
    assert _run(out) == 2
    assert _files(out) == before and (out / MARKER_NAME).is_dir()


def test_a_marker_the_build_wrote_is_still_accepted(tmp_path):
    out = tmp_path / "dist"
    assert _run(out) == 0
    assert (out / MARKER_NAME).read_bytes() == MARKER_TEXT.encode("utf-8")
    assert not (out / MARKER_NAME).is_symlink()
    assert _run(out) == 0


# --- fix round 1: no build input may be inside, or hold, the output folder -------------------------------

DIR_INPUTS = ("PACKAGE_DIR", "STATIC_DIR", "TEMPLATE_DIR", "SCHEMA_DIR")
FILE_INPUTS = ("CHANGELOG_PATH",)


def _point_input_at(monkeypatch, name, path):
    """Make the build treat `path` as that input. raising=False: the older build did not know these names."""
    monkeypatch.setattr(build, name, path, raising=False)


@pytest.mark.parametrize("name", DIR_INPUTS)
@pytest.mark.parametrize("where", ["equal", "inside", "ancestor"])
def test_build_refuses_an_output_folder_that_is_inside_holds_or_is_a_build_input_folder(tmp_path, monkeypatch, name, where):
    holder = _marked(tmp_path / "holder")
    inner = holder / "inner"
    inner.mkdir()
    (inner / "kept.txt").write_text("input", encoding="utf-8")
    _point_input_at(monkeypatch, name, inner)
    out = {"equal": inner, "inside": inner / "dist", "ancestor": holder}[where]
    if where == "equal":
        _marked(out)  # even a folder that looks like an earlier build, so only the input check can refuse it
    before = _files(holder)

    assert _run(out) == 2
    assert _files(holder) == before
    assert where != "inside" or not out.exists()


@pytest.mark.parametrize("name", FILE_INPUTS)
def test_build_refuses_a_marked_folder_that_holds_an_input_file(tmp_path, monkeypatch, name):
    holder = _marked(tmp_path / "holder")
    source = holder / "input-file"
    source.write_bytes((ROOT / "static" / "api-changelog.md").read_bytes())
    _point_input_at(monkeypatch, name, source)
    before = _files(holder)
    assert _run(holder) == 2
    assert _files(holder) == before


def test_build_refuses_an_output_folder_inside_the_data_folder(tmp_path):
    data = _data_copy(tmp_path)
    before = _files(data)
    assert _run(data / "dist", data=data) == 2
    assert _files(data) == before and not (data / "dist").exists()


@pytest.mark.parametrize(
    "relative",
    ["", "carbon", "carbon/dist", "static", "static/fonts/new", "static/api-changelog.md", "templates", "schema", "data", "data/dist", "parish.toml"],
)
def test_every_real_build_input_is_protected_from_the_output_folder(relative):
    out = ROOT / relative if relative else ROOT
    problem = build._out_problem(out, ROOT / "data", ROOT / "parish.toml")  # only looks; it deletes nothing
    assert problem and "build input" in problem, f"{out} was not recognised as overlapping a build input"


def test_a_marked_folder_that_holds_the_settings_file_is_not_replaced(tmp_path):
    holder = _marked(tmp_path / "holder")
    settings_file = write_settings(holder)
    before = _files(holder)
    assert _run(holder, settings=settings_file) == 2
    assert _files(holder) == before


@pytest.mark.parametrize("target", ["marked", "empty", "dangling"])
def test_build_refuses_an_output_folder_that_is_a_symlink(tmp_path, capsys, target):
    real = tmp_path / "real"
    if target == "marked":
        _marked(real)
    elif target == "empty":
        real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    before = _files(real) if real.exists() else None

    assert _run(link) == 2
    assert "symbolic link" in capsys.readouterr().err
    assert link.is_symlink()
    assert (_files(real) if real.exists() else None) == before


# --- fix round 2: the folder that is checked is the folder that is replaced ----------------------------------


def _symlink_then_dotdot_layout(tmp_path, monkeypatch):
    """cwd holds a symlink `lk` to real/x, plus a file; the tests build with --out paths that go through `lk/..`.

    Lexically `lk/..` is cwd; physically (what the operating system does) it is `real`, lk's target's parent.
    """
    cwd, real = tmp_path / "cwd", tmp_path / "real"
    (real / "x").mkdir(parents=True)
    cwd.mkdir()
    (cwd / "lk").symlink_to(real / "x")
    (cwd / "thesis.txt").write_text("keep me", encoding="utf-8")
    monkeypatch.chdir(cwd)
    return cwd, real


def _names(folder):
    return sorted(p.name for p in folder.iterdir())


def test_out_through_a_symlink_and_dotdot_is_built_where_it_physically_points_and_the_other_folder_is_untouched(
    tmp_path, monkeypatch
):
    cwd, real = _symlink_then_dotdot_layout(tmp_path, monkeypatch)
    (cwd / "dist").mkdir()  # the folder a lexical reading of lk/../dist names: not empty, no marker
    (cwd / "dist" / "keep.txt").write_text("keep me", encoding="utf-8")
    before = _files(cwd)

    assert _run("lk/../dist") == 0
    assert (real / "dist" / "index.html").is_file() and (real / "dist" / MARKER_NAME).is_file()
    assert _files(cwd) == before and (cwd / "lk").is_symlink()
    assert _names(tmp_path) == ["cwd", "real"], "a stage or old folder was left behind"
    assert _names(real) == ["dist", "x"] and _names(cwd) == ["dist", "lk", "thesis.txt"]


@pytest.mark.parametrize("marked", [True, False])
def test_out_dotdot_after_a_symlink_replaces_only_the_folder_it_physically_names(tmp_path, monkeypatch, marked):
    cwd, real = _symlink_then_dotdot_layout(tmp_path, monkeypatch)
    (real / "old.html").write_text("an earlier build", encoding="utf-8")
    if marked:
        (real / MARKER_NAME).write_bytes(MARKER_TEXT.encode("utf-8"))
    before_cwd, before_real = _files(cwd), _files(real)

    code = _run("lk/..")
    assert _files(cwd) == before_cwd and (cwd / "lk").is_symlink(), "the folder the path only seems to name was touched"
    if marked:
        assert code == 0
        assert (real / "index.html").is_file() and not (real / "old.html").exists()
    else:
        assert code == 2
        assert _files(real) == before_real
    assert _names(tmp_path) == ["cwd", "real"], "a stage or old folder was left behind"


# --- fix round 1: publishing is a swap, so a failure never leaves a half-written output --------------------


def _enospc():
    return OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))


def _fail_from(monkeypatch, owner, name, nth):
    """Make the nth call of owner.name, and every later one, fail with "no space left on device"."""
    original, calls = getattr(owner, name), {"n": 0}

    def wrapper(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] >= nth:
            raise _enospc()
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, name, wrapper)


def _leftovers(tmp_path):
    """Everything in tmp_path except the output folder and the data folders the tests make."""
    return sorted(p.name for p in tmp_path.iterdir() if p.name not in ("dist", "data"))


# One build makes exactly these calls that touch the disk through the two functions the tests can break. The
# first test below counts them in a real build, so if the build ever makes more or fewer the numbers here are
# stale and that test fails, rather than the injection test quietly skipping calls (or never firing).
COPYFILE_CALLS = 7  # shutil.copyfile: five font files (four fonts and their license) and the two images
WRITE_TEXT_CALLS = 19  # build._write_text: the eighteen feed files and the marker
FILE_CALLS = [("copyfile", n) for n in range(1, COPYFILE_CALLS + 1)] + [
    ("write_text", n) for n in range(1, WRITE_TEXT_CALLS + 1)
]


def _count_calls(monkeypatch, owner, name):
    original, calls = getattr(owner, name), {"n": 0}

    def wrapper(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, name, wrapper)
    return calls


def test_the_failure_injection_below_covers_every_file_call_a_build_makes(tmp_path, monkeypatch):
    copies = _count_calls(monkeypatch, shutil, "copyfile")
    writes = _count_calls(monkeypatch, build, "_write_text")
    assert _run(tmp_path / "dist") == 0
    assert (copies["n"], writes["n"]) == (COPYFILE_CALLS, WRITE_TEXT_CALLS)


@pytest.mark.parametrize("kind, nth", FILE_CALLS, ids=[f"{kind}-{nth}" for kind, nth in FILE_CALLS])
def test_a_disk_error_at_any_file_call_exits_1_and_never_leaves_a_partial_output(tmp_path, monkeypatch, capsys, kind, nth):
    # The first version of the build deleted the output and then copied file by file, so an error part-way left
    # a folder without index.html. Here the nth call fails, and so does every later one, wherever it falls.
    out = tmp_path / "dist"
    assert _run(out) == 0
    (out / "stale-from-the-old-build.html").write_text("old", encoding="utf-8")
    before = _files(out)
    capsys.readouterr()

    if kind == "copyfile":
        _fail_from(monkeypatch, shutil, "copyfile", nth)
    else:
        _fail_from(monkeypatch, build, "_write_text", nth)
    code = _run(out, generated_at="2026-12-01")

    assert code == 1, "the injected failure did not fire: the build makes fewer calls than this test assumes"
    err = capsys.readouterr().err
    assert "No space left on device" in err and "Traceback" not in err
    assert _files(out) == before
    assert _leftovers(tmp_path) == []


@pytest.mark.parametrize("failing_call", [1, 2])
def test_a_failure_at_the_swap_restores_the_previous_output(tmp_path, monkeypatch, capsys, failing_call):
    out = tmp_path / "dist"
    assert _run(out) == 0
    before = _files(out)
    capsys.readouterr()

    original, calls = os.rename, {"n": 0}

    def flaky(source, target):
        calls["n"] += 1
        if calls["n"] == failing_call:
            raise _enospc()
        return original(source, target)

    monkeypatch.setattr(build, "_rename", flaky, raising=False)
    assert _run(out, generated_at="2026-12-01") == 1
    err = capsys.readouterr().err
    assert "No space left on device" in err and "Traceback" not in err
    assert calls["n"] >= failing_call, "the build did not swap by rename"
    assert _files(out) == before
    assert _leftovers(tmp_path) == []


def test_a_failure_to_restore_the_previous_output_says_where_it_is_kept(tmp_path, monkeypatch, capsys):
    out = tmp_path / "dist"
    assert _run(out) == 0
    before = _files(out)
    capsys.readouterr()

    original, calls = os.rename, {"n": 0}

    def flaky(source, target):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise _enospc()
        return original(source, target)

    monkeypatch.setattr(build, "_rename", flaky, raising=False)
    assert _run(out, generated_at="2026-12-01") == 1
    err = capsys.readouterr().err
    kept = [p for p in tmp_path.iterdir() if p.name not in ("dist", "data")]
    assert len(kept) == 1 and kept[0].name in err
    assert _files(kept[0]) == before
    assert not out.exists()


def test_a_missing_changelog_source_exits_1_without_a_traceback_and_leaves_the_output_untouched(tmp_path, monkeypatch, capsys):
    out = tmp_path / "dist"
    assert _run(out) == 0
    before = _files(out)
    capsys.readouterr()

    monkeypatch.setattr("carbon.feed.CHANGELOG_PATH", tmp_path / "nowhere" / "api-changelog.md")
    assert _run(out) == 1
    err = capsys.readouterr().err
    assert "api-changelog.md" in err and "Traceback" not in err
    assert _files(out) == before
    assert _leftovers(tmp_path) == []


def test_a_stylesheet_that_cannot_be_written_exits_1_and_leaves_the_output_untouched(tmp_path, monkeypatch, capsys):
    # The stylesheet is written, not copied (the brand colours go in), so it has its own way to fail.
    out = tmp_path / "dist"
    assert _run(out) == 0
    before = _files(out)
    capsys.readouterr()

    def refuse(css, settings):
        raise _enospc()

    monkeypatch.setattr("carbon.site.brand_stylesheet", refuse)
    assert _run(out, generated_at="2026-12-01") == 1
    assert "Traceback" not in capsys.readouterr().err
    assert _files(out) == before
    assert _leftovers(tmp_path) == []


# --- the guard runs inside the build -------------------------------------------------------------------


@pytest.mark.parametrize(
    "label, code",
    [
        ("Offsets bought for the boiler", "OFFSET_TERM"),
        ("Account 1234 5678 90 opened", "LONG_DIGIT_RUN"),
        ("Paid $  9 for a plaque", "DOLLAR_AMOUNT"),
    ],
)
def test_the_guard_stops_a_bad_entry_and_reports_files_and_codes_but_never_the_text(tmp_path, capsys, label, code):
    data = _with_milestone_label(tmp_path, label)
    out = tmp_path / "dist"
    assert _run(out, data=data) == 1
    captured = capsys.readouterr()
    report = captured.out + captured.err
    assert code in report
    assert "about/index.html" in report and "api/v1/milestones.json" in report
    for fragment in ("Offsets", "1234", "5678", "$ ", "plaque", label):
        assert fragment not in report, f"the report echoes {fragment!r}"
    assert not out.exists(), "nothing may be published when the guard finds something"


def test_the_guard_also_catches_a_bad_spanish_label(tmp_path, capsys):
    data = _with_milestone_label(tmp_path, "Chapel painted", label_es="Se compensa la caldera")
    assert _run(tmp_path / "dist", data=data) == 1
    report = capsys.readouterr()
    assert "es/about/index.html" in report.out + report.err


def test_the_validator_is_not_the_only_defence(tmp_path):
    # These entries pass validate_dataset (it looks for 9 digits in a row and for the word solar), so only the
    # guard stands between them and the public site.
    data = _with_milestone_label(tmp_path, "Account 1234 5678 90 opened")
    assert [i for i in validate_dataset(load_dataset(data)) if i.level == "error"] == []
    assert _run(tmp_path / "dist", data=data) == 1


# --- reproducibility and forbidden content in the real output --------------------------------------------


def test_build_is_reproducible(tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    assert _run(first) == 0 and _run(second) == 0
    files = _files(first)
    assert files == _files(second)
    assert ".htaccess" not in files and MARKER_NAME in files and "api/v1/index.json" in files
    assert len(files) >= 30


def test_a_different_generated_at_changes_only_the_dated_files(tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    assert _run(first) == 0 and _run(second, generated_at="2026-12-01") == 0
    a, b = _files(first), _files(second)
    assert set(a) == set(b)
    changed = sorted(p for p in a if a[p] != b[p])
    assert "api/v1/index.json" in changed
    assert all(p.endswith(("index.json", ".html")) for p in changed), changed


# The feed's organization schema says, in one sentence, that real meter and account numbers are never published.
# That policy statement is the only place the phrase may appear; it is removed before the phrase check below.
POLICY_SENTENCES = {"api/v1/schema/organization.schema.json": "Real meter or account numbers are never published."}


def _visible_or_raw(path):
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".html":
        return BeautifulSoup(text, "html.parser").get_text(" ")
    return text


@pytest.mark.parametrize("which", ["seed", "travel", "church_solar"])
def test_built_output_has_no_forbidden_content(request, which):
    out = request.getfixturevalue(f"{which}_build")
    assert scan_tree(out) == {}

    checked = 0
    for path in sorted(out.rglob("*")):
        if path.suffix not in (".html", ".json", ".csv", ".md"):
            continue
        checked += 1
        text = _visible_or_raw(path)
        where = path.relative_to(out).as_posix()
        if where in POLICY_SENTENCES:
            assert POLICY_SENTENCES[where] in text, f"{where}: the policy sentence changed; update this test"
            text = text.replace(POLICY_SENTENCES[where], "")
        assert not re.search(r"\$\s?\d", text), f"{where}: dollar amount"
        assert not re.search(r"\d{9,}", text), f"{where}: nine-digit run"
        assert "account number" not in text.lower(), where
        assert "meter number" not in text.lower(), where
        assert offset_terms_outside_allowed(text) == [], f"{where}: offset wording"
    assert checked >= 20


def test_the_sample_build_publishes_the_travel_line_in_both_languages_and_the_feed(seed_build):
    """End to end: the sample's twelve travel months reach the Home card and the feed, and nothing else moves."""
    assert scan_tree(seed_build) == {}  # the content guard is clean on the built output, travel included
    for page, heading, avoided in (
        ("index.html", TRAVEL_EN, "avoided emissions compared with a gasoline car, not an offset"),
        ("es/index.html", TRAVEL_ES, "emisiones evitadas en comparación con un auto de gasolina, no una compensación"),
    ):
        soup = BeautifulSoup((seed_build / page).read_text(encoding="utf-8"), "html.parser")
        card = soup.find(id="travel")
        assert card is not None and card.find("h2").get_text(strip=True) == heading, page
        text = re.sub(r"\s+", " ", card.get_text(" ", strip=True))
        assert "~6,500" in text and avoided in text, page
        page_text = soup.get_text(" ")
        assert not re.search(r"\bcommut\w*|\$", page_text, re.IGNORECASE), page

    api = seed_build / "api" / "v1"
    travel = json.loads((api / "travel.json").read_text(encoding="utf-8"))
    assert [r["period_end_month"] for r in travel["rows"]] == [f"2025-{m:02d}" for m in (11, 12)] + [
        f"2026-{m:02d}" for m in range(1, 11)
    ]
    assert {r["vehicle_id"] for r in travel["rows"]} == {SAMPLE_VEHICLE}
    assert sum(r["miles"] for r in travel["rows"]) == 6500
    assert len((api / "travel.csv").read_text(encoding="utf-8").splitlines()) == 13  # header and twelve months
    annual = json.loads((api / "annual.json").read_text(encoding="utf-8"))
    baseline = annual["campus"]["baseline"]
    assert (baseline["electricity"]["quantity"], baseline["natural_gas"]["quantity"]) == (33430, 6029)
    assert baseline["tco2e_total"] == pytest.approx(baseline["electricity"]["tco2e"] + baseline["natural_gas"]["tco2e"], abs=1e-5)
    assert annual["staff_travel"]["vehicles"][0]["vehicle_id"] == SAMPLE_VEHICLE
    assert annual["staff_travel"]["vehicles"][0]["periods"][-1]["miles"] == 6500


def test_small_figures_in_neighbouring_table_cells_do_not_stop_the_build(tmp_path):
    out = tmp_path / "dist"
    assert _run(out, data=_with_small_figures(tmp_path)) == 0
    # Joined with spaces, the cells of one Home table row ("2025 950 40 0") read as a 10-digit number. The guard
    # judges one text node at a time, so a month with small figures builds.
    home = BeautifulSoup((out / "index.html").read_text(encoding="utf-8"), "html.parser")
    assert re.search(r"\d(?:[ -]?\d){8,}", home.get_text(" "))
    assert scan_tree(out) == {}


# --- every local link resolves -------------------------------------------------------------------------


def _local_target(out, page, value):
    """The file a root-relative or relative link points to, or None for links that are not local."""
    value = value.strip()
    if not value or value.startswith("#") or re.match(r"^(mailto:|tel:|https?:|//)", value, re.IGNORECASE):
        return None
    path = urlsplit(value).path
    base = out if path.startswith("/") else page.parent
    target = (base / path.lstrip("/")).resolve()
    assert out.resolve() in target.parents or target == out.resolve(), f"{page}: {value} leaves the site"
    return target / "index.html" if target.is_dir() else target


@pytest.mark.parametrize("which", ["seed", "travel", "church_solar"])
def test_every_local_link_resolves(request, which):
    out = request.getfixturevalue(f"{which}_build")
    pages = sorted(out.rglob("*.html"))
    assert len(pages) == 8

    seen = set()
    for page in pages:
        soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
        for tag in soup.find_all(True):
            for attribute in ("href", "src"):
                value = tag.get(attribute)
                if value is None:
                    continue
                target = _local_target(out, page, value)
                if target is None:
                    continue
                seen.add(urlsplit(value.strip()).path)
                assert target.is_file(), f"{page.relative_to(out)}: {attribute}={value!r} does not resolve"

    assert "/assets/site.css" in seen
    assert "/es/" in seen and "/data/" in seen
    api = {p for p in seen if p.startswith("/api/v1/")}
    assert "/api/v1/monthly.csv" in api and "/api/v1/annual.json" in api, sorted(api)
    if which != "seed":
        assert "/api/v1/travel.json" in api and "/api/v1/travel.csv" in api


def test_the_link_check_would_notice_a_broken_link(tmp_path):
    (tmp_path / "index.html").write_text('<a href="/missing/">x</a><a href="/">y</a>', encoding="utf-8")
    page = tmp_path / "index.html"
    assert _local_target(tmp_path, page, "/missing/") is not None
    assert not _local_target(tmp_path, page, "/missing/").is_file()
    assert _local_target(tmp_path, page, "/").is_file()
    assert _local_target(tmp_path, page, "https://example.org/x") is None
    assert _local_target(tmp_path, page, "mailto:a@b.org") is None
    assert _local_target(tmp_path, page, "#top") is None


# --- absolute links ------------------------------------------------------------------------------------


def _alternates(soup):
    return {a["hreflang"]: a["href"] for a in soup.find_all("link", rel="alternate")}


def test_with_no_base_url_anywhere_links_are_root_relative_and_there_is_no_canonical(seed_build, capsys):
    soup = BeautifulSoup((seed_build / "es" / "data" / "index.html").read_text(encoding="utf-8"), "html.parser")
    assert _alternates(soup) == {"en": "/data/", "es": "/es/data/", "x-default": "/data/"}
    assert soup.find_all("link", rel="canonical") == []


def test_the_base_url_flag_fills_in_when_parish_toml_leaves_it_blank(tmp_path):
    out = tmp_path / "dist"
    assert _run(out, "--base-url", "http://localhost:8766/") == 0
    soup = BeautifulSoup((out / "index.html").read_text(encoding="utf-8"), "html.parser")
    assert _alternates(soup)["es"] == "http://localhost:8766/es/"  # the trailing slash gives no double slash
    assert soup.find("link", rel="canonical")["href"] == "http://localhost:8766/"


def test_a_base_url_in_parish_toml_wins_over_the_flag(tmp_path):
    own = _settings_file(tmp_path, {"site.base_url": "https://carbon.example.org"})
    out = tmp_path / "dist"
    assert _run(out, "--base-url", "https://someone.github.io/parish-carbon", settings=own) == 0
    html = (out / "es" / "data" / "index.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    assert [c["href"] for c in soup.find_all("link", rel="canonical")] == ["https://carbon.example.org/es/data/"]
    assert "someone.github.io" not in html and "/parish-carbon/" not in html
    assert soup.find("link", rel="stylesheet")["href"] == "/assets/site.css"


def test_an_empty_base_url_flag_is_the_same_as_none(tmp_path):
    # The workflow passes whatever GitHub reports; before Pages is switched on that is nothing.
    one, two = tmp_path / "one", tmp_path / "two"
    assert _run(one, "--base-url", "") == 0 and _run(two) == 0
    assert _files(one) == _files(two)


# --- spot-check ----------------------------------------------------------------------------------------


def _cells(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def test_spotcheck_table_lists_requested_months():
    ds = sample_dataset()
    table = spotcheck_table(ds, ["2026-10", "2025-12", "2025-11"])
    lines = table.splitlines()
    header, divider, rows = _cells(lines[0]), lines[1], [_cells(line) for line in lines[2:]]

    assert header == ["Meter", "Month", "Transcribed value", "Quality", "Second reader's reading of the bill"]
    assert set(divider.replace("|", "").replace("-", "").replace(":", "").replace(" ", "")) == set()
    assert rows == [
        ["electric-main", "2026-10", "2,390 kWh", "bill", ""],
        ["gas-main", "2026-10", "176 therm", "bill", ""],
        ["electric-main", "2025-12", "2,540 kWh", "bill", ""],
        ["gas-main", "2025-12", "1,085 therm", "chart_measured", ""],
        ["electric-main", "2025-11", "2,210 kWh", "chart_estimate", ""],
        ["gas-main", "2025-11", "612 therm", "bill", ""],
    ]


def test_spotcheck_table_says_when_a_meter_has_no_reading_for_a_month():
    ds = sample_dataset()
    ds = replace(ds, readings=[r for r in ds.readings if (r.meter_id, r.period_end_month) != ("gas-main", "2025-11")])
    rows = [_cells(line) for line in spotcheck_table(ds, ["2025-11", "2027-01"]).splitlines()[2:]]
    assert rows == [
        ["electric-main", "2025-11", "2,210 kWh", "chart_estimate", ""],
        ["gas-main", "2025-11", "no reading entered", "", ""],
        ["electric-main", "2027-01", "no reading entered", "", ""],
        ["gas-main", "2027-01", "no reading entered", "", ""],
    ]


@pytest.mark.parametrize("bad", ["2026-13", "2026-1", "Oct 2026", "2026-10-25", ""])
def test_spotcheck_table_rejects_a_month_that_is_not_yyyy_mm(bad):
    with pytest.raises(ValueError, match="YYYY-MM"):
        spotcheck_table(sample_dataset(), [bad])


def test_spotcheck_command_prints_the_table(capsys):
    assert spotcheck_main(["--data", str(DATA_DIR), "--settings", str(SETTINGS_FILE), "2026-10", "2026-08", "2025-11"]) == 0
    out = capsys.readouterr().out
    assert out.count("| electric-main |") == 3 and out.count("| gas-main |") == 3
    assert "| 2025-11 | 2,210 kWh | chart_estimate |" in out


def test_spotcheck_command_reports_bad_input(tmp_path, capsys):
    common = ["--settings", str(SETTINGS_FILE)]
    assert spotcheck_main(["--data", str(DATA_DIR), *common, "October"]) == 2
    assert "YYYY-MM" in capsys.readouterr().err
    assert spotcheck_main(["--data", str(tmp_path / "missing"), *common, "2026-10"]) == 1
    assert capsys.readouterr().err.strip()
    bad = _settings_file(tmp_path, {"organization.zip": "627"})
    assert spotcheck_main(["--data", str(DATA_DIR), "--settings", str(bad), "2026-10"]) == 1
    assert "zip" in capsys.readouterr().err


def test_spotcheck_works_for_a_parish_with_travel_switched_off_and_no_travel_files(tmp_path, capsys):
    data = _data_copy(tmp_path)
    (data / "vehicles.csv").unlink()
    (data / "commute.csv").unlink()
    off = _settings_file(tmp_path, {"travel.enabled": False})
    assert spotcheck_main(["--data", str(data), "--settings", str(off), "2026-10"]) == 0
    assert "| gas-main | 2026-10 | 176 therm | bill |" in capsys.readouterr().out


# =================================================================================================
# The settings file end to end: what a parish changes in parish.toml, built by the real command.
# =================================================================================================


def _html_pages(out):
    return sorted(p for p in out.rglob("*.html"))


def _soup_of(path):
    return BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")


# --- problems in parish.toml stop the build with plain sentences ----------------------------------------


def test_a_problem_in_parish_toml_stops_the_build_names_the_key_and_leaves_the_output_alone(tmp_path, capsys):
    out = tmp_path / "dist"
    assert _run(out) == 0
    before = _files(out)
    capsys.readouterr()

    bad = _settings_file(tmp_path, {"organization.zip": "6270", "site.languages": ["es"], "grid.egrid_subregion": ""})
    assert _run(out, settings=bad) == 1
    err = capsys.readouterr().err
    assert "[organization] zip" in err and "[site] languages" in err and "[grid] egrid_subregion" in err
    assert err.count("ERROR   SETTINGS: parish.toml:") == 3, "every problem is reported at once"
    assert "3 problem(s) in parish.toml" in err and "Traceback" not in err
    assert _files(out) == before


def test_a_missing_settings_file_is_said_plainly(tmp_path, capsys):
    assert _run(tmp_path / "dist", settings=tmp_path / "parish.toml") == 1
    err = capsys.readouterr().err
    assert "parish.toml was not found" in err and "Traceback" not in err
    assert not (tmp_path / "dist").exists()


def test_brand_colours_with_too_little_contrast_stop_the_build(tmp_path, capsys):
    pale = _settings_file(tmp_path, {"brand.primary_color": "#9AD0B0"})
    assert _run(tmp_path / "dist", settings=pale) == 1
    err = capsys.readouterr().err
    assert "[brand] primary_color #9AD0B0" in err and "too light" in err and "4.5 to 1" in err
    assert not (tmp_path / "dist").exists()

    fine = _settings_file(tmp_path, {"brand.primary_color": "#7A1F3D", "brand.accent_color": "#FFE9A8"})
    out = tmp_path / "dist"
    assert _run(out, settings=fine) == 0
    css = (out / "assets" / "site.css").read_text(encoding="utf-8")
    assert "--primary: #7A1F3D;" in css and "--accent: #FFE9A8;" in css
    assert 'content="#7A1F3D"' in (out / "index.html").read_text(encoding="utf-8")


def test_an_unknown_subregion_stops_the_build_and_lists_the_ones_on_file(tmp_path, capsys):
    data = _data_copy(tmp_path)
    with (data / "factors.csv").open("a", encoding="utf-8", newline="") as handle:
        handle.write("electricity_zzzt,0.9,kg CO2e/kWh,test,test only: a made-up subregion,test,test,https://example.org/,2026-01-01,prueba,prueba\n")
    wrong = _settings_file(tmp_path, {"grid.egrid_subregion": "NOPE"})
    assert _run(tmp_path / "dist", data=data, settings=wrong) == 1
    err = capsys.readouterr().err
    assert "UNKNOWN_SUBREGION" in err and '"NOPE"' in err and "RFCW, ZZZT" in err
    assert not (tmp_path / "dist").exists()


# --- a different subregion, chosen from a factors file with many rows ---------------------------------------


def test_a_different_subregion_is_used_and_published_and_the_other_rows_are_not(tmp_path):
    # The extra rows are made up, live only in this test's copy of the data, and are plainly not EPA figures.
    data = _data_copy(tmp_path)
    with (data / "factors.csv").open("a", encoding="utf-8", newline="") as handle:
        handle.write("electricity_zzzt,0.9,kg CO2e/kWh,test,test only: a made-up subregion,Made-up edition,Made-up revision,https://example.org/zzzt,2026-01-01,Edición inventada,Revisión inventada\n")
        handle.write("electricity_aaat,0.1,kg CO2e/kWh,test,test only: a made-up subregion,Other edition,Other revision,https://example.org/aaat,2026-01-01,Otra edición,Otra revisión\n")
    _edit(data / "vehicles.csv", ",home,RFCW,", ",home,ZZZT,")
    out = tmp_path / "dist"
    assert _run(out, data=data, settings=_settings_file(tmp_path, {"grid.egrid_subregion": "zzzt"})) == 0

    api = out / "api" / "v1"
    factors = {f["factor_id"]: f["value"] for f in json.loads((api / "factors.json").read_text(encoding="utf-8"))["factors"]}
    assert factors.keys() == {"electricity_zzzt", "natural_gas", "gasoline_car_per_mile"} and factors["electricity_zzzt"] == 0.9
    monthly = json.loads((api / "monthly.json").read_text(encoding="utf-8"))["rows"]
    electric = [r for r in monthly if r["fuel"] == "electricity"]
    assert len(electric) == 14 and {r["factor_id"] for r in electric} == {"electricity_zzzt"}
    assert sum(r["tco2e"] for r in electric) == pytest.approx(sum(r["quantity"] for r in electric) * 0.9 / 1000, abs=1e-4)
    assert json.loads((api / "organization.json").read_text(encoding="utf-8"))["egrid_subregion"] == "ZZZT"

    for page in _html_pages(out):
        text = _soup_of(page).get_text(" ")
        assert "RFCW" not in text and "AAAT" not in text and "eGRID2023" not in text, page
    methodology = _soup_of(out / "methodology" / "index.html")
    rows = [re.sub(r"\s+", " ", r.get_text(" ", strip=True)) for r in methodology.select("table.factors tbody tr")]
    assert len(rows) == 3 and any("regional grid ZZZT" in r and "0.9 kg CO2e/kWh" in r and "Made-up edition" in r for r in rows)
    assert "Edición inventada" in (out / "es" / "methodology" / "index.html").read_text(encoding="utf-8")
    for path in sorted(out.rglob("*")):
        if path.is_file() and path.suffix in (".html", ".json", ".csv"):
            assert "aaat" not in path.read_text(encoding="utf-8").lower(), path


# --- English only -----------------------------------------------------------------------------------------


def test_an_english_only_build_has_no_spanish_pages_links_or_requirements(tmp_path, capsys):
    data = _data_copy(tmp_path)
    (data / "milestones.csv").write_text("date,kind,label_en,precision\n2019-01-01,boiler,Boiler replaced (2019),year\n", encoding="utf-8")
    # The Spanish columns of the factors are emptied too: an English-only site asks for none of it.
    rows = list(csv.DictReader((data / "factors.csv").open(encoding="utf-8", newline="")))
    with (data / "factors.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[k for k in rows[0] if not k.endswith("_es")], lineterminator="\n")
        writer.writeheader()
        writer.writerows([{k: v for k, v in row.items() if not k.endswith("_es")} for row in rows])
    english = _settings_file(tmp_path, {"site.languages": ["en"], "organization.campus_es": None, "travel.label_es": None})
    out = tmp_path / "dist"
    assert _run(out, "--base-url", "https://someone.github.io/parish-carbon", data=data, settings=english) == 0
    printed = capsys.readouterr()
    assert "FACTOR_NO_SPANISH" not in printed.out + printed.err

    assert not (out / "es").exists()
    assert [p.relative_to(out).as_posix() for p in _html_pages(out)] == ["about/index.html", "data/index.html", "index.html", "methodology/index.html"]
    for page in _html_pages(out):
        html = page.read_text(encoding="utf-8")
        soup = _soup_of(page)
        assert soup.html["lang"] == "en"
        assert soup.find(class_="lang") is None and not soup.find_all(attrs={"hreflang": True}), page
        assert "/es/" not in html and "Español" not in html, page
        assert len(soup.find_all("link", rel="canonical")) == 1
    index = json.loads((out / "api" / "v1" / "index.json").read_text(encoding="utf-8"))
    assert index["languages"] == ["en"]
    milestones = json.loads((out / "api" / "v1" / "milestones.json").read_text(encoding="utf-8"))["milestones"]
    assert milestones[0]["label_en"] == "Boiler replaced (2019)" and milestones[0]["label_es"] == ""

    # The same data cannot be built in Spanish: the label column is missing, and the message says which.
    assert _run(tmp_path / "dist2", data=data) == 1
    assert "label_es" in capsys.readouterr().err


# --- travel switched off ----------------------------------------------------------------------------------


@pytest.mark.parametrize("files", ["missing", "header_only", "left_as_they_are"])
def test_with_travel_switched_off_nothing_about_travel_is_built_whatever_the_travel_files_hold(tmp_path, files):
    data = _data_copy(tmp_path)
    for name in ("vehicles.csv", "commute.csv"):
        if files == "missing":
            (data / name).unlink()
        elif files == "header_only":
            _header_only(data / name)
    out = tmp_path / "dist"
    assert _run(out, data=data, settings=_settings_file(tmp_path, {"travel.enabled": False})) == 0

    api = out / "api" / "v1"
    names = sorted(p.relative_to(api).as_posix() for p in api.rglob("*") if p.is_file())
    assert not [name for name in names if "travel" in name]
    index = json.loads((api / "index.json").read_text(encoding="utf-8"))
    assert not [key for key in index["files"] if "travel" in key]
    annual = json.loads((api / "annual.json").read_text(encoding="utf-8"))
    assert "staff_travel" not in annual and annual["campus"]["baseline"]["label"] == "2025-11 to 2026-10"
    assert "gasoline_car_per_mile" not in (api / "factors.json").read_text(encoding="utf-8")
    assert "/travel" not in (api / "openapi.json").read_text(encoding="utf-8")

    words = re.compile(r"travel|viaje|vehicle|vehículo|gasoline|gasolina|scope 3|alcance 3|avoided|evitad|staff-ev", re.IGNORECASE)
    for page in _html_pages(out):
        soup = _soup_of(page)
        assert soup.find(id="travel") is None, page
        found = words.search(soup.get_text(" "))
        assert not found, f"{page}: {found.group(0)!r}"
        assert not [a["href"] for a in soup.find_all("a", href=True) if "travel" in a["href"]]


def test_with_travel_on_a_missing_travel_file_is_an_error_that_names_it(tmp_path, capsys):
    data = _data_copy(tmp_path)
    (data / "commute.csv").unlink()
    assert _run(tmp_path / "dist", data=data) == 1
    assert "commute.csv" in capsys.readouterr().err


def test_travel_switched_off_gives_the_same_campus_figures_as_travel_on(tmp_path, seed_build):
    out = tmp_path / "dist"
    assert _run(out, settings=_settings_file(tmp_path, {"travel.enabled": False})) == 0
    for name in ("monthly.json", "monthly.csv", "milestones.json"):
        assert (out / "api" / "v1" / name).read_bytes() == (seed_build / "api" / "v1" / name).read_bytes(), name
    with_travel = json.loads((seed_build / "api" / "v1" / "annual.json").read_text(encoding="utf-8"))
    without = json.loads((out / "api" / "v1" / "annual.json").read_text(encoding="utf-8"))
    assert with_travel["campus"] == without["campus"]


# --- the base path: a GitHub Pages project site under /repo-name/ -----------------------------------------

BASE = "https://someone.github.io/parish-carbon"
BASE_PREFIX = "/parish-carbon"
_ATTRIBUTES = ("href", "src", "poster", "data", "action")
_CSS_URL = re.compile(r"url\(\s*[\"']?([^)\"'\s]+)")


@pytest.fixture(scope="module")
def prefixed_build(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("prefixed")
    out = tmp / "dist"
    assert _run(out, "--base-url", BASE) == 0
    return out


def _addresses(out):
    """Every address in every built page, stylesheet and inline style: (file, where it was found, value)."""
    for page in _html_pages(out):
        soup = _soup_of(page)
        rel = page.relative_to(out).as_posix()
        for node in soup.find_all(True):
            for attr in _ATTRIBUTES:
                if isinstance(node.get(attr), str):
                    yield rel, f"<{node.name} {attr}>", node[attr]
            for part in (node.get("srcset") or "").split(","):
                if part.strip():
                    yield rel, f"<{node.name} srcset>", part.split()[0]
            for ref in _CSS_URL.findall(node.get("style") or ""):
                yield rel, f"<{node.name} style>", ref
            for attr in ("fill", "stroke", "clip-path", "mask", "filter"):
                for ref in _CSS_URL.findall(node.get(attr) or ""):
                    yield rel, f"<{node.name} {attr}>", ref
    for sheet in sorted(out.rglob("*.css")):
        for ref in _CSS_URL.findall(sheet.read_text(encoding="utf-8")):
            yield sheet.relative_to(out).as_posix(), "url()", ref


def test_under_a_base_path_no_href_src_or_url_escapes_the_prefix(prefixed_build):
    internal = 0
    for where, what, value in _addresses(prefixed_build):
        label = f"{where}: {what} = {value!r}"
        assert not value.startswith("//"), label
        if value.startswith(("mailto:", "#")):
            continue
        if what == "url()" or " style>" in what:
            assert not value.startswith(("/", "http")), label  # relative to the stylesheet, so it follows it anywhere
            continue
        if value.startswith(("http://", "https://")):
            if value.startswith("https://someone.github.io"):
                assert value.startswith(BASE + "/"), label
                internal += 1
            else:
                assert what == "<a href>", label  # the license and the factor sources are the only outside links
            continue
        assert value.startswith(BASE_PREFIX + "/"), label
        internal += 1
    assert internal > 100


def test_under_a_base_path_every_internal_address_is_a_file_in_the_build(prefixed_build):
    checked = 0
    for where, what, value in _addresses(prefixed_build):
        if value.startswith(BASE + "/"):
            value = value[len("https://someone.github.io") :]
        if what == "url()":
            if value.startswith("#"):
                continue
            target = (prefixed_build / where).parent / value
        elif value.startswith(BASE_PREFIX + "/"):
            target = prefixed_build / value[len(BASE_PREFIX) + 1 :]
            if value.endswith("/"):
                target = target / "index.html"
        else:
            continue
        assert target.is_file(), f"{where}: {what} = {value!r} points at nothing"
        checked += 1
    assert checked > 100


def test_under_a_base_path_the_feed_addresses_itself_relatively_and_names_the_prefix_only_in_openapi(prefixed_build):
    api = prefixed_build / "api" / "v1"
    index = json.loads((api / "index.json").read_text(encoding="utf-8"))
    for entry in index["files"].values():
        assert (api / entry["path"]).is_file()
        if "schema" in entry:
            assert (api / entry["schema"]).is_file()
    assert json.loads((api / "openapi.json").read_text(encoding="utf-8"))["servers"][0]["url"] == BASE_PREFIX + "/api/v1"
    for path in sorted(api.rglob("*.json")):
        if path.name != "openapi.json":
            assert "parish-carbon" not in path.read_text(encoding="utf-8"), path


def test_a_base_path_build_differs_from_a_root_build_only_in_its_addresses(prefixed_build, seed_build):
    a, b = _files(prefixed_build), _files(seed_build)
    assert set(a) == set(b)
    changed = sorted(p for p in a if a[p] != b[p])
    assert all(p.endswith(".html") or p == "api/v1/openapi.json" for p in changed), changed
    assert len([p for p in changed if p.endswith(".html")]) == 8
    for page in _html_pages(prefixed_build):
        there = _soup_of(page).get_text(" ").split()
        here = _soup_of(seed_build / page.relative_to(prefixed_build)).get_text(" ").split()
        assert there == here, page


def test_a_base_url_at_a_domain_root_keeps_root_relative_links_and_absolute_canonicals(tmp_path):
    out = tmp_path / "dist"
    assert _run(out, "--base-url", "https://carbon.example.org") == 0
    soup = _soup_of(out / "es" / "data" / "index.html")
    assert soup.find("link", rel="stylesheet")["href"] == "/assets/site.css"
    assert soup.find("link", rel="canonical")["href"] == "https://carbon.example.org/es/data/"
    assert _alternates(soup)["en"] == "https://carbon.example.org/data/"


def test_the_build_summary_line_says_where_the_links_point(tmp_path, capsys):
    assert _run(tmp_path / "one", "--base-url", BASE) == 0
    assert f"links on {BASE}." in capsys.readouterr().out
    assert _run(tmp_path / "two") == 0
    assert "root-relative links" in capsys.readouterr().out


# --- forbidden words from parish.toml ----------------------------------------------------------------------


def test_a_forbidden_word_from_the_settings_stops_the_build_without_echoing_it(tmp_path, capsys):
    data = _with_milestone_label(tmp_path, "New roof on the Rectory", label_es="Techo nuevo en la casa parroquial")
    listed = _settings_file(tmp_path, {"privacy.forbidden_words": ["rectory", "casa parroquial"]})
    out = tmp_path / "dist"
    assert _run(out, data=data, settings=listed) == 1
    captured = capsys.readouterr()
    report = captured.out + captured.err
    assert "FORBIDDEN_WORD" in report
    for file in ("about/index.html", "es/about/index.html", "api/v1/milestones.json"):
        assert file in report
    assert "rectory" not in report.lower() and "parroquial" not in report.lower() and "roof" not in report
    assert not out.exists()

    # The same label builds for a parish that has not listed the word: nothing is forbidden by default.
    assert _run(out, data=data) == 0
    assert "New roof on the Rectory" in (out / "about" / "index.html").read_text(encoding="utf-8")


def test_a_forbidden_word_in_the_parishs_own_settings_text_is_caught_too(tmp_path, capsys):
    own = _settings_file(tmp_path, {"privacy.forbidden_words": ["vicarage"], "organization.campus_en": "our church and vicarage"})
    assert _run(tmp_path / "dist", settings=own) == 1
    report = capsys.readouterr()
    assert "FORBIDDEN_WORD" in report.err and "index.html" in report.err and "vicarage" not in report.err


def test_a_word_inside_a_longer_word_does_not_stop_the_build(tmp_path):
    data = _with_milestone_label(tmp_path, "Parish directory printed")
    listed = _settings_file(tmp_path, {"privacy.forbidden_words": ["rectory"]})
    assert _run(tmp_path / "dist", data=data, settings=listed) == 0


# --- the sample-data banner and flag ------------------------------------------------------------------------


def test_the_sample_flag_puts_the_banner_on_every_page_and_in_the_feed(seed_build, capsys):
    pages = _html_pages(seed_build)
    assert len(pages) == 8
    for page in pages:
        banner = _soup_of(page).find_all(class_="sample")
        assert len(banner) == 1, page
        text = banner[0].get_text(" ", strip=True)
        assert text.startswith("Sample data.") or text.startswith("Datos de ejemplo."), page
    api = seed_build / "api" / "v1"
    for name in ("index.json", "organization.json", "annual.json"):
        assert json.loads((api / name).read_text(encoding="utf-8"))["sample_data"] is True, name


def test_with_sample_data_false_and_the_parishs_own_name_there_is_no_banner_and_the_feed_says_false(tmp_path, capsys):
    real = _settings_file(tmp_path, {"sample_data": False, "organization.name": "Grace Chapel", "organization.city": "Rivertown", "organization.state": "OH"})
    out = tmp_path / "dist"
    assert _run(out, settings=real) == 0
    printed = capsys.readouterr().out
    assert "sample_data is true" not in printed and printed.strip().endswith("Content guard: clean.")
    for page in _html_pages(out):
        soup = _soup_of(page)
        assert soup.find(class_="sample") is None, page
        text = soup.get_text(" ")
        assert "Sample data" not in text and "Datos de ejemplo" not in text and ORG_NAME not in text
        assert "Grace Chapel" in text and "Rivertown, OH" in text
    api = out / "api" / "v1"
    index = json.loads((api / "index.json").read_text(encoding="utf-8"))
    assert index["sample_data"] is False
    assert index["organization"] == {"slug": "grace-chapel-rivertown-oh", "name": "Grace Chapel", "city": "Rivertown", "state": "OH", "zip": "62701"}


def test_sample_data_false_with_the_sample_name_still_in_place_is_refused(tmp_path, capsys):
    assert _run(tmp_path / "dist", settings=_settings_file(tmp_path, {"sample_data": False})) == 1
    err = capsys.readouterr().err
    assert "sample_data is false" in err and ORG_NAME in err
    assert not (tmp_path / "dist").exists()


def test_the_build_says_when_the_banner_is_on_and_still_ends_with_the_clean_guard_line(tmp_path, capsys):
    assert _run(tmp_path / "dist") == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[-1] == "Content guard: clean."
    assert any("sample_data is true" in line for line in lines)


# --- the Markdown summary the publishing workflow shows --------------------------------------------------


def test_the_summary_file_reports_a_good_build_with_its_warnings(tmp_path):
    data = _data_copy(tmp_path)
    _edit(data / "readings.csv", "2026-09,41,therm", "2026-09,0,therm")
    summary = tmp_path / "summary.md"
    summary.write_text("earlier step\n", encoding="utf-8")
    assert _run(tmp_path / "dist", "--summary", str(summary), data=data) == 0
    text = summary.read_text(encoding="utf-8")
    assert text.startswith("earlier step\n"), "the summary is appended to, as GitHub expects"
    # Zero therms is one warning, and being far below September of the year before is another.
    assert "### The data check passed and the site was built (2 warning(s) to look at)" in text
    assert "Warning `ZERO_QUANTITY`: gas-main 2026-09" in text and "Warning `PLAUSIBILITY`: gas-main 2026-09" in text
    assert "`sample_data = true`" in text


def test_the_summary_file_explains_a_data_error_in_plain_words_and_says_nothing_was_published(tmp_path):
    data = _data_copy(tmp_path)
    _edit(data / "readings.csv", "3180,kWh", "3180,MWh")
    summary = tmp_path / "summary.md"
    assert _run(tmp_path / "dist", "--summary", str(summary), data=data) == 1
    text = summary.read_text(encoding="utf-8")
    assert "### The data needs fixing (1 error(s))" in text
    assert "**Error `UNIT_MISMATCH`**: electric-main 2025-09" in text
    assert "Nothing was published" in text and "docs/monthly-update.md" in text


def test_the_summary_file_lists_every_settings_problem(tmp_path):
    bad = _settings_file(tmp_path, {"organization.zip": "6270", "organization.contact_email": "office"})
    summary = tmp_path / "summary.md"
    assert _run(tmp_path / "dist", "--summary", str(summary), settings=bad) == 1
    text = summary.read_text(encoding="utf-8")
    assert "### The settings file needs fixing (2 problem(s))" in text
    assert "**parish.toml**: [organization] zip" in text and "**parish.toml**: [organization] contact_email" in text


def test_the_summary_file_reports_a_guard_finding_by_file_and_code_only(tmp_path):
    data = _with_milestone_label(tmp_path, "Offsets bought for the boiler")
    summary = tmp_path / "summary.md"
    assert _run(tmp_path / "dist", "--summary", str(summary), data=data) == 1
    text = summary.read_text(encoding="utf-8")
    assert "### The content guard stopped the build" in text and "`OFFSET_TERM`" in text and "`about/index.html`" in text
    assert "Offsets" not in text and "boiler" not in text.lower()


def test_a_summary_file_that_cannot_be_written_does_not_fail_a_good_build(tmp_path, capsys):
    assert _run(tmp_path / "dist", "--summary", str(tmp_path / "no" / "such" / "folder" / "summary.md")) == 0
    assert "could not write the summary" in capsys.readouterr().err


# --- the repository as it stands -----------------------------------------------------------------------------


def test_the_repositorys_own_settings_and_data_build_clean_twice_to_the_same_bytes(tmp_path, capsys):
    """Whatever parish.toml and data/ hold today (the template's sample, or a parish's own), they build."""
    one, two = tmp_path / "one", tmp_path / "two"
    for out in (one, two):
        args = ["--data", str(ROOT / "data"), "--settings", str(ROOT / "parish.toml"), "--out", str(out), "--generated-at", GENERATED_AT]
        assert main(args) == 0, capsys.readouterr().err
        assert capsys.readouterr().out.strip().endswith("Content guard: clean.")
    assert _files(one) == _files(two)
    assert (one / "index.html").is_file() and (one / "api" / "v1" / "index.json").is_file()


def test_the_template_ships_with_the_sample_parish_flagged_as_sample_data():
    """While parish.toml still names the sample parish, it must say sample_data = true (the loader enforces it)."""
    from carbon.settings import SAMPLE_NAME, load_settings

    shipped = load_settings(ROOT / "parish.toml")
    assert shipped.sample_data or shipped.name != SAMPLE_NAME
    assert SAMPLE_NAME == ORG_NAME and CONTACT.endswith("@example.org")
