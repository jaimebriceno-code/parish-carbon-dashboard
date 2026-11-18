# parish-carbon-dashboard

A template any US parish can copy to publish a static carbon dashboard: its natural gas and electricity use, the greenhouse gases that causes, and a read-only JSON feed under `/api/v1/`. English at `/`, Spanish at `/es/` when switched on. It is a Python static-site generator: `parish.toml` plus `data/*.csv` in, a folder of HTML, CSS and JSON out, published by GitHub Pages from `.github/workflows/publish.yml`. The design is in `docs/spec.md`; the volunteer's guides are `README.md` and `docs/monthly-update.md`.

The people who run a copy have no coding background and work only in the GitHub website. Anything they must touch belongs in `parish.toml` or `data/`, and anything that can go wrong for them must end in a plain sentence in the workflow's summary, never a traceback.

## Commands

Use the repo's virtualenv (`.venv`, made with `uv venv .venv` and `uv pip install -r requirements.txt`). Python 3.11 or newer runs it (`tomllib`); the workflow uses 3.12.

- Tests: `.venv/bin/python -m pytest -q`
- Build: `.venv/bin/python -m carbon.build --out dist --generated-at <YYYY-MM-DD>`. It reads `parish.toml` (`--settings` for another file) and `data/` (`--data`), validates both, builds into a staging folder beside `dist/`, runs the content guard, and only then swaps the folder in by rename. A failed build leaves `dist/` as it was. `dist/` is replaced only when it is missing, empty or holds the `.carbon-build` marker; an `--out` that is a symlink, or that overlaps a build input, is refused with exit code 2. Exit 1 means the settings, the data, the guard or the disk said no. Two builds with the same `--generated-at` are byte-identical. A good build ends with "Content guard: clean."
- `--base-url URL` is used only when `parish.toml` leaves `[site] base_url` blank (the workflow passes the address GitHub Pages reports). With neither, links are root-relative, which is what a local preview needs.
- `--summary FILE` appends the result and every message as Markdown (the workflow passes `$GITHUB_STEP_SUMMARY`).
- Preview: build, then `python3 -m http.server 8766 --directory dist` (the `parish-carbon-dist` entry in `.claude/launch.json`).
- Spot-check sheet for a second reader: `.venv/bin/python -m carbon.spotcheck 2026-10 2026-08` prints a Markdown table of transcribed values to compare with the bills.
- There is no deploy command. Publishing is the workflow: on a push to `main` or a manual run it checks the settings and data, runs the tests, builds, and deploys with `actions/deploy-pages`, but only when Pages is switched on for the repository.

## Where things are

- `parish.toml`: the one settings file. `carbon/settings.py` reads it (`tomllib`), checks every key and returns a frozen `Settings`; every problem is a sentence that names the section and key. Keep the file small and comment every key in plain words.
- `carbon/models.py` (CSV loading), `validate.py` (errors and warnings by code), `calc.py` (the method; pure functions, no rounding), `feed.py` (the JSON feed), `site.py` and `templates/` (the pages), `i18n.py` (every sentence, English beside Spanish), `charts.py` (inline SVG), `guard.py` (the content guard), `build.py` (the command).
- `tests/support.py` and `tests/fixtures/sample/`: a frozen copy of the sample parish. Tests take their expectations from there or from data they build themselves, never from `data/` or `parish.toml`, because a parish replaces both and its workflow runs the whole suite on every change. The few tests that do read the repository's own files check only what holds for any valid parish.

## Data conventions

- Data lives in `data/*.csv`: `meters`, `readings`, `milestones`, `factors`, `vehicles`, `commute`. `commute` is the internal name of the travel data; the site and feed say travel. `load_dataset` rejects any column it does not know and any column that could hold dollars or identifiers.
- Never store dollar amounts, account numbers, meter numbers or bill scans. Meter ids are made-up slugs (`electric-main`, `gas-main`). `.gitignore` skips `*.pdf`.
- The sample data is fictional ("St. Example Church") and invented, and the site says so in a banner on every page, and the feed in `sample_data`, until `parish.toml` says `sample_data = false`.
- `data_quality` is `bill`, `chart_estimate`, `chart_measured` or `utility_export`. `chart_estimate` is a figure printed as a number on a bill's chart; `chart_measured` is a bar measured against the chart's printed gridlines and needs `uncertainty_pct`. Both are labelled estimates and are replaced by exact figures. Travel rows are `estimate` or `odometer`.
- Billing months are labelled by the month the period ends, with no pro-rating. `period_start` and `period_end` are both given or both blank; a period is 25 to 35 days; two periods on a meter may share one boundary day. A baseline is twelve consecutive billing months with every meter complete.
- Scope 1 is natural gas. Scope 2 is electricity, location-based. Metric tonnes CO2e, all three gases.
- Emission factors live in `data/factors.csv`, one row per factor: `natural_gas`, `gasoline_car_per_mile`, and one `electricity_<subregion>` row per EPA eGRID subregion (id in lower case). `[grid] egrid_subregion` in `parish.toml` picks the electricity row; an unknown one is a build error that lists the ones on file. Only the factors a parish uses are shown and published. **Never type a factor, a unit conversion or any EPA figure from memory**: each row comes from EPA's published tables and records value, unit, source, edition, revision, URL and retrieval date. When factors change, all months are recalculated and the feed changelog (`static/api-changelog.md`) says so. Test-only factors are obviously fake and live in the tests.
- Baseline: the latest twelve complete billing months, labelled provisional, until the calendar year named in `[baseline] calendar_year` is complete; then that year. Labelled estimated while any estimate remains.
- Travel is optional (`[travel] enabled`) and is scope 3, category 6 (business travel), in its own card and its own feed files. It is never added to the campus totals or the baseline and never called an offset; the avoided figure reads "avoided emissions compared with a gasoline car, not an offset". A vehicle that charges at the church has its emissions marked "already counted in campus electricity". With travel off there is no card, no section, no travel file and no `staff_travel` block, and the two travel CSVs may be missing.
- The About page mentions solar only when a `solar_connected` milestone exists (`SOLAR_LABEL` enforces it).
- No "net-zero", "carbon neutral" or offset claims, and no comparisons between parishes.

## Guardrails

- Nothing in the template may name a real parish, person, place, utility or diocese. Wording that belongs to one parish goes in `parish.toml` and reaches the pages through the placeholders in `carbon/i18n.py` (`ORG_PLACEHOLDERS`).
- Every internal link, asset and feed link goes through the site's path prefix (`Settings.path_prefix`), so the site works under `https://someone.github.io/repo-name/` and at a domain root. The stylesheet reaches its fonts by relative URLs. Tests build with a prefixed base URL and fail if anything escapes it.
- The feed is a promise to outside readers: within v1 only additive changes (new files, fields, rows). Licence CC BY 4.0 for each parish's data. Write feed changes in `static/api-changelog.md`.
- The content guard (`carbon/guard.py`) always refuses dollar amounts, long digit runs and offset wording, and the parish's own `[privacy] forbidden_words`. It reports codes and file paths, never the text.
- Brand colours come from `parish.toml` and are checked for WCAG AA contrast against the fixed colours of `static/site.css` when the file is read. Mobile first, print friendly, works with JavaScript off, each chart an inline SVG with a data table beside it.
- Write tests with every change, and when a validator code is added, explain it in `docs/monthly-update.md` (a test checks). Do not add a remote, push or publish without the owner's say-so.
