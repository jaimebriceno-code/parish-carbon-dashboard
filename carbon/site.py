"""The static website: `render_site` turns a dataset and the parish settings into the pages.

Pages, written under `<out>/` (English) and, when the parish builds in Spanish too, `<out>/es/`:

    index.html, data/index.html, methodology/index.html, about/index.html

plus `<out>/assets/` (the stylesheet with the parish's two brand colours written in, the self-hosted Noto fonts,
the built-in mark and favicon, and the parish's own logo when `parish.toml` names one).

What the built site is
----------------------
Plain HTML and CSS. There are no scripts, no external resources and nothing that needs JavaScript: the
language switch is an ordinary link to the same page in the other language, each page declares its alternate
with `<link rel="alternate" hreflang>`, and each chart is an inline SVG with a captioned data table in the same
section. An English-only site has no `/es/` pages, no language switch and no alternates.

How it is organised
-------------------
`_Facts` computes what the pages need from the dataset once (baseline, priced months, travel results) and holds
the settings. One `_*_view` function per page turns those facts and a language into plain strings, numbers and
lists; the Jinja templates in `templates/` only lay them out and call `t()` for every sentence. Nothing is
hard-coded that belongs to the data or to the parish: the parish's name, place, campus wording and travel label
are placeholders in the string table, filled from the settings on every string; the miles per working day on
the travel card is the travel miles divided by the weekdays in the same months.

Text and the CO2e subscript
---------------------------
The string table writes "CO₂e", but the self-hosted Noto subsets have no U+2082 glyph. In the templates `t()`
returns the escaped string with each "CO₂e" turned into `CO<sub>2</sub>e`; `tp()` returns plain text with
"CO2e" for attributes, `<title>` and the SVG chart. Numbers are formatted the same way in both languages
(1,234.5), matching the strings that already say "100,000 BTU"; only month names and date order differ.

`render_site` refuses to run while `carbon.i18n.parity_errors()` is not empty and writes the same bytes for
the same inputs.

Links and the base path
-----------------------
A site on GitHub Pages usually lives under a path (https://someone.github.io/repo-name/), so every internal
link, asset, font preload and feed link starts with the settings' `path_prefix` ("/repo-name", or "" at a
domain root and for a local build). The stylesheet refers to its fonts relative to itself, so it needs no
prefix. When a base URL is set each page also gets `<link rel="canonical">` and its `hreflang` alternates are
absolute (origin plus prefix plus path), which is what search engines need. With no base URL the links are
root-relative, so `python -m http.server` in the output folder shows the site as it will be.
"""

from __future__ import annotations

import calendar
import re
import shutil
from dataclasses import dataclass
from datetime import date
from html import escape as html_escape
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup, escape

from carbon import i18n
from carbon.calc import (
    EXACT_QUALITIES,
    GRID_FACTOR_PREFIX,
    CommuteResult,
    MonthEmissions,
    Period,
    commute_results,
    factors_in_use,
    monthly_emissions,
    select_baseline,
)
from carbon.charts import _rgb, bar_chart_svg
from carbon.feed import API_DIR, LICENCE_NAME, LICENCE_URL
from carbon.models import MILESTONE_PRECISIONS, Dataset, Milestone
from carbon.settings import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "templates"
STATIC_DIR = REPO_ROOT / "static"
# Only these parts of static/ are website assets (the feed changelog lives there too). The stylesheet is not
# copied but written, with the parish's brand colours in place of the defaults.
ASSET_ENTRIES = ("fonts", "img")
STYLESHEET = "site.css"
MARK_FILE = "mark.svg"  # the built-in mark shown in the header when the parish gives no logo
FAVICON_FILE = "favicon.svg"
HATCH_SHADE = 0.88  # the accent colour's hatch lines are the accent darkened to this share of its brightness

