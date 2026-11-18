"""Tests on the repository as a template: nothing of the parish it was first written for is left, the publishing
workflow is shaped as the guides say, and the guides cover what a volunteer will meet.

The workflow itself cannot run here (it needs GitHub). These tests pin its structure as text, so a change that
would break the promises in the README (the data check comes first and is readable; a copy without Pages does
not fail) is at least noticed.
"""

import json
import re

import pytest

from carbon import guard, validate
from carbon.settings import KNOWN_KEYS
from tests.support import ROOT

WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"
SKIPPED_FOLDERS = {".venv", ".git", "dist", "__pycache__", ".pytest_cache"}


def _repo_files():
    """Every file a copy of the template would hold: all of them, except installed packages, git's own folder,
    build output and Python's caches (which are never committed)."""
    for path in sorted(ROOT.rglob("*")):
        if path.is_file() and not SKIPPED_FOLDERS & set(path.relative_to(ROOT).parts):
            yield path


# --- the first parish is gone ---------------------------------------------------------------------------------

# The identifiers of the parish this template grew out of. They are written backwards so that this file does
# not itself contain them: a plain search of the repository for any of them must find nothing at all.
_BACKWARDS = ("leahcim", "slegnaleahcimts", "nywreb", "20406", "aams", "dlihc slegna", "emiaj", "onecirb")
SOURCE_IDENTIFIERS = re.compile("|".join(re.escape(word[::-1]) for word in _BACKWARDS).encode("ascii"), re.IGNORECASE)


def test_the_pattern_is_the_agreed_list_and_does_find_what_it_should():
    assert len(_BACKWARDS) == 8
    for word in _BACKWARDS:
        assert SOURCE_IDENTIFIERS.search(f"x {word[::-1].upper()} y".encode("ascii"))
    assert not SOURCE_IDENTIFIERS.search(b"St. Example Church, Springfield, IL 62701")


def test_no_file_in_the_repository_names_the_parish_the_template_came_from():
    files = list(_repo_files())
    assert len(files) > 50, "the walk found too few files to mean anything"
    found = []
    for path in files:
        data = path.read_bytes()
        for match in SOURCE_IDENTIFIERS.finditer(data):
            line = data.count(b"\n", 0, match.start()) + 1
            found.append(f"{path.relative_to(ROOT)}:{line}")
    assert found == [], "identifiers of the source parish are still in: " + ", ".join(found[:20])


def test_no_file_or_folder_is_named_after_it_either():
    for path in _repo_files():
        assert not SOURCE_IDENTIFIERS.search(str(path.relative_to(ROOT)).encode("utf-8")), path


def test_the_one_server_deploy_script_its_tests_and_its_server_config_are_gone():
    for gone in ("scripts/deploy.sh", "scripts", "tests/test_deploy_script.py", "static/htaccess", "docs/acceptance.md", "docs/runbook.md"):
        assert not (ROOT / gone).exists(), gone
    for kept in ("static/fonts/OFL.txt", "static/img/mark.svg", "static/img/favicon.svg", "parish.toml", "LICENSE", "README.md"):
        assert (ROOT / kept).is_file(), kept
    assert sorted(p.name for p in (ROOT / "static" / "img").iterdir()) == ["favicon.svg", "mark.svg"]
    assert len(list((ROOT / "static" / "fonts").glob("noto-*.woff2"))) == 4


def test_the_preview_entry_serves_dist_and_nothing_else():
    launch = json.loads((ROOT / ".claude" / "launch.json").read_text(encoding="utf-8"))
    assert [c["name"] for c in launch["configurations"]] == ["parish-carbon-dist"]
    (entry,) = launch["configurations"]
    assert entry["runtimeArgs"][-2:] == ["--directory", "dist"] and "127.0.0.1" in entry["runtimeArgs"]


def test_git_never_takes_the_virtualenv_the_build_output_or_a_bill():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
    for entry in (".venv/", "dist/", "__pycache__/", "*.pdf"):
        assert entry in ignored, entry


