# Changelog

Changes to this parish's carbon data feed, served under `/api/v1/`. Every parish that publishes with the same dashboard template uses this format, so one reader can read them all.

## How versions work

- This is version 1 (`v1`). Within `v1` only additive changes are made: new files, new fields, new rows. Existing fields keep their names, types and meaning.
- A breaking change creates `/api/v2/`. `v1` stays up beside it.
- Every JSON file has a schema under `schema/`. The schemas allow unknown extra fields, so readers should ignore fields they do not know.
- Emissions are metric tonnes CO2e, rounded to six decimal places. Energy quantities are kWh (electricity) and therms (natural gas).
- When EPA publishes new emission factors, every month is recalculated with the new set and the change is listed here. The energy quantities themselves are not changed by it.

Entries are added at the top, newest first.

## v1: what the feed holds

- `index.json`: version, last updated, links to every file, licence (CC BY 4.0) and contact. `sample_data` is true while the site still shows the template's made-up sample numbers: a reader adding parishes together must skip a feed that says true. `languages` lists the languages the website is built in.
- `organization.json`: stable slug, name, location, campus boundary and the meters included, by generic id. `egrid_subregion` names the EPA eGRID subregion whose rate prices the electricity. `website` is present when the parish has given one.
- `monthly.json` and `monthly.csv`: one row per meter per billing month with quantity, unit, tonnes CO2e, data quality and the factor used.
- `annual.json`: the baseline and every complete twelve-month period, with campus totals by fuel (scope 1 natural gas, scope 2 electricity, location-based). The baseline's `basis` is `calendar_` and the parish's baseline year (for example `calendar_2025`) once January to December of that year is complete, and `latest_twelve_billing_months` before that. Staff travel is a separate block, `staff_travel`, with `scope: 3` and `ghg_category: "business_travel"`. It is never added to the campus totals.
- `travel.json` and `travel.csv`: monthly miles, emissions where counted here and the avoided figure, by vehicle slug. Avoided emissions are compared with a gasoline car; not an offset and not subtracted from campus totals. Where a vehicle charges at the church its electricity is already counted in campus electricity, so `tco2e_counted_here` is null.
- A parish that does not report a travel line has no `travel.json`, no `travel.csv`, no entries for them in `index.json` and no `staff_travel` block in `annual.json`. Readers should treat all of these as optional.
- `factors.json`: the emission factors this parish's figures use, each with value, unit, source, edition, revision, URL and retrieval date: natural gas, the electricity rate of its own eGRID subregion (`electricity_` and the subregion in lower case) and, with a travel line, the gasoline-car comparison.
- `milestones.json`: dated milestones in English and Spanish (`label_es` is empty on a site built in English only). Each has an optional `date_precision`: `year`, `month` or `day`. It says how much of `date` is known. For `year`, `date` is 1 January of that year and only the year is meant; for `month`, only the year and month, and `date` is the first day of that month. Readers that do not know the field can ignore it.
- `openapi.json`: OpenAPI description of these read-only paths.
- `schema/`: a JSON Schema for each JSON file.