LANG_PREFIX = {"en": "", "es": "/es"}
PAGES = ("home", "data", "methodology", "about")
PAGE_PATHS = {"home": "/", "data": "/data/", "methodology": "/methodology/", "about": "/about/"}
PAGE_FILES = {
    "home": "index.html",
    "data": "data/index.html",
    "methodology": "methodology/index.html",
    "about": "about/index.html",
}
PAGE_TITLE_KEY = {"home": "home_h1", "data": "data_h1", "methodology": "method_h1", "about": "about_h1"}
PAGE_DESCRIPTION_KEY = {
    "home": "home_intro",
    "data": "data_intro",
    "methodology": "method_intro",
    "about": "about_campus_body",
}
NAV_KEY = {"home": "nav_home", "data": "nav_data", "methodology": "nav_methodology", "about": "nav_about"}

FEED_PATH = f"/{API_DIR}/"

TRAVEL_WINDOW_MONTHS = 12
FUEL_ORDER = {"electricity": 0, "natural_gas": 1}
SCOPE_OF_FUEL = {"electricity": 2, "natural_gas": 1}
UNIT_KEY_OF_FUEL = {"electricity": "unit_kwh", "natural_gas": "unit_therms"}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# --- text and number formatting -------------------------------------------------


def page_url(lang: str, page: str, prefix: str = "") -> str:
    """The URL path of `page` in `lang`: "/data/" in English, "/es/data/" in Spanish, after the site's `prefix`
    ("/repo-name/es/data/" for a site served under /repo-name)."""
    return prefix + LANG_PREFIX[lang] + PAGE_PATHS[page]


NBSP = "\u00a0"


def _say(lang: str, key: str, **fmt: object) -> str:
    """The string `key` in `lang`, with the Spanish "80 %" kept on one line (a no-break space before the sign)."""
    return i18n.t(lang, key, **fmt).replace(" %", NBSP + "%")


def _plain(text: str) -> str:
    """Text for attributes, `<title>` and the SVG chart: no U+2082, which the self-hosted fonts do not have."""
    return text.replace("CO₂e", "CO2e")


def _rich(text: str) -> Markup:
    """Escaped text for the page body, with each "CO₂e" set as CO<sub>2</sub>e."""
    return Markup(str(escape(text)).replace("CO₂e", "CO<sub>2</sub>e"))


def _linkifier(contact: str):
    """A template filter that links the contact address and the data license name inside an escaped sentence."""
    shown = html_escape(contact)

    def linkify(value: object) -> Markup:
        text = str(value)
        text = text.replace(shown, f'<a href="mailto:{shown}">{shown}</a>')
        text = text.replace(LICENCE_NAME, f'<a href="{html_escape(LICENCE_URL)}">{html_escape(LICENCE_NAME)}</a>')
        return Markup(text)

    return linkify


def _fmt_int(value: float) -> str:
    return f"{_half_up(value):,}"


def _fmt_fixed(value: float, places: int) -> str:
    return f"{value:,.{places}f}"


def _fmt_trimmed(value: float, places: int = 2) -> str:
    """Up to `places` decimals without trailing zeros: 2,525 and 25.22."""
    text = f"{value:,.{places}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _fmt_tonnes(value: float) -> str:
    """Tonnes for headlines: one decimal, or two when the figure is under one so it never shows as 0.0."""
    return f"{value:,.2f}" if 0 < value < 1 else f"{value:,.1f}"


def _half_up(value: float) -> int:
    """Round half up (80.5 gives 81), unlike the built-in round, which rounds half to even."""
    return int(value + 0.5) if value >= 0 else -int(-value + 0.5)


def _percent(share: float) -> int:
    """A share from 0 to 1 as a whole percent: never 0 for a share above zero, never 100 for one below one."""
    whole = _half_up(100 * share)
    if share > 0:
        whole = max(1, whole)
    if share < 1:
        whole = min(99, whole)
    return whole


def _month_text(lang: str, month: str) -> str:
    """"2025-11" as "Nov 2025" (English) or "nov 2025" (Spanish)."""
    return f"{_say(lang, f'month_short_{month[5:7]}')} {month[:4]}"