# --- the publishing workflow --------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def workflow():
    return WORKFLOW.read_text(encoding="utf-8")


def _step_names(workflow):
    return re.findall(r"^      - name: (.+)$", workflow, re.MULTILINE)


def test_the_workflow_runs_on_a_push_to_main_and_by_hand(workflow):
    assert re.search(r"^on:\n  push:\n    branches: \[main\]\n  workflow_dispatch:", workflow, re.MULTILINE)
    assert "pull_request" not in workflow and "schedule:" not in workflow


def test_every_action_is_an_official_one_pinned_to_a_major_version(workflow):
    used = re.findall(r"uses: (\S+)", workflow)
    assert sorted(used) == sorted(
        [
            "actions/checkout@v7",
            "actions/setup-python@v7",
            "actions/configure-pages@v6",
            "actions/upload-pages-artifact@v5",
            "actions/deploy-pages@v5",
        ]
    )
    for action in used:
        assert re.fullmatch(r"actions/[a-z-]+@v\d+", action), action


def test_the_workflow_asks_for_no_more_than_it_needs_and_holds_no_secret(workflow):
    assert re.search(r"^permissions:\n  contents: read\n", workflow, re.MULTILINE), "read-only by default"
    assert workflow.count("pages: write") == 1 and workflow.count("id-token: write") == 1
    deploy = workflow[workflow.index("\n  deploy:") :]
    assert "pages: write" in deploy and "id-token: write" in deploy
    assert "secrets." not in workflow and "contents: write" not in workflow


def test_the_build_job_checks_the_data_before_anything_else_can_fail_and_writes_the_summary(workflow):
    build = workflow[workflow.index("\n  build:") : workflow.index("\n  deploy:")]
    names = _step_names(build)
    assert names == [
        "Get the files",
        "Set up Python",
        "Install the four libraries the build uses",
        "Check the settings and the data",
        "Run the program's own tests",
        "Ask GitHub where the site lives",
        "Build the site",
        "Hand the built site to the publishing job",
        "Say why nothing was published yet",
    ]
    check = build[build.index("Check the settings and the data") : build.index("Run the program's own tests")]
    assert "python -m carbon.build" in check and '--summary "$GITHUB_STEP_SUMMARY"' in check
    assert '--generated-at "$(date -u +%F)"' in check, "today's date, in UTC"
    assert "$RUNNER_TEMP" in check, "the check builds into a scratch folder, not into what is published"
    assert "python -m pytest -q" in build
    assert "pip install" in build and "-r requirements.txt" in build
    assert 'python-version: "3.12"' in build


def test_the_published_build_uses_githubs_pages_address_and_todays_utc_date(workflow):
    build = workflow[workflow.index("      - name: Build the site") : workflow.index("      - name: Hand the built site")]
    assert "PAGES_BASE_URL: ${{ steps.pages.outputs.base_url }}" in build
    assert '--base-url "$PAGES_BASE_URL"' in build and "--out dist" in build
    assert '--generated-at "$(date -u +%F)"' in build
    # The address is passed through the environment, never pasted into the command line by GitHub.
    assert "${{" not in build.split("run: |")[1]
    upload = workflow[workflow.index("Hand the built site") : workflow.index("Say why nothing was published yet")]
    assert "actions/upload-pages-artifact@v5" in upload and "path: dist" in upload


def test_a_copy_without_pages_is_checked_and_passes_and_only_publishing_is_skipped(workflow):
    assert "PAGES_ON: ${{ github.event.repository.has_pages || inputs.publish_anyway || false }}" in workflow
    build = workflow[workflow.index("\n  build:") : workflow.index("\n  deploy:")]
    # The three Pages-only steps are conditional; the check, the tests and the build are not.
    for name in ("Ask GitHub where the site lives", "Hand the built site to the publishing job"):
        step = build[build.index(f"- name: {name}") :].split("\n      - name:")[0]
        assert "if: env.PAGES_ON == 'true'" in step, name
    for name in ("Check the settings and the data", "Run the program's own tests", "Build the site"):
        step = build[build.index(f"- name: {name}") :].split("\n      - name:")[0]
        assert "\n        if:" not in step, name
    note = build[build.index("- name: Say why nothing was published yet") :]
    assert "if: env.PAGES_ON != 'true'" in note and "Source" in note and "GitHub Actions" in note
    assert "continue-on-error" not in workflow, "a failed step must never be published over"

    deploy = workflow[workflow.index("\n  deploy:") :]
    assert "needs: build" in deploy
    assert "if: ${{ github.event.repository.has_pages || inputs.publish_anyway }}" in deploy
    assert "name: github-pages" in deploy and "actions/deploy-pages@v5" in deploy


