# The monthly update, in full

This is the longer version of "Each month" in the README. Everything is done on the GitHub website. You change a file by clicking it, pressing the pencil icon, typing, and pressing **Commit changes** (GitHub's word for save).

After every save GitHub checks your files and rebuilds the site. That takes about two minutes. If a check fails, the site keeps its last good version and the **Actions** tab tells you what to fix.

## 1. Add the month's two rows

Open `data/readings.csv` and press the pencil. Add one row for the electricity bill and one for the gas bill, at the end of the file.

The first line of the file names the columns. Every row has the same twelve columns, separated by commas:

```
meter_id,period_start,period_end,period_end_month,quantity,unit,peak_kw,generation_kwh,export_kwh,data_quality,uncertainty_pct,note
electric-main,2026-09-23,2026-10-22,2026-10,2390,kWh,,,,bill,,
gas-main,2026-09-09,2026-10-08,2026-10,176,therm,,,,bill,,
```

- **Dates** are written year-month-day: `2026-10-22`.
- **`period_end_month` is the month the period ends in**, even if most of the days were in the month before. A bill that ends on November 4 is filed under `2026-11`. Months are never split.
- **Numbers have no commas**: `2390`.
- **Empty columns keep their commas.** A row always has eleven commas.
- A billing period is **25 to 35 days**. If yours is longer or shorter, check the dates.
- Gas and electricity bills often end on different days. That is normal.

## 2. If your gas bill prints CCF, not therms

The site counts gas in therms. Some bills print CCF (hundreds of cubic feet) and, somewhere near it, a multiplier. It may be called a "heat factor", "BTU factor" or "therm factor".

**Therms = CCF × the factor printed on that bill.**

Example: a bill shows 170 CCF and a factor of 1.035. Then 170 × 1.035 = 175.95. Type `176` (or `175.95`) and `therm`.

Use the factor on **that month's bill**: it changes a little from month to month. If your bill prints no factor, ask the gas company for it. Do not guess one and do not use one from another source.

If your bill already prints therms, type that number. Nothing to convert.

## 3. A month you only have an estimate for

Sometimes you do not have the bill, but a later bill shows a small bar chart of past months.

- If the chart **prints the number** for that month, enter it with `data_quality` set to `chart_estimate`. Leave the two date columns empty.
- If the chart shows **only bars**, you may measure a bar against the chart's printed lines. Enter it as `chart_measured` and put your margin of error, in percent, in `uncertainty_pct` (for example `5`). Do not enter a bar you only read by eye.

```
electric-main,,,2025-11,2210,kWh,,,,chart_estimate,,
gas-main,,,2025-12,1085,therm,,,,chart_measured,5,
```

The site labels these months "estimated", and the baseline "provisional", for as long as any remain.

## 4. Replacing an estimate with the exact figure

When the real bill turns up, **change the row that is already there**. Do not add a second row for the same month.

1. Find the row for that meter and month.
2. Put in the exact `quantity`.
3. Fill in `period_start` and `period_end` from the bill.
4. Change `data_quality` to `bill`.
5. Empty `uncertainty_pct`.

Before: `gas-main,,,2025-12,1085,therm,,,,chart_measured,5,`
After: `gas-main,2025-11-07,2025-12-08,2025-12,1102,therm,,,,bill,,`

If your utility gives you a file of your past use, you may enter those numbers with `data_quality` set to `utility_export`. They count as exact.

## 5. See whether it worked

Click **Actions** at the top of the page. The newest run is at the top of the list.

- **Green tick:** done. Reload your site.
- **Yellow dot:** still working. Wait a minute.
- **Red cross:** something needs fixing. Click the run. The summary page lists each problem with a short code in capital letters, the meter and the month. Find the code in the lists below, fix the row, and commit again.

Warnings do not stop the site. They ask you to look twice at a number.

## What each message means

### Problems in `parish.toml`

These start with `parish.toml:` and name the section in square brackets and the key, for example `[organization] zip`. The sentence says what is expected. Open `parish.toml`, find that line, fix it.

Common ones:

- A missing quote mark.
- A ZIP code that is not five digits.
- `languages` that is not `["en"]` or `["en", "es"]`.
- A color that would make text hard to read. Pick a darker primary color or a lighter accent color.
- `sample_data = false` while the name is still the sample's.

### "Cannot read the data"

The file's shape is wrong, so the site could not read a row at all. The message names the file, the line and the column.

| Message says | What to do |
|---|---|
| `a value is required` | A column that must be filled is empty on that line. |
| `not a number` | A number has a comma, a letter or a unit in it. Type `2390`, not `2,390` or `2390 kWh`. |
| `expected YYYY-MM-DD` / `expected YYYY-MM` | A date is in the wrong form. Use `2026-10-22` and `2026-10`. |
| `more values than columns` | The line has too many commas, or a note with a comma in it. Put the note in double quotes, or take the comma out. |
| `unexpected column` / `missing required column` | The first line of the file was changed. Put the column names back exactly as they were. |
| `forbidden column` | A column for dollars or account numbers was added. Remove it. Those never go in these files. |

### Errors: these stop the site until fixed

| Code | What it means | How to fix it |
|---|---|---|
| `UNIT_MISMATCH` | An electricity row does not say `kWh`, or a gas row does not say `therm`. | Correct the `unit` column. If the bill is in CCF, convert it (section 2). |
| `PERIOD_LENGTH` | The period from `period_start` to `period_end` is shorter than 25 days or longer than 35. | Check both dates against the bill. A wrong month or year is the usual slip. |
| `PERIOD_MONTH_MISMATCH` | `period_end_month` is not the month of `period_end`. | Make `period_end_month` the year and month in which the period ends. |
| `PERIOD_PARTIAL` | Only one of the two dates is filled in. | Give both dates, or leave both empty. |
| `PERIOD_OVERLAP` | Two periods on the same meter cover the same days. | Check the dates of the two months named. One period may end on the day the next begins, no more. |
| `DUPLICATE_READING` | Two rows for the same meter and month. | Keep one. To correct a month, change its row; do not add another. |
| `UNKNOWN_METER` | The `meter_id` on a row is not listed in `meters.csv`. | Fix the spelling (`electric-main`, `gas-main`), or add the meter to `meters.csv`. |
| `DUPLICATE_METER` | A meter is listed twice in `meters.csv`. | Remove one of the two lines. |
| `INVALID_VALUE` | A value is not allowed: an amount below zero, a `data_quality` that is not one of `bill`, `chart_estimate`, `chart_measured`, `utility_export`, a margin of error outside 0 to 100, and the like. | The message names the row and the value. Correct it. |
| `DOLLAR_IN_TEXT` | A note or label has a dollar amount in it. | Take it out. Dollars never go in these files. |
| `LONG_DIGITS_IN_TEXT` | A note or label has nine or more digits in a row. It looks like an account number. | Take it out. |
| `UNKNOWN_SUBREGION` | The region code in `parish.toml` (`egrid_subregion`) has no matching line in `data/factors.csv`. The message lists the codes that do. | Check the code against EPA's Power Profiler and correct it in `parish.toml`. Do not edit `factors.csv` to make it fit. |
| `MISSING_FACTOR` | `data/factors.csv` has lost the line for natural gas or for the gasoline-car comparison. | Put the file back as it was (GitHub keeps every earlier version under **History**). |
| `DUPLICATE_FACTOR` | The same factor is listed twice in `data/factors.csv`. | Remove the extra line. |
| `MISSING_SPANISH` | A milestone has no Spanish label, and your site is also in Spanish. | Fill in `label_es` in `milestones.csv`, or set `languages = ["en"]` in `parish.toml`. |
| `SOLAR_LABEL` | A milestone's label says "solar" but its `kind` is not `solar_connected`. | Change the `kind` to `solar_connected`, or reword the label. |
| `DUPLICATE_VEHICLE` | A vehicle is listed twice in `vehicles.csv`. | Remove one line. |
| `UNKNOWN_VEHICLE` | A row in `commute.csv` names a vehicle that is not in `vehicles.csv`. | Fix the spelling of `vehicle_id`, or add the vehicle. |
| `DUPLICATE_COMMUTE` | Two rows in `commute.csv` for the same vehicle and month. | Keep one. To correct a month, change its row. |

### Warnings: the site still updates, but look twice

| Code | What it means | What to do |
|---|---|---|
| `PLAUSIBILITY` | A month is more than 40 percent above or below the same month a year earlier. | Check the number against the bill. If it is right, leave it: a cold winter or a closed building is a real reason. |
| `GAP` | A month is missing between two months you entered. | Add the missing month when you have it. A baseline needs twelve months in a row. |
| `ZERO_QUANTITY` | An amount is zero. | Check the bill. Zero is possible (gas in summer) but rare. |
| `MISSING_UNCERTAINTY` | A `chart_measured` row has no margin of error. | Put your margin, in percent, in `uncertainty_pct`. |
| `FACTOR_NO_SPANISH` | The line for your region in `data/factors.csv` has no Spanish wording, so the Spanish method page shows English there. | Nothing is wrong with your numbers. Tell whoever maintains the template. |

### The content guard

After the site is built, and before it is published, a last check reads every page. If it finds something that must not be public it stops and names the page and a code. It never shows the text it found.

| Code | What it found | Where to look |
|---|---|---|
| `DOLLAR_AMOUNT` | A dollar sign or "USD" followed by a number. | Labels in `meters.csv` and `milestones.csv`, and the wording in `parish.toml`. |
| `LONG_DIGIT_RUN` | Nine or more digits, even with spaces or hyphens between them. It looks like an account or meter number. | The same places. |
| `OFFSET_TERM` | The word "offset" (or Spanish "compensación", "compensar", "compensa"). The site never calls anything an offset. | Reword the label. |
| `FORBIDDEN_WORD` | One of the words you listed under `[privacy]` in `parish.toml`. | The same places. Reword, or take the word off your list if it is there by mistake. |
| `NOT_UTF8` | A file is saved in an unusual text format. | Re-type the last thing you pasted from another program. |

The page name tells you which file to look in: `about/index.html` shows the milestones; `api/v1/organization.json` shows the meter labels and your parish's details; every page shows the wording from `parish.toml`.

### "The program's own tests failed"

Your settings and data passed, but a file other than `parish.toml` or the files in `data/` was changed. Undo that change: open the file, click **History**, and restore the earlier version. If you did not change anything else, ask whoever maintains the template.

## Other things you may want to do

**Add a milestone.** In `data/milestones.csv`, add a row: the date, a `kind` (`boiler`, `solar_connected` or `other`), a short label in English, the same in Spanish, and how much of the date you know (`day`, `month` or `year`). If you only know the year, type January 1 of that year and `year`; the site then shows just the year.

```
2019-01-01,boiler,Boiler replaced (2019),Caldera reemplazada (2019),year
```

**Solar panels.** The site says nothing about solar until you add a milestone with `kind` set to `solar_connected` and the date the system was connected. From then on the About page has a short solar section.

**Church driving in an electric car.** One row per car in `vehicles.csv`: a made-up name, a plain label, the car's energy use in kWh per mile, where it charges (`home` or `church`) and the region code where it charges. Take the energy use from the fuel economy label for the exact model at <https://www.fueleconomy.gov>: the combined kWh per 100 miles, divided by 100. Then one row per month in `commute.csv` with the miles driven on church business. Use `estimate` until you read the odometer, then `odometer`. No trips, places or names. This line is shown on its own and is never added to the buildings' total.

**More than one meter.** Add a line to `meters.csv` with a new made-up name (for example `electric-hall`) and its fuel (`electricity` or `natural_gas`). Then add a row for it every month, like the others. A baseline needs twelve months in a row for every meter listed.

**Your baseline year.** The site first shows the latest twelve months, marked "provisional". Once every bill for the year named under `[baseline]` in `parish.toml` is entered, January to December, the baseline settles on that year.