def _date_text(lang: str, day: date) -> str:
    """Nov 17, 2026 in English, 17 nov 2026 in Spanish."""
    month = _say(lang, f"month_short_{day.month:02d}")
    return f"{month} {day.day}, {day.year}" if lang == "en" else f"{day.day} {month} {day.year}"


def _milestone_date_text(lang: str, milestone: Milestone) -> str:
    """A milestone date shown only as precisely as it is known: "2021", "Mar 2027" or "Mar 9, 2027"."""
    day = milestone.date
    if milestone.precision == "year":
        return str(day.year)
    if milestone.precision == "month":
        return _month_text(lang, f"{day.year:04d}-{day.month:02d}")
    if milestone.precision == "day":
        return _date_text(lang, day)
    raise ValueError(f"milestone {day}: precision {milestone.precision!r} is not one of {', '.join(MILESTONE_PRECISIONS)}")


def _milestone_datetime(milestone: Milestone) -> str:
    """The machine-readable `<time datetime>` value at the same precision: "2021", "2027-03" or "2027-03-09"."""
    day = milestone.date
    return {"year": f"{day.year:04d}", "month": f"{day.year:04d}-{day.month:02d}"}.get(milestone.precision, day.isoformat())


def _sentence_case(text: str) -> str:
    return text[:1].upper() + text[1:]


def _month_index(month: str) -> int:
    return int(month[:4]) * 12 + int(month[5:7]) - 1


def _month_label(index: int) -> str:
    year, month = divmod(index, 12)
    return f"{year:04d}-{month + 1:02d}"


def _weekdays(month: str) -> int:
    """Monday to Friday days in the calendar month "YYYY-MM"."""
    year, number = int(month[:4]), int(month[5:7])
    first_weekday, days = calendar.monthrange(year, number)  # Monday is 0
    return sum(1 for offset in range(days) if (first_weekday + offset) % 7 < 5)


# --- the facts every page draws on ----------------------------------------------


class _Facts:
    """What the pages need from the dataset and the settings, computed once. Independent of language."""

    def __init__(self, ds: Dataset, settings: Settings):
        self.ds = ds
        self.settings = settings
        self.baseline: Period | None = select_baseline(ds)
        self.priced: list[MonthEmissions] = monthly_emissions(ds)
        # With travel switched off in parish.toml the site has no travel line at all, whatever the data holds.
        self.travel: list[CommuteResult] = commute_results(ds) if settings.travel_enabled else []
        self.uncertainty = {(r.meter_id, r.period_end_month): r.uncertainty_pct for r in ds.readings}
        self.solar_milestones = sorted((m for m in ds.milestones if m.kind == "solar_connected"), key=lambda m: m.date)
        self._words = {lang: settings.words(lang) for lang in settings.languages}

    def say(self, lang: str, key: str, **fmt: object) -> str:
        """The string `key` in `lang`, with the parish's own words filled in as well as `fmt`."""
        return _say(lang, key, **{**self._words[lang], **fmt})


# --- Home ----------------------------------------------------------------------