def test_one_publish_runs_at_a_time_and_is_never_cut_off(workflow):
    assert re.search(r"^concurrency:\n  group: pages\n  cancel-in-progress: false", workflow, re.MULTILINE)


def test_the_workflow_file_has_no_tabs_and_consistent_indentation(workflow):
    assert "\t" not in workflow
    for number, line in enumerate(workflow.splitlines(), 1):
        if line.strip() and not line.lstrip().startswith("#"):
            assert (len(line) - len(line.lstrip(" "))) % 2 == 0, f"line {number}"


def test_requirements_are_pinned_to_exact_versions():
    lines = [line for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 4
    for line in lines:
        assert re.fullmatch(r"[a-z0-9-]+==\d+(\.\d+)+", line), line


# --- the guides ----------------------------------------------------------------------------------------------

README = ROOT / "README.md"
MONTHLY = ROOT / "docs" / "monthly-update.md"


def _codes_in(module):
    source = (ROOT / "carbon" / f"{module.__name__.split('.')[-1]}.py").read_text(encoding="utf-8")
    return set(re.findall(r'(?:error|warn)\(\s*"([A-Z][A-Z_]+)"', source))


def test_the_monthly_guide_explains_every_code_the_validator_and_the_guard_can_print():
    guide = MONTHLY.read_text(encoding="utf-8")
    validator_codes = _codes_in(validate)
    assert len(validator_codes) >= 20, validator_codes
    guard_codes = {value for name, value in vars(guard).items() if name.isupper() and isinstance(value, str) and value == name}
    assert guard_codes == {"FORBIDDEN_WORD", "DOLLAR_AMOUNT", "LONG_DIGIT_RUN", "OFFSET_TERM", "NOT_UTF8"}
    for code in sorted(validator_codes | guard_codes):
        assert f"`{code}`" in guide, f"docs/monthly-update.md does not explain {code}"


def test_the_monthly_guide_covers_ccf_estimates_and_replacing_them():
    guide = MONTHLY.read_text(encoding="utf-8")
    assert "Therms = CCF × the factor printed on that bill" in guide
    assert "170 × 1.035 = 175.95" in guide and round(170 * 1.035, 2) == 175.95
    # It gives no conversion factor of its own: the bill's is the only one to use.
    assert "Do not guess one" in guide
    for phrase in ("chart_estimate", "chart_measured", "uncertainty_pct", "utility_export", "Do not add a second row", "change the row that is already there"):
        assert phrase in guide, phrase


def test_the_example_rows_in_the_guides_are_rows_the_loader_accepts(tmp_path):
    from carbon.models import load_dataset
    from tests.support import sample_data_copy

    header = "meter_id,period_start,period_end,period_end_month,quantity,unit,peak_kw,generation_kwh,export_kwh,data_quality,uncertainty_pct,note"
    rows = set()
    for path in (README, MONTHLY):
        rows.update(re.findall(r"^(?:Before: `|After: `)?((?:electric|gas)-main,[^`\n]*)`?$", path.read_text(encoding="utf-8"), re.MULTILINE))
    assert len(rows) >= 5
    for row in sorted(rows):
        assert row.count(",") == 11, f"an example row has the wrong number of columns: {row}"
        data = sample_data_copy(tmp_path / str(abs(hash(row))))
        (data / "readings.csv").write_text(header + "\n" + row + "\n", encoding="utf-8")
        (reading,) = load_dataset(data).readings
        errors = [i for i in validate.validate_dataset(load_dataset(data)) if i.level == "error"]
        assert errors == [], f"{row}: {errors}"
        assert reading.quantity > 0


def test_the_readme_walks_through_setup_on_the_github_website_and_the_monthly_update():
    text = README.read_text(encoding="utf-8")
    for heading in ("## Set up your parish's dashboard", "## Each month", "## Privacy rules", "## For a diocese: adding up several parishes", "## License"):
        assert heading in text, heading
    for must in (
        "https://www.epa.gov/egrid/power-profiler",
        "Use this template",
        "parish.toml",
        "pencil",
        "Commit changes",
        "**Source** to **GitHub Actions**",
        "Your site is live at",
        "`readings.csv`",
        "about two minutes",
        "sample_data = false",
        "/api/v1/index.json",
        "/api/v1/annual.json",
        "CC BY 4.0",
        "MIT License",
    ):
        assert must in text, must
    setup = text[text.index("## Set up your parish's dashboard") : text.index("## Each month")]
    assert [int(n) for n in re.findall(r"^(\d+)\. ", setup, re.MULTILINE)] == list(range(1, 13))
    # Done in the browser: no step asks for a terminal or an install.
    for word in ("terminal", "command line", "git clone", "pip install", "npm"):
        assert word not in setup.lower() and word not in text[text.index("## Each month") : text.index("## Privacy rules")].lower()


def test_the_readme_table_lists_every_column_of_the_readings_file_with_an_example_row():
    text = README.read_text(encoding="utf-8")
    table = text[text.index("### The columns of `readings.csv`") : text.index("## Privacy rules")]
    header = (ROOT / "tests" / "fixtures" / "sample" / "readings.csv").read_text(encoding="utf-8").splitlines()[0]
    for column in header.split(","):
        assert f"`{column}`" in table, column
    assert "electric-main,2026-09-23,2026-10-22,2026-10,2390,kWh,,,,bill,," in table


def test_the_guides_are_written_in_short_plain_sentences():
    for path in (README, MONTHLY):
        text = path.read_text(encoding="utf-8")
        prose = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
        prose = "\n".join(line for line in prose.splitlines() if not line.startswith("|"))
        lowered = prose.lower()
        for jargon in ("repo ", "ci ", "pipeline", "stdout", "traceback", "yaml", "lint", "regex", "pull request", "fork"):
            assert jargon not in lowered, f"{path.name} says {jargon!r}"
        # Headings, list items and paragraphs each stand alone; within one, sentences end at . ! ? or a colon.
        blocks = re.split(r"\n\s*\n|\n(?=#|- |\d+\. )", prose)
        blocks = [block for block in blocks if not block.lstrip().startswith("#")]
        sentences = [s for block in blocks for s in re.split(r"(?<=[.!?:])\s+", re.sub(r"\s+", " ", block))]
        long = [s for s in sentences if len(s.split()) > 45]
        assert not long, f"{path.name} has sentences over 45 words: {long[:2]}"


def test_the_license_is_mit_for_the_code_and_says_the_data_is_cc_by():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("MIT License\n\nCopyright (c) ") and "parish-carbon-dashboard contributors" in text
    assert "Permission is hereby granted, free of charge" in text and 'THE SOFTWARE IS PROVIDED "AS IS"' in text
    assert "CC BY 4.0" in text and "https://creativecommons.org/licenses/by/4.0/" in text
    assert "SIL Open Font" in text


def test_the_project_notes_name_the_settings_file_the_commands_and_the_guardrails():
    notes = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    for must in ("parish.toml", "python -m pytest -q", "python -m carbon.build --out dist --generated-at", "## Data conventions", "## Guardrails", "from memory", "tests/fixtures/sample"):
        assert must in notes, must
    assert "deploy.sh" not in notes and "htaccess" not in notes
    spec = (ROOT / "docs" / "spec.md").read_text(encoding="utf-8")
    for section, keys in KNOWN_KEYS.items():
        for key in keys:
            assert f"`{key}`" in spec, f"docs/spec.md does not list {section} {key}"
