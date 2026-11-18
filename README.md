# Parish carbon dashboard

A small website that shows how much energy your parish buildings use and the greenhouse gases that causes. You type in two numbers a month from your utility bills. The site does the rest: a headline figure, a month-by-month chart, a page that explains the method, and a data feed your diocese can read. It can be in English, or in English and Spanish.

**What it counts:** the natural gas you burn for heat and the electricity you buy, for the meters you list. If you wish, it also shows church driving in an electric car, on a separate line that is never mixed into the total.

**What it does not count:** water, refrigerants, people's travel to church, food, purchases. It makes no "net zero" or "carbon neutral" claim and it never compares one parish with another.

It costs nothing to run. There is nothing to install: everything below is done on the GitHub website, in a browser.

> Until you change it, the site shows **made-up sample numbers** for "St. Example Church", with a banner that says so on every page.

## Set up your parish's dashboard

Allow about half an hour. You need your parish's ZIP code and an email address people can write to.

1. **Create a free GitHub account.** Go to [github.com](https://github.com) and choose **Sign up**. GitHub is a website that stores files and keeps every earlier version of them.
2. **Make your own copy.** On this project's page, press the green **Use this template** button, then **Create a new repository**. (A "repository" is GitHub's word for a folder of files.) Give it a short name such as `carbon`. Leave it set to **Public**. Press **Create repository**.
3. **Open the settings file.** In your new copy, click the file named `parish.toml`, then the pencil icon at the top right of the file. This file is the only place your parish's details live.
4. **Fill in your details.** Change the words between the quote marks: your parish's name, city, state, ZIP code and contact email. Each line has a note above it that says what it is for.
5. **Find your electricity region.** Open EPA's Power Profiler at <https://www.epa.gov/egrid/power-profiler>, type your ZIP code, and note the short code it shows for your region (for example `RFCW`). Type that code next to `egrid_subregion`.
6. **Save.** Press the green **Commit changes** button, then **Commit changes** again in the box that opens. ("Commit" is GitHub's word for save.)
7. **Switch on the website.** Click **Settings** (top of the page), then **Pages** in the left-hand list. Under **Build and deployment**, set **Source** to **GitHub Actions**.
8. **Publish it the first time.** Click **Actions** (top of the page), then **Check data and publish site** on the left, then **Run workflow**, and **Run workflow** again. Wait about two minutes for a green tick.
9. **Find your site's address.** Go back to **Settings**, then **Pages**. The address is at the top: "Your site is live at ...". It is also on the summary page of the run you just watched. Share that address.

Your site now shows your parish's name with the sample numbers. Next:

10. **Put in your own readings.** Follow "Each month" below for as many past months as you have bills for. Replace the sample rows; do not keep them.
11. **Tidy the other sample files** in the `data` folder: `milestones.csv` (big changes such as a new boiler; you may leave only the first line) and, if you track church driving, `vehicles.csv` and `commute.csv`. If you do not track driving, set `enabled = false` under `[travel]` in `parish.toml`.
12. **Take the banner down.** In `parish.toml`, change `sample_data = true` to `sample_data = false` and commit.

## Each month

When the gas and electricity bills arrive, add one row for each. It takes about five minutes.

1. Open the `data` folder, then `readings.csv`. Click the pencil icon.
2. Go to the end of the file and add **two rows**: one for electricity, one for gas. Copy the row above and change the dates and the amount. The columns are explained below.
3. Press **Commit changes**, then **Commit changes** again.

The site updates by itself in about two minutes.

**If it does not update:** click **Actions** at the top of the page. A green tick means it worked; reload your site. A red cross means something in the file needs fixing. Click the run with the red cross: its summary page says what is wrong and on which row, in plain words. Fix that row the same way (pencil, change, commit). Until then your site keeps showing its last good version, so nothing is ever broken for your visitors. The longer guide, [docs/monthly-update.md](docs/monthly-update.md), explains every message.

### The columns of `readings.csv`

| Column | What to type |
|---|---|
| `meter_id` | `electric-main` for the electricity bill, `gas-main` for the gas bill. These are made-up names, never the number on the bill. |
| `period_start` | The first day of the billing period, as `YYYY-MM-DD`. |
| `period_end` | The last day of the billing period, as `YYYY-MM-DD`. A period is 25 to 35 days. |
| `period_end_month` | The year and month the period **ends** in, as `YYYY-MM`. |
| `quantity` | The amount used. Electricity in kWh. Gas in therms. |
| `unit` | `kWh` for electricity, `therm` for gas. |
| `peak_kw` | Leave empty. (Optional: the peak demand, if your electricity bill prints one.) |
| `generation_kwh`, `export_kwh` | Leave empty. (For solar panels, later.) |
| `data_quality` | `bill` when the number is printed on the bill. |
| `uncertainty_pct` | Leave empty. |
| `note` | Leave empty, or a few words for yourself. It is never shown on the site. |

An example row, for an electricity bill that ran from September 23 to October 22, 2026 and used 2,390 kWh:

```
electric-main,2026-09-23,2026-10-22,2026-10,2390,kWh,,,,bill,,
```

Type numbers without commas: `2390`, not `2,390`. Keep every comma that separates the columns, even where a column is empty.

Some gas bills print CCF and not therms. The longer guide shows how to convert, and how to enter a month you only have an estimate for.

## Privacy rules

The files in your copy are public. So:

- **No dollar amounts.** Not in a column, not in a note.
- **No account numbers and no meter numbers.** Meters are called `electric-main` and `gas-main`.
- **No bills.** Never upload a bill, a scan or a photo of one. Read the number off the bill and type it.
- **No private addresses or names** in notes or labels.

The site checks for most of this before it publishes and refuses if it finds any. It never repeats what it found, only where. Do not rely on it: a public file's earlier versions stay visible. If there is a word that must never appear on your site, list it under `[privacy]` in `parish.toml`.

## For a diocese: adding up several parishes

Every site built from this template publishes the same data files, free to reuse with credit (CC BY 4.0), at its address followed by `/api/v1/`. Any website or spreadsheet may read them. For each parish:

1. Read `/api/v1/index.json`. It names the parish, lists every file, and has `"sample_data"`. **Skip any site where `sample_data` is `true`:** its numbers are the made-up sample.
2. For one headline figure per parish, read `/api/v1/annual.json` and take `campus.baseline.tco2e_total` (metric tonnes CO2e), with `campus.baseline.label` for the twelve months it covers. Parishes may have different baseline periods; say so beside any total.
3. To add up the same months across parishes, read `/api/v1/monthly.json` (or `monthly.csv`) and add the `tco2e` of the rows whose `period_end_month` falls in the months you want. Do not add up the `periods` in `annual.json`: they are overlapping twelve-month windows.
4. Keep `estimated` and `provisional` beside the numbers. They say when a figure may still change.

Travel, where a parish reports it, is in its own file and is never part of these totals. `/api/v1/openapi.json` and the files under `/api/v1/schema/` describe every field.

## Changing the look, the language and more

All of these are in `parish.toml`, each with a note above it:

- your two colors and your logo;
- English only, or English and Spanish;
- the name of the travel section, or no travel section at all;
- the calendar year your baseline will settle on;
- a web address of your own, if your parish has one.

## For people who code

`CLAUDE.md` has the commands and conventions, and `docs/spec.md` the design. In short: Python 3.12 or newer, `pip install -r requirements.txt`, `python -m pytest -q`, `python -m carbon.build --out dist --generated-at YYYY-MM-DD`.

## License

The program is under the MIT License (see `LICENSE`). Each parish's published data is its own, shared under CC BY 4.0. The Noto fonts are under the SIL Open Font License (`static/fonts/OFL.txt`).