def _baseline_view(lang: str, facts: _Facts, baseline: Period) -> dict:
    def t(key: str, **fmt: object) -> str:
        return facts.say(lang, key, **fmt)

    months = list(baseline.months)
    by_month = {m: {"electricity": 0.0, "natural_gas": 0.0, "kwh": 0.0, "therms": 0.0, "estimated": False} for m in months}
    for row in facts.priced:
        slot = by_month.get(row.period_end_month)
        if slot is None:
            continue
        slot[row.fuel] += row.tco2e
        slot["kwh" if row.fuel == "electricity" else "therms"] += row.quantity
        if row.data_quality not in EXACT_QUALITIES:
            slot["estimated"] = True

    labels = []
    for i, month in enumerate(months):
        short = t(f"month_short_{month[5:7]}")
        labels.append(f"{short}\n{month[:4]}" if i == 0 or month.endswith("-01") else short)
    chart = bar_chart_svg(
        _plain(t("chart_title_monthly")),
        _plain(t("chart_desc_monthly")),
        labels,
        {
            _plain(t("fuel_electricity")): [by_month[m]["electricity"] for m in months],
            _plain(t("fuel_gas")): [by_month[m]["natural_gas"] for m in months],
        },
        _plain(t("chart_y_axis")),
        estimated=[by_month[m]["estimated"] for m in months],
        no_data_text=_plain(t("chart_no_data")),
        primary=facts.settings.primary_color,
        accent=facts.settings.accent_color,
    )

    total = baseline.tco2e_total
    share_gas = _half_up(100 * baseline.tco2e_gas / total) if total > 0 else 0
    share_electricity = 100 - share_gas if total > 0 else 0

    calendar_year = baseline.label == str(facts.ds.baseline_year)
    period = baseline.label if calendar_year else t("period_range", first=_month_text(lang, months[0]), last=_month_text(lang, months[-1]))
    eyebrow = t("home_eyebrow_provisional" if baseline.provisional else "home_eyebrow_final", period=period)

    estimated_pct = _percent(baseline.estimated_share)
    if baseline.provisional and baseline.estimated_share > 0:
        badge = t("home_badge_provisional_estimated", pct=estimated_pct)
    elif baseline.provisional:
        badge = t("home_badge_provisional")
    elif baseline.estimated_share > 0:
        badge = t("home_badge_estimated", pct=estimated_pct)
    else:
        badge = t("home_badge_final")

    reasons = []
    if baseline.provisional:
        if baseline.estimated_share > 0:
            reasons.append("home_why_estimates")
        if not calendar_year:
            reasons.append("home_why_window")

    rows = [
        {
            "month": _month_text(lang, m),
            "kwh": _fmt_int(by_month[m]["kwh"]),
            "therms": _fmt_int(by_month[m]["therms"]),
            "tco2e": _fmt_fixed(by_month[m]["electricity"] + by_month[m]["natural_gas"], 2),
            "estimated": by_month[m]["estimated"],
        }
        for m in months
    ]
    return {
        "eyebrow": eyebrow,
        "badge": badge,
        "reasons": reasons,
        "total": _fmt_tonnes(total),
        "gas_t": _fmt_tonnes(baseline.tco2e_gas),
        "electricity_t": _fmt_tonnes(baseline.tco2e_electricity),
        "therms": _fmt_int(baseline.gas_therms),
        "kwh": _fmt_int(baseline.electricity_kwh),
        "share_gas": share_gas,
        "share_electricity": share_electricity,
        "split_label": t("home_split_label", gas=share_gas, electricity=share_electricity),
        "chart": Markup(chart),  # built by carbon.charts, which escapes every string it is given
        "rows": rows,
        "total_row": {
            "kwh": _fmt_int(baseline.electricity_kwh),
            "therms": _fmt_int(baseline.gas_therms),
            "tco2e": _fmt_fixed(total, 2),
        },
    }


def _travel_window(facts: _Facts) -> list[str]:
    """The twelve months the travel card covers: the baseline's, else the twelve ending at the latest entry."""
    if facts.baseline is not None:
        return list(facts.baseline.months)
    last = _month_index(max(r.period_end_month for r in facts.travel))
    return [_month_label(i) for i in range(last - TRAVEL_WINDOW_MONTHS + 1, last + 1)]


