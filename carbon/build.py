"""The build command: turns `parish.toml` and the data folder into the whole publishable folder.

    .venv/bin/python -m carbon.build --out dist --generated-at 2026-11-18

The output folder holds the site pages (English at `/`, Spanish at `/es/` when the parish builds in Spanish) with
their assets, the feed under `api/v1/` and a marker file, `.carbon-build`.

Order of work, and why a bad entry cannot be published
-------------------------------------------------------
1. The arguments and the output folder are checked first (exit code 2).
2. `parish.toml` is read and checked (`carbon.settings.load_settings`). Every problem is printed as a plain
   sentence naming the key; any problem stops the build (exit code 1).
3. The data is loaded and `carbon.validate.validate_dataset` runs. Every issue is printed; any error stops the
   build (exit code 1). Warnings are printed and do not stop it. The string tables must be in parity too.
4. Everything is built into a staging folder beside `--out` (same filesystem), never into `--out` itself.
5. `carbon.guard.scan_tree` scans the staged result for forbidden content (dollars, account-like digit runs,
   offset wording and the parish's own `[privacy] forbidden_words`). Any finding stops the build (exit code 1)
   and prints the files and finding codes, never the text.
6. Only now is the finished folder swapped in by rename: the old `--out` is renamed aside, the staged folder is
   renamed to `--out`, and the old one is deleted. If a step fails, the previous output is put back, the staged
   folder is deleted, and the build exits 1 with a one-line message. A build that fails at any step leaves `--out`
   as it was; a half-written folder is never the one that gets published. Any `OSError` (a full disk, a missing
   template) is reported the same way, never as a traceback.

What the person entering data sees
----------------------------------
On GitHub the only feedback is the workflow run. Everything above is printed to the log, and with
`--summary FILE` the same messages are also written as Markdown to that file; the workflow passes
`$GITHUB_STEP_SUMMARY`, so they appear on the run's front page: what is wrong, in which file, and that the
published site was not changed, or that the build is fine and how many warnings are worth a look.

The base URL
------------
`[site] base_url` in `parish.toml` decides where links point. `--base-url` is used only when that key is blank:
the workflow passes the address GitHub Pages reports for the repository, so a parish need not know it. With
neither, links are root-relative, which suits a local preview (`python -m http.server` in the output folder).

When `--out` may be replaced
----------------------------
`--out` is replaced only when it is missing, empty, or holds the marker `.carbon-build` that an earlier build wrote:
a regular file (not a symlink, not a folder) whose content is exactly `MARKER_TEXT`. The marker has fixed content,
so two builds with the same `--generated-at` give identical files. Anything else that is not empty is refused
(exit code 2), so `--out ~/Documents` never loses a file. `--out` is resolved once, the way the operating system
reads it (a `lk/..` after a symlink `lk` means the real parent), and that one path is checked, reported and swapped.
A `--out` that is itself a symlink is refused. So is any
`--out` that equals, holds, or lies inside a build input (the data folder, `static/`, `templates/`, `schema/`, the
`carbon` package or the changelog), even if it carries a marker.

Exit codes: 0 built; 1 the settings, the data, the language tables, the calculation, the content guard or the disk
said no; 2 the arguments or the output folder were unacceptable.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import secrets
import shutil
import sys
from datetime import date
from pathlib import Path

from carbon.feed import CHANGELOG_PATH, SCHEMA_DIR, build_feed
from carbon.guard import scan_tree
from carbon.i18n import parity_errors
from carbon.models import load_dataset
from carbon.settings import Settings, SettingsError, load_settings, normalize_base_url
from carbon.site import REPO_ROOT, STATIC_DIR, TEMPLATE_DIR, render_site
from carbon.validate import validate_dataset

MARKER_NAME = ".carbon-build"
MARKER_TEXT = (
    "This folder is written by `python -m carbon.build` and is emptied and rebuilt on every build.\n"
    "Do not keep other files here.\n"
)
PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_SETTINGS_PATH = REPO_ROOT / "parish.toml"

EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m carbon.build",
        description="Validate the data and build the site and the feed into one folder.",
    )
    parser.add_argument("--data", default="data", help="folder with the CSV data files (default: data)")
    parser.add_argument("--out", default="dist", help="folder to build into (default: dist)")
    parser.add_argument(
        "--generated-at",
        default=None,
        metavar="YYYY-MM-DD",
        help="the 'last updated' date written into the pages and the feed (default: today; pass it for a reproducible build)",
    )
    parser.add_argument(
        "--settings",
        default=str(DEFAULT_SETTINGS_PATH),
        help="the parish settings file (default: parish.toml in the repository's top folder)",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="where the site will be served, such as https://someone.github.io/repo-name; used only when "
        "parish.toml leaves [site] base_url blank (default: none, which gives root-relative links)",
    )
    parser.add_argument(
        "--summary",
        default=None,
        metavar="FILE",
        help="also append the result and every message to this file as Markdown (on GitHub: $GITHUB_STEP_SUMMARY)",
    )
    return parser


def _generated_at_problem(value: str) -> str | None:
    if _DATE_RE.fullmatch(value):
        try:
            date.fromisoformat(value)
            return None
        except ValueError:
            pass
    return f"--generated-at must be a date written YYYY-MM-DD, got {value!r}"


def _build_inputs(data: Path, settings_path: Path | None = None) -> list[tuple[str, Path]]:
    """Everything the build reads, with a name for messages. Looked up at call time."""
    settings_input = [("the settings file", settings_path)] if settings_path is not None else []
    return settings_input + [
        ("the data folder", data),
        ("the static folder", STATIC_DIR),
        ("the templates folder", TEMPLATE_DIR),
        ("the schema folder", SCHEMA_DIR),
        ("the carbon package", PACKAGE_DIR),
        ("the feed changelog", CHANGELOG_PATH),
    ]


def _overlaps(a: Path, b: Path) -> bool:
    """True when the two paths are the same, or one lies inside the other."""
    a, b = a.resolve(), b.resolve()
    return a == b or a in b.parents or b in a.parents


def _has_marker(out: Path) -> bool:
    """True when `out` holds the marker a build writes: a regular file with exactly the marker content."""
    marker = out / MARKER_NAME
    try:
        return marker.is_file() and not marker.is_symlink() and marker.read_bytes() == MARKER_TEXT.encode("utf-8")
    except OSError:
        return False


def _symlink_problem(requested: Path) -> str | None:
    """Why the path as given may not be used because it is itself a symlink, or None. Looked at before resolving."""
    if requested.is_symlink():
        return (
            f"--out {requested} is a symbolic link. Links are not supported, so that a build can never clear "
            "something else by accident; give the real folder."
        )
    return None


def _out_problem(out: Path, data: Path, settings_path: Path | None = None) -> str | None:
    """Why `out` (the resolved output path) may not be built into, or None.

    Only a missing, empty or previously built folder is allowed, and never one that overlaps a build input.
    """
    for label, path in _build_inputs(data, settings_path):
        if _overlaps(out, path):
            return (
                f"--out {out} overlaps a build input, {label} ({path}); building there would delete files the "
                "build reads. Choose another --out."
            )
    if not out.exists():
        return None
    if not out.is_dir():
        return f"--out {out} exists and is not a folder"
    if not any(out.iterdir()) or _has_marker(out):
        return None
    return (
        f"--out {out} is not empty and has no valid {MARKER_NAME} file (a previous build writes one with fixed "
        "content), so it will not be replaced. Choose another --out, or empty that folder yourself."
    )


def _describe(error: OSError) -> str:
    """One line for an I/O error: what the system said, and the file it was about if it named one."""
    text = error.strerror or str(error)
    return f"{text}: {error.filename}" if error.filename else text


def _write_text(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:  # exact bytes: no newline translation
        handle.write(text)


def _build_into(ds, root: Path, generated_at: str, settings: Settings) -> None:
    render_site(ds, root, generated_at, settings)
    for relative, text in build_feed(ds, generated_at, settings).items():
        _write_text(root, relative, text)
    _write_text(root, MARKER_NAME, MARKER_TEXT)


def _rename(source: Path, target: Path) -> None:
    os.rename(source, target)


def _sibling(out: Path, kind: str) -> Path:
    """A hidden, unique folder name next to `out` (same filesystem, so a rename between them is one step)."""
    return out.parent / f".{out.name}.{kind}-{secrets.token_hex(4)}"


class _OutputLost(Exception):
    """The swap failed and the previous output could not be put back."""


def _swap_in(staging: Path, out: Path) -> None:
    """Make the finished `staging` folder the new `out`: rename the old one aside, rename the new one in, delete the old.

    If any step fails the previous output is put back and the OSError is raised; `_OutputLost` is raised only
    when even that fails, and says where the previous output is kept.
    """
    backup = None
    if out.exists():
        backup = _sibling(out, "old")
        _rename(out, backup)  # if this fails nothing has changed
    try:
        _rename(staging, out)
    except OSError:
        if backup is not None:
            try:
                _rename(backup, out)
            except OSError as restore_error:
                raise _OutputLost(
                    f"the previous output could not be put back ({_describe(restore_error)}); it is kept in {backup}"
                ) from restore_error
        raise
    if backup is not None:
        shutil.rmtree(backup, ignore_errors=True)
        if backup.exists():
            _err(f"Note: could not delete the previous output, now in {backup}; delete it yourself.")


def _summary(root: Path) -> tuple[int, int]:
    """(files, bytes) under `root`."""
    files = [p for p in root.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


class _Report:
    """Collects what the build has to say, for the Markdown summary a person reads on GitHub."""

    def __init__(self, path: str | None):
        self.path = path
        self.lines: list[str] = []

    def add(self, line: str) -> None:
        self.lines.append(line)

    def write(self, heading: str, closing: str = "") -> None:
        """Append the summary to the file, if one was asked for. A summary that cannot be written is not an error."""
        if not self.path:
            return
        text = f"### {heading}\n\n"
        if self.lines:
            text += "\n".join(f"- {line}" for line in self.lines) + "\n\n"
        if closing:
            text += closing + "\n\n"
        try:
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(text)
        except OSError as exc:
            _err(f"Note: could not write the summary file: {_describe(exc)}")


NOT_PUBLISHED = (
    "Nothing was published: the site keeps showing the last good version. Fix the lines above in the named "
    "file and commit again. `docs/monthly-update.md` explains each code."
)


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:  # argparse exits on --help and on unknown options; hand the code back instead
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    report = _Report(args.summary)
    try:
        return _run(args, report)
    except _OutputLost as exc:
        _err(f"Build failed: {exc}.")
        report.add(f"The build could not finish: {exc}.")
        report.write("The site was not built")
        return EXIT_FAILED
    except OSError as exc:  # a full disk, a missing template, a folder that cannot be written
        _err(f"Build failed: {_describe(exc)}. The output folder was not changed.")
        report.add(f"The build could not finish: {_describe(exc)}.")
        report.write("The site was not built")
        return EXIT_FAILED


def _run(args: argparse.Namespace, report: _Report) -> int:
    generated_at = args.generated_at if args.generated_at is not None else date.today().isoformat()
    # The single source of truth for the output folder: the path as given is looked at once, for a symlink, and
    # then resolved once, the way the operating system reads it (symlinks followed, `..` applied to the real
    # parent). Every check, message, the staging folder and the swap use `target`, never `args.out` again, so
    # the folder that is validated is always the folder that is replaced.
    requested = Path(args.out)
    problem = _symlink_problem(requested)
    target = requested.resolve()
    problem = problem or _generated_at_problem(generated_at) or _out_problem(target, Path(args.data), Path(args.settings))
    if problem:
        _err(problem)
        return EXIT_USAGE

    if args.base_url:
        try:
            normalize_base_url(args.base_url)
        except ValueError as exc:
            _err(f"--base-url: {exc}")
            return EXIT_USAGE

    settings_name = Path(args.settings).name
    try:
        settings = load_settings(Path(args.settings), fallback_base_url=args.base_url or None)
    except SettingsError as exc:
        for line in exc.problems:
            _err(f"ERROR   SETTINGS: {settings_name}: {line}")
            report.add(f"**{settings_name}**: {line}")
        _err(f"Build failed: {len(exc.problems)} problem(s) in {settings_name}. Fix that file; {target} was not changed.")
        report.write(f"The settings file needs fixing ({len(exc.problems)} problem(s))", NOT_PUBLISHED)
        return EXIT_FAILED

    try:
        ds = load_dataset(Path(args.data), settings)
    except (OSError, ValueError, csv.Error) as exc:
        _err(f"Cannot read the data in {args.data}: {exc}")
        report.add(f"**The data files could not be read**: {exc}")
        report.write("The data needs fixing", NOT_PUBLISHED)
        return EXIT_FAILED

    issues = validate_dataset(ds, settings)
    for issue in issues:
        print(f"{issue.level.upper():7} {issue.code}: {issue.message}", file=sys.stderr if issue.level == "error" else sys.stdout)
    errors = [issue for issue in issues if issue.level == "error"]
    warnings = [issue for issue in issues if issue.level == "warning"]
    if errors:
        for issue in errors:
            report.add(f"**Error `{issue.code}`**: {issue.message}")
        for issue in warnings:
            report.add(f"Warning `{issue.code}`: {issue.message}")
        _err(f"Build failed: {len(errors)} validation error(s). Fix the data; {target} was not changed.")
        report.write(f"The data needs fixing ({len(errors)} error(s))", NOT_PUBLISHED)
        return EXIT_FAILED
    for issue in warnings:
        report.add(f"Warning `{issue.code}`: {issue.message}")

    mismatches = parity_errors()
    if mismatches:
        for line in mismatches:
            _err(f"ERROR   LANGUAGE_PARITY: {line}")
            report.add(f"**Error `LANGUAGE_PARITY`**: {line}")
        _err(f"Build failed: the English and Spanish strings disagree. {target} was not changed.")
        report.write("The site was not built", NOT_PUBLISHED)
        return EXIT_FAILED

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = _sibling(target, "stage")
    staging.mkdir()
    try:
        try:
            _build_into(ds, staging, generated_at, settings)
        except ValueError as exc:
            _err(f"Build failed: {exc} {target} was not changed.")
            report.add(f"**The site could not be built**: {exc}")
            report.write("The site was not built", NOT_PUBLISHED)
            return EXIT_FAILED

        findings = scan_tree(staging, settings.forbidden_words)
        if findings:
            _err("Content guard: forbidden content found. Files and finding codes (the text is not shown):")
            for path, codes in findings.items():
                _err(f"  {path}: {', '.join(codes)}")
                report.add(f"**Content guard**: `{path}` contains {', '.join(f'`{code}`' for code in codes)}")
            _err(
                "The codes are explained in docs/monthly-update.md. Free text in the data files (such as milestone "
                f"labels) and in parish.toml is the usual source. Build failed; {target} was not changed."
            )
            report.write(
                "The content guard stopped the build",
                "Something that must not be published (a dollar amount, a long number that looks like an account, "
                "an offset claim or one of your forbidden words) is in the text of a data file or of `parish.toml`. "
                "The guard never shows the text itself. " + NOT_PUBLISHED,
            )
            return EXIT_FAILED

        _swap_in(staging, target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    count, size = _summary(target)
    where = settings.base_url or "root-relative links (no base URL)"
    print(f"Built {count} files ({size / 1024:.0f} KB) into {target}, dated {generated_at}, links on {where}.")
    if settings.sample_data:
        print("Note: sample_data is true in parish.toml, so every page carries the sample-data banner.")
        report.add(
            "`sample_data = true` in `parish.toml`: every page says the numbers are made up. Set it to `false` "
            "once your own readings are in."
        )
    print("Content guard: clean.")
    report.write(
        "The data check passed and the site was built" + (f" ({len(warnings)} warning(s) to look at)" if warnings else ""),
        f"{len(ds.readings)} readings, dated {generated_at}.",
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
