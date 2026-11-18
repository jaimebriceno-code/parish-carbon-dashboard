# Parish carbon dashboard: design

What the template is for, what it deliberately does not do, and the decisions behind the code. `CLAUDE.md` has the commands and conventions; `README.md` and `docs/monthly-update.md` are for the volunteer who runs a copy.

## Intent

A parish wants an honest, public record of the greenhouse gases its buildings cause, built from its own utility bills, with a machine-readable feed a diocese or regional body can add up. The person keeping it current is a volunteer with no coding background. So:

- One settings file (`parish.toml`) and one data file a month (`data/readings.csv`), both edited in the GitHub website.
- No server, database, login or key. A static site, rebuilt and published by a GitHub Actions workflow on every change.
- Every mistake the volunteer can make ends in a plain sentence that names the file, the row or key, and the fix, and never takes the published site down.

## Goals

- A defensible baseline of emissions from natural gas and electricity, with the energy use behind it, and the monthly trend.
- A public site, in English or in English and Spanish, that explains the numbers plainly.
- A read-only, versioned JSON feed plus CSV with stable addresses and the same shape for every parish. Additive changes only within a version.
- Honest labels: every month says whether it came from a bill or was estimated from a bill's chart, and estimates are replaced by exact figures when they arrive.
- An optional travel line (church driving in an electric car), shown apart from the campus footprint with an "avoided compared with a gasoline car" figure that is never subtracted from anything.
- Room for solar later: generation and export fields, and dated milestones.

## Non-goals

- No interval data, demand analysis, solar sizing or savings calculators.
- No scope 3 other than the optional travel line. No offsets, no "net-zero" or "carbon neutral" claims, no rankings or comparisons between parishes.
- Water, refrigerants and the congregation's travel are out of scope for version 1.
- No hosting other than a static file host. The template ships a GitHub Pages workflow; the built folder works on any static host.

## Anti-goals (a failure even if it works)

- Publishing dollar amounts, account numbers, meter numbers or bill scans.
- Presenting estimates as measurements, or silently restating numbers when factors change.
- Netting travel, an avoided-emissions figure or anything else against the campus footprint, or calling any of it an offset.
- A site only a programmer can update.
- Anything in the template that names one real parish, place, utility or diocese.

## Settings: `parish.toml`

Read with `tomllib` and checked by `carbon/settings.py`. Every problem is reported at once, each as a sentence naming the section and key.

| Section | Keys | Notes |
|---|---|---|
| (top) | `sample_data` | True shows the sample-data banner on every page and sets `sample_data` in the feed. Refused as false while the name is still the sample's. |
| `[organization]` | `name`, `slug`, `city`, `state`, `zip`, `contact_email`, `website`, `campus_en`, `campus_es` | `slug` is derived from name, city and state when blank. The campus phrases are used inside sentences ("We count our church campus"). |
| `[site]` | `base_url`, `languages` | `base_url` is an origin plus optional path; blank means "use what the workflow passes, else root-relative links". `languages` is `["en"]` or `["en", "es"]`. |
| `[brand]` | `primary_color`, `accent_color`, `logo` | The colours replace three tokens in the stylesheet and colour the chart. They are refused if text contrast would fall below WCAG AA (4.5 to 1) against the fixed colours, or if the two are too alike to tell apart in the chart (3 to 1). |
| `[grid]` | `egrid_subregion` | Picks the `electricity_<subregion>` row of `data/factors.csv`. Unknown: a build error listing the subregions on file. |
| `[baseline]` | `calendar_year` | The calendar year the baseline settles on once its twelve months are complete. |
| `[travel]` | `enabled`, `label_en`, `label_es` | Off: no travel card, section, feed files or block, and the two travel CSVs are not read. |
| `[privacy]` | `forbidden_words` | Extra whole words or phrases the content guard refuses. Empty by default. |

## Data

- `data/meters.csv`: `meter_id` (a made-up slug, never an account number), `fuel` (`electricity` or `natural_gas`), `label`, `notes`.
- `data/readings.csv`: `meter_id`, `period_start`, `period_end` (both or neither), `period_end_month` (YYYY-MM), `quantity`, `unit` (kWh or therm), `peak_kw`, `generation_kwh`, `export_kwh`, `data_quality` (`bill`, `chart_estimate`, `chart_measured`, `utility_export`), `uncertainty_pct`, `note`.
- `data/milestones.csv`: `date`, `kind` (`boiler`, `solar_connected`, `other`), `label_en`, `label_es` (optional on an English-only site), `precision` (`year`, `month`, `day`).
- `data/vehicles.csv`: `vehicle_id`, `label`, `kwh_per_mile` (from the EPA fuel economy label for the exact model), `charging_place` (`home` or `church`), `charging_grid_region`, and two private columns, `kwh_per_mile_source` and `note`.
- `data/commute.csv`: `vehicle_id`, `period_end_month`, `miles`, `data_quality` (`estimate` or `odometer`), `note`. No trip logs, addresses or dates of single trips.
- `data/factors.csv`: `factor_id`, `value`, `unit`, `gases`, `source`, `edition`, `revision`, `url`, `retrieved`, and the Spanish `edition_es`, `revision_es`. One row each for `natural_gas` and `gasoline_car_per_mile`, and one `electricity_<subregion>` row per eGRID subregion. All rows are loaded; a parish's figures use, show and publish only its own. Values come from EPA's published tables, with the conversion written in `source`; none is typed from memory.
- Free text that is published: meter labels and milestone labels. Free text that is never published: every `note`, the vehicle label and its source text.
- The data shipped with the template is fictional sample data.