def _travel_view(lang: str, facts: _Facts) -> dict | None:
    """The numbers for the travel card, or None when travel is switched off or the dataset has no commute rows."""
    if not facts.travel:
        return None

    def t(key: str, **fmt: object) -> str:
        return facts.say(lang, key, **fmt)

    window = _travel_window(facts)
    inside = [r for r in facts.travel if r.period_end_month in window]
    basis = inside or facts.travel
    estimate_rows = [r for r in basis if r.data_quality != "odometer"]
    counted_here = [r for r in basis if not r.counted_in_campus]

    miles = sum(r.miles for r in inside)
    charging = sum(r.tco2e_counted_here for r in inside if not r.counted_in_campus)
    avoided = sum(r.avoided_tco2e for r in inside)
    approx = "~" if estimate_rows else ""

    miles_per_day = None
    if estimate_rows:
        # Estimated miles over the weekdays of the months they cover, so the sentence always matches the data.
        estimated_inside = [r for r in inside if r.data_quality != "odometer"] or estimate_rows
        weekdays = sum(_weekdays(r.period_end_month) for r in estimated_inside)
        miles_per_day = _fmt_int(sum(r.miles for r in estimated_inside) / weekdays)

    ev_kwh = sum(r.ev_kwh for r in inside)
    if miles > 0:
        kwh_per_100_miles = 100 * ev_kwh / miles
    else:
        kwh_per_100_miles = 100 * facts.ds.vehicles[0].kwh_per_mile if facts.ds.vehicles else 0.0

    months_entered = len({r.period_end_month for r in inside})
    return {
        "miles": approx + _fmt_int(miles),
        "quality": t("travel_dq_estimate" if estimate_rows else "travel_dq_odometer"),
        "charging": f"{approx}{_fmt_tonnes(charging)} t" if counted_here else None,
        "avoided": f"{approx}{_fmt_tonnes(avoided)} t" if avoided > 0 else None,
        "kwh100": f"{kwh_per_100_miles:.1f}",
        "miles_per_day": miles_per_day,
        "months_entered": months_entered,
        "partial": months_entered < TRAVEL_WINDOW_MONTHS,
        "charges_here": bool(counted_here),
        "charges_at_church": any(r.counted_in_campus for r in basis),
    }


def _home_view(lang: str, facts: _Facts) -> dict:
    return {
        "baseline": _baseline_view(lang, facts, facts.baseline) if facts.baseline is not None else None,
        "travel": _travel_view(lang, facts),
    }


# --- Data ----------------------------------------------------------------------


def _source_text(lang: str, row: MonthEmissions, facts: _Facts) -> str:
    text = _say(lang, f"dq_{row.data_quality}")
    pct = facts.uncertainty.get((row.meter_id, row.period_end_month))
    if pct is not None:
        text += ", " + _say(lang, "dq_uncertainty", pct=_fmt_trimmed(pct, 1))
    return text


def _data_view(lang: str, facts: _Facts) -> dict:
    rows = []
    for row in sorted(facts.priced, key=lambda r: (r.period_end_month, FUEL_ORDER.get(r.fuel, 9), r.meter_id)):
        rows.append(
            {
                "month": _month_text(lang, row.period_end_month),
                "fuel": _say(lang, "fuel_electricity" if row.fuel == "electricity" else "fuel_gas"),
                "quantity": _fmt_trimmed(row.quantity, 2),
                "unit": _say(lang, UNIT_KEY_OF_FUEL[row.fuel]),
                "scope": SCOPE_OF_FUEL[row.fuel],
                "tco2e": _fmt_fixed(row.tco2e, 3),
                "source": _source_text(lang, row, facts),
            }
        )
    travel_rows = []
    for r in sorted(facts.travel, key=lambda r: (r.period_end_month, r.vehicle_id)):
        travel_rows.append(
            {
                "month": _month_text(lang, r.period_end_month),
                "miles": _fmt_trimmed(r.miles, 1),
                "charging": _say(lang, "travel_cell_already_counted")
                if r.counted_in_campus
                else _fmt_fixed(r.tco2e_counted_here, 3),
                "avoided": _fmt_fixed(r.avoided_tco2e, 3),
                "source": _sentence_case(_say(lang, "travel_dq_odometer" if r.data_quality == "odometer" else "travel_dq_estimate")),
            }
        )
    return {"rows": rows, "travel_rows": travel_rows, "has_travel": bool(facts.travel)}


# --- Methodology ---------------------------------------------------------------


def _factor_name(lang: str, factor_id: str) -> str:
    """A reader's name for a factor: every electricity_<subregion> row is the regional grid, with its code."""
    if factor_id.startswith(GRID_FACTOR_PREFIX):
        return _say(lang, "factor_name_electricity", code=factor_id[len(GRID_FACTOR_PREFIX):].upper())
    key = f"factor_name_{factor_id}"
    return _say(lang, key) if key in i18n.STRINGS[lang] else factor_id


def _methodology_view(lang: str, facts: _Facts) -> dict:
    has_travel = bool(facts.travel)
    factors = []
    # Only the factors this parish uses: its own grid subregion, natural gas and, with travel data, the
    # gasoline-car comparison (which serves nothing else). Other subregions' rows in the file are not shown.
    for factor in factors_in_use(facts.ds, travel=has_travel):
        factors.append(
            {
                "name": _factor_name(lang, factor.factor_id),
                "value": f"{factor.value:.10g}",
                "unit": factor.unit,
                # The Spanish page shows the Spanish wording when the data has it, the English text otherwise.
                "edition": (factor.edition_es if lang == "es" else "") or factor.edition,
                "revision": (factor.revision_es if lang == "es" else "") or factor.revision,
                "retrieved": factor.retrieved,
                "url": factor.url,
            }
        )
    return {"factors": factors, "has_travel": has_travel}


# --- About ---------------------------------------------------------------------


def _about_view(lang: str, facts: _Facts) -> dict:
    milestones = []
    for m in sorted(facts.ds.milestones, key=lambda m: (m.date, m.kind, m.label_en)):
        milestones.append(
            {
                "iso": _milestone_datetime(m),
                "date": _milestone_date_text(lang, m),
                "label": m.label_en if lang == "en" else m.label_es,
            }
        )
    solar = None
    if facts.solar_milestones:  # the earliest one; the validator keeps the word off every other milestone
        first = facts.solar_milestones[0]
        solar = {"date": _milestone_date_text(lang, first), "exact_day": first.precision == "day"}
    return {"milestones": milestones, "solar": solar, "website": facts.settings.website, "name": facts.settings.name}


# --- rendering -----------------------------------------------------------------


@dataclass(frozen=True)
class _Renderer:
    env: Environment
    facts: _Facts
    generated_at: str
    logo_file: str  # the file name under assets/img/ shown in the header

    def render(self, lang: str, page: str) -> str:
        settings = self.facts.settings
        prefix = settings.path_prefix

        def rich(key: str, **fmt: object) -> Markup:
            return _rich(self.facts.say(lang, key, **fmt))

        def plain(key: str, **fmt: object) -> str:
            return _plain(self.facts.say(lang, key, **fmt))

        views = {
            "home": _home_view,
            "data": _data_view,
            "methodology": _methodology_view,
            "about": _about_view,
        }
        context = {
            "lang": lang,
            "page": page,
            "languages": settings.languages,
            "t": rich,
            "tp": plain,
            "title": plain("page_title", page=plain(PAGE_TITLE_KEY[page]), site=plain("site_name")),
            "description": plain(PAGE_DESCRIPTION_KEY[page]),
            # Paths from the web root, the site's own prefix included; `origin` in front makes them absolute.
            "urls": {code: page_url(code, page, prefix) for code in settings.languages},
            "origin": settings.origin,
            "root": prefix,
            "home_url": page_url(lang, "home", prefix),
            "nav": [
                {"key": NAV_KEY[name], "url": page_url(lang, name, prefix), "current": name == page} for name in PAGES
            ],
            "contact": settings.contact_email,
            "license_name": LICENCE_NAME,
            "feed": prefix + FEED_PATH,
            "logo": f"{prefix}/assets/img/{self.logo_file}",
            "favicon": f"{prefix}/assets/img/{FAVICON_FILE}",
            "theme_color": settings.primary_color,
            "sample_data": settings.sample_data,
            "updated": _date_text(lang, date.fromisoformat(self.generated_at)),
            "v": views[page](lang, self.facts),
        }
        return self.env.get_template(f"{page}.html.j2").render(**context)


def _environment(settings: Settings) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters["linkify"] = _linkifier(settings.contact_email)
    return env