## Calculation

- tonnes CO2e = quantity times the factor in kg, divided by 1000. Scope 1 is natural gas; scope 2 is purchased electricity, location-based; all three gases.
- Billing months are used as reported, labelled by the month the period ends, with no pro-rating to calendar months.
- A period is twelve consecutive billing months in which every listed meter has a reading and both fuels are present. A missing month is never filled in.
- Baseline: the baseline calendar year if complete; otherwise the latest complete period, marked provisional. Provisional also while any reading in it is an estimate.
- Market-based electricity is not reported in version 1.
- Travel: EV electricity is miles times the vehicle's kWh per mile. Charged away from the campus, its emissions use the grid factor of the charging region and are counted on the travel line only. Charged at the church, the electricity is already in the campus meter, so nothing is counted again. The avoided figure is the gasoline-car emissions for the same miles minus what is counted on the line. It is informational and never enters a campus total.
- Nothing in `carbon/calc.py` rounds. The feed rounds emissions to six decimal places and quantities to three; the pages round for display, half up.

## Website

- Pages: Home (baseline, split by fuel, monthly chart with its table, the travel card), Data (every reading, downloads, the feed), Methodology (boundary, factors with sources, billing months, baseline, labels, exclusions, travel, terms), About (why, the campus, updates, solar once connected, milestones, contact).
- Static HTML and CSS. No scripts, no external resources. Each chart is an inline SVG with a captioned data table in the same section; estimates are shown by a dashed outline as well as by wording. Mobile first, print friendly, WCAG 2.1 AA.
- Every sentence is in `carbon/i18n.py`, English beside Spanish, with the parish's own words as placeholders. English and Spanish pages have the same sections and numbers.
- Links carry the site's path prefix, so the same code serves `https://someone.github.io/repo-name/` and a domain root. With a base URL set, pages carry a canonical link and absolute `hreflang` alternates.
- While `sample_data` is true a banner on every page says the numbers are made up.

## Feed (version 1)

Static files under `/api/v1/`, with paths relative to `index.json`.

- `index.json`: version, last updated, organization, licence (CC BY 4.0), contact, `sample_data`, `languages`, and a link to every file and schema.
- `organization.json`: slug, name, city, state, ZIP, campus and boundary statements, `egrid_subregion`, `sample_data`, the meters by generic id.
- `monthly.json`, `monthly.csv`: one row per meter per billing month with quantity, unit, tonnes CO2e, data quality, uncertainty and the factor id.
- `annual.json`: the baseline and every complete twelve-month period, by fuel. With travel on, a separate `staff_travel` block (scope 3, business travel) that is never added to the campus totals.
- `travel.json`, `travel.csv` (travel on only): monthly miles, emissions counted on the line and the avoided figure, by vehicle slug.
- `factors.json`: the factors in use, with sources. `milestones.json`. `openapi.json`. `schema/*.schema.json`. `CHANGELOG.md`.

Within v1 only additive changes are allowed. A breaking change creates v2 and leaves v1 up.

## Checks before anything is published

1. `parish.toml` is read and checked.
2. The data is loaded (unknown or forbidden columns stop it) and validated: units, period lengths, overlaps, duplicates, the configured subregion, free text free of dollars and long digit runs. Errors stop the build; warnings (gaps, implausible changes, zero quantities) are printed.
3. The site and feed are built into a staging folder.
4. The content guard scans every built page and feed file for dollar amounts, long digit runs, offset wording and the parish's forbidden words. It reports file and code, never the text.
5. Only then is the folder swapped into place. A failure at any step leaves the previous output, and the published site, untouched.

## Publishing

`.github/workflows/publish.yml` runs on a push to `main` and by hand. The build job checks the settings and data first (writing its messages to the run's summary), runs the test suite, asks GitHub for the Pages address, builds with that address unless `parish.toml` sets one, and uploads the result. The deploy job publishes with `actions/deploy-pages` and runs only when Pages is switched on for the repository, so a fresh copy of the template passes its checks before the volunteer has enabled Pages.

## Acceptance checks

- The validator and tests pass, and the build is reproducible byte for byte.
- Every feed file validates against its published schema.
- English and Spanish pages have the same sections and numbers; an English-only build has no trace of Spanish pages or links.
- A build under a path prefix has no internal link, asset or `url()` that escapes the prefix.
- The pages work without JavaScript, and the stylesheet's text and background pairs meet AA contrast for any accepted brand colours.
- The built site and feed contain no dollar amounts, account numbers, meter numbers or scans.
- No tracked file names the parish the template was first written for.