def _check_generated_at(generated_at: str) -> None:
    problem = f"generated_at must be a YYYY-MM-DD date string, got {generated_at!r}"
    if not isinstance(generated_at, str) or not _DATE_RE.match(generated_at):
        raise ValueError(problem)
    try:
        date.fromisoformat(generated_at)  # also rejects impossible dates such as 2026-13-01
    except ValueError:
        raise ValueError(problem) from None


# --- assets --------------------------------------------------------------------

_BRAND_TOKEN_RE = re.compile(r"(--(primary|accent|hatch):\s*)#[0-9A-Fa-f]{6}(\s*;)")


def hatch_colour(accent: str) -> str:
    """The accent colour, a little darker, for the hatch lines drawn over it in the swatches and the split bar."""
    return "#" + "".join(f"{int(channel * HATCH_SHADE + 0.5):02X}" for channel in _rgb(accent))


def brand_stylesheet(css: str, settings: Settings) -> str:
    """`static/site.css` with the three brand tokens in `:root` set from the settings. Nothing else changes.

    Raises ValueError if the stylesheet no longer defines each token exactly once, so a refactor of the CSS
    cannot silently stop the parish's colours from reaching the pages.
    """
    values = {
        "primary": settings.primary_color,
        "accent": settings.accent_color,
        "hatch": hatch_colour(settings.accent_color),
    }
    found = [match.group(2) for match in _BRAND_TOKEN_RE.finditer(css)]
    if sorted(found) != sorted(values):
        raise ValueError(
            f"static/{STYLESHEET} must define --primary, --accent and --hatch once each as six-digit hex colours; "
            f"found {found}"
        )
    return _BRAND_TOKEN_RE.sub(lambda m: f"{m.group(1)}{values[m.group(2)]}{m.group(3)}", css)


def _logo_file(settings: Settings) -> str:
    """The name the header image has under assets/img/: the parish's logo as `logo.<type>`, else the built-in mark."""
    return f"logo{settings.logo.suffix.lower()}" if settings.logo is not None else MARK_FILE


def _copy_assets(out_dir: Path, settings: Settings) -> None:
    target = out_dir / "assets"
    target.mkdir(parents=True, exist_ok=True)
    for name in ASSET_ENTRIES:
        shutil.copytree(STATIC_DIR / name, target / name, dirs_exist_ok=True, copy_function=shutil.copyfile)
    css = (STATIC_DIR / STYLESHEET).read_text(encoding="utf-8")
    with (target / STYLESHEET).open("w", encoding="utf-8", newline="") as handle:  # exact bytes on every system
        handle.write(brand_stylesheet(css, settings))
    if settings.logo is not None:
        shutil.copyfile(settings.logo, target / "img" / _logo_file(settings))


def render_site(ds: Dataset, out_dir: Path, generated_at: str, settings: Settings) -> None:
    """Write the four pages in each of the parish's languages, and the assets, under `out_dir` (created if needed).

    `generated_at` is the "Last updated" date (YYYY-MM-DD); it is passed in so the build is reproducible.
    `settings` supplies everything about the parish: its words, colours, languages, whether travel is shown,
    and the base URL (see the module text on links). Assumes `ds` has passed `carbon.validate`. A dataset with
    no complete baseline, or none at all, still builds every page. Raises ValueError for a bad `generated_at`
    or base URL, or while the two string tables disagree.
    """
    _check_generated_at(generated_at)
    settings.origin  # raises ValueError now for a base URL that cannot be used, before anything is written
    problems = i18n.parity_errors()
    if problems:
        raise ValueError("English and Spanish strings disagree: " + "; ".join(problems))

    out_dir = Path(out_dir)
    renderer = _Renderer(_environment(settings), _Facts(ds, settings), generated_at, _logo_file(settings))
    for lang in settings.languages:
        base = out_dir / LANG_PREFIX[lang].strip("/")
        for page in PAGES:
            path = base / PAGE_FILES[page]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(renderer.render(lang, page), encoding="utf-8")
    _copy_assets(out_dir, settings)
