"""Tests for the static site generator (carbon.site.render_site), the templates and static/site.css.

The site is built into a temporary folder and read back with BeautifulSoup, so the tests check what a visitor
and a screen reader get, not how the Python is arranged. The built pages are pure HTML and CSS: no scripts, no
external resources, nothing that needs JavaScript.
"""

import calendar
import re
from dataclasses import replace
from datetime import date, timedelta
from html import unescape
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

import carbon.site as site
from carbon.calc import commute_results, select_baseline
from carbon.charts import DEFAULT_ACCENT, DEFAULT_PRIMARY, INK, MUTED, contrast_ratio
from carbon.i18n import STRINGS, offset_terms_outside_allowed
from carbon.i18n import t as _table_text
from carbon.models import (
    CommuteMonth,
    Dataset,
    Factor,
    Meter,
    Milestone,
    Reading,
    Vehicle,
)
from carbon.settings import NEUTRALS, brand_contrast_problems
from carbon.site import render_site
from carbon.validate import validate_dataset
from tests.support import (
    CONTACT,
    ORG_NAME,
    ROOT,
    SAMPLE_MILES_PER_DAY,
    SETTINGS,
    TRAVEL_EN,
    TRAVEL_ES,
    sample_dataset,
    settings,
)

CSS_PATH = ROOT / "static" / "site.css"


def t(lang, key, **fmt):
    """The string as the sample parish's pages say it: its own words filled in, then `fmt`."""
    return _table_text(lang, key, **{**SETTINGS.words(lang), **fmt})


def factor_name(lang, factor_id):
    return site._factor_name(lang, factor_id)

GENERATED_AT = "2026-11-17"

LANGS = ("en", "es")
PAGES = ("home", "data", "methodology", "about")
FILES = {
    "home": "index.html",
    "data": "data/index.html",
    "methodology": "methodology/index.html",
    "about": "about/index.html",
}
URLS = {
    "en": {"home": "/", "data": "/data/", "methodology": "/methodology/", "about": "/about/"},
    "es": {"home": "/es/", "data": "/es/data/", "methodology": "/es/methodology/", "about": "/es/about/"},
}


# --- synthetic datasets ---------------------------------------------------------


def _months(first, count):
    year, month = int(first[:4]), int(first[5:7])
    out = []
    for _ in range(count):
        out.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def _weekdays(month):
    """Monday to Friday days in the calendar month "YYYY-MM", counted independently of the site code."""
    year, number = int(month[:4]), int(month[5:7])
    day, count = date(year, number, 1), 0
    while day.month == number:
        count += day.weekday() < 5
        day += timedelta(days=1)
    return count


def _factor(factor_id, value, unit, edition, revision, url, retrieved):
    return Factor(factor_id, value, unit, "CO2, CH4, N2O", "test source text", edition, revision, url, retrieved)


FACTORS = [
    _factor("electricity_rfcw", 0.4, "kg CO2e/kWh", "eGRID test edition", "Rev test A", "https://example.org/egrid.pdf", "2026-01-02"),
    _factor("natural_gas", 5.3, "kg CO2e/therm", "Hub test edition", "Rev test B", "https://example.org/hub.pdf", "2026-01-03"),
    _factor("gasoline_car_per_mile", 0.3, "kg CO2e/mile", "Hub test edition", "Rev test C", "https://example.org/car.pdf", "2026-01-04"),
]
METERS = [
    Meter("electric-main", "electricity", "Campus electricity"),
    Meter("gas-main", "natural_gas", "Campus natural gas"),
]
BOILER = Milestone(date(2021, 1, 1), "boiler", "Boiler installed (2021)", "Caldera instalada (2021)", "year")


def _dataset(*, first="2025-11", count=12, quality=None, vehicles=(), commute=(), milestones=(BOILER,)):
    """Both meters for `count` billing months. `quality(i, fuel)` picks each reading's data quality."""
    quality = quality or (lambda i, fuel: "bill")
    readings = []
    for i, month in enumerate(_months(first, count)):
        readings.append(Reading("electric-main", month, 1000.0 + 10 * i, "kWh", quality(i, "electricity")))
        measured = quality(i, "gas") == "chart_measured"
        readings.append(
            Reading(
                "gas-main",
                month,
                100.0 + 3.5 * i,
                "therm",
                quality(i, "gas"),
                uncertainty_pct=5.0 if measured else None,
            )
        )
    return Dataset(
        meters=list(METERS),
        readings=readings,
        milestones=list(milestones),
        factors=list(FACTORS),
        vehicles=list(vehicles),
        commute=list(commute),
        grid_subregion="RFCW",
    )


def _estimates_first(n):
    return lambda i, fuel: "chart_estimate" if i < n else "bill"


def _commute_rows(vehicle_id, per_day, *, first="2025-11", count=12, quality="estimate"):
    return [CommuteMonth(vehicle_id, m, per_day * _weekdays(m), quality) for m in _months(first, count)]


HOME_EV = Vehicle("ev-home", "Test EV", 0.33, "home", "RFCW")
CHURCH_EV = Vehicle("ev-church", "Test EV", 0.33, "church", "RFCW")


def _with_travel(per_day=80, vehicle=HOME_EV, **kw):
    return _dataset(vehicles=[vehicle], commute=_commute_rows(vehicle.vehicle_id, per_day, **kw), quality=_estimates_first(6))


SOLAR = Milestone(date(2027, 3, 9), "solar_connected", "Solar system connected", "Sistema solar conectado")


# --- building and reading pages -------------------------------------------------


def _build(tmp_path_factory, ds, generated_at=GENERATED_AT, which=SETTINGS):
    out = tmp_path_factory.mktemp("site")
    render_site(ds, out, generated_at, which)
    return out


@pytest.fixture(scope="module")
def seed_site(tmp_path_factory):
    """The frozen sample parish (the fixture names are kept from when the template was one church's seed data)."""
    return _build(tmp_path_factory, sample_dataset())


@pytest.fixture(scope="module")
def seed_no_travel_site(tmp_path_factory):
    """The sample with its vehicle and travel entries taken out."""
    return _build(tmp_path_factory, replace(sample_dataset(), vehicles=[], commute=[]))


@pytest.fixture(scope="module")
def travel_site(tmp_path_factory):
    return _build(tmp_path_factory, _with_travel())


@pytest.fixture(scope="module")
def empty_site(tmp_path_factory):
    return _build(tmp_path_factory, Dataset())


def _path(out, lang, page):
    return Path(out) / ("es" if lang == "es" else "") / FILES[page]


def _html(out, lang, page):
    return _path(out, lang, page).read_text(encoding="utf-8")


def _soup(out, lang, page):
    return BeautifulSoup(_html(out, lang, page), "html.parser")


def _text(node):
    """Visible text with whitespace collapsed and the CO<sub>2</sub>e subscript read back as CO₂e."""
    text = node.get_text(" ", strip=True)
    text = re.sub(r"CO\s2\se", "CO₂e", text)
    return re.sub(r"\s+", " ", text)


def _all_pages(out):
    for lang in LANGS:
        for page in PAGES:
            yield lang, page, _soup(out, lang, page)


def _section_for(soup, heading_text):
    for heading in soup.find_all("h2"):
        if _text(heading) == heading_text:
            return heading.find_parent("section")
    return None


# --- structure of every page ----------------------------------------------------


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("page", PAGES)
def test_every_page_has_lang_title_single_h1_and_skip_link(seed_site, lang, page):
    soup = _soup(seed_site, lang, page)
    assert soup.html["lang"] == lang
    title = soup.title.get_text(strip=True)
    assert title and t(lang, "site_name") in title
    assert len(soup.find_all("h1")) == 1
    assert soup.find("h1").get_text(strip=True)
    skip = soup.find("a", class_="skip")
    assert skip is not None and skip["href"] == "#main"
    assert skip.get_text(strip=True) == t(lang, "skip_to_content")
    assert soup.find("a") is skip, "the skip link must be the first link on the page"
    main = soup.find("main")
    assert main is not None and main["id"] == "main"
    assert soup.find("meta", attrs={"name": "viewport"})
    assert soup.find("meta", attrs={"name": "description"})["content"].strip()


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("page", PAGES)
def test_every_page_links_the_stylesheet_and_declares_both_languages(seed_site, lang, page):
    soup = _soup(seed_site, lang, page)
    sheets = soup.find_all("link", rel="stylesheet")
    assert [s["href"] for s in sheets] == ["/assets/site.css"]
    alternates = {a["hreflang"]: a["href"] for a in soup.find_all("link", rel="alternate")}
    assert alternates["en"] == URLS["en"][page]
    assert alternates["es"] == URLS["es"][page]


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("page", PAGES)
def test_language_switch_and_navigation_are_plain_links_to_real_pages(seed_site, lang, page):
    soup = _soup(seed_site, lang, page)
    other = "es" if lang == "en" else "en"
    group = soup.find(class_="lang")
    assert group["aria-label"] == t(lang, "lang_switch_label")
    link = group.find("a")
    assert link["href"] == URLS[other][page]
    assert link["hreflang"] == other and link["lang"] == other
    assert link.get_text(strip=True) == t(other, f"lang_name_{other}")
    current = group.find(attrs={"aria-current": True})
    assert current.get_text(strip=True) == t(lang, f"lang_name_{lang}")
    assert group.find("button") is None

    nav = soup.find("nav", attrs={"aria-label": t(lang, "nav_label")})
    hrefs = {a["href"]: a for a in nav.find_all("a")}
    assert set(hrefs) == set(URLS[lang].values())
    for name, url in URLS[lang].items():
        assert hrefs[url].get_text(strip=True) == t(lang, f"nav_{name}")
        assert (hrefs[url].get("aria-current") == "page") == (name == page)


def test_the_last_updated_date_comes_from_generated_at_on_every_page(tmp_path_factory):
    out = _build(tmp_path_factory, _dataset(), generated_at="2026-12-18")
    for lang, page, soup in _all_pages(out):
        expected = "Dec 18, 2026" if lang == "en" else "18 dic 2026"
        assert t(lang, "updated_on", date=expected) in _text(soup), f"{lang}/{page}"


def test_footer_has_contact_and_the_data_license_with_a_link(seed_site):
    for lang, page, soup in _all_pages(seed_site):
        footer = _text(soup.find("footer"))
        assert CONTACT in footer
        assert "CC BY 4.0" in footer
        assert soup.find("footer").find("a", href=f"mailto:{CONTACT}")
        assert f"{ORG_NAME} · Springfield, IL 62701" in footer


# --- home: baseline label and numbers --------------------------------------------


def _hero(soup):
    return soup.select_one("section.hero")


def test_home_labels_baseline_provisional_and_estimated(tmp_path_factory):
    ds = _dataset(quality=_estimates_first(6))
    baseline = select_baseline(ds)
    assert baseline.provisional and 0 < baseline.estimated_share < 1
    pct = round(100 * baseline.estimated_share)
    out = _build(tmp_path_factory, ds)
    en, es = _soup(out, "en", "home"), _soup(out, "es", "home")

    assert _text(_hero(en).select_one(".badge")) == f"Provisional: {pct}% estimated"
    assert _text(_hero(es).select_one(".badge")) == f"Provisional: {pct} % estimado"
    assert _text(_hero(en).select_one(".eyebrow")) == "Provisional baseline · Nov 2025 to Oct 2026"
    assert _text(_hero(es).select_one(".eyebrow")) == "Línea base provisional · nov 2025 a oct 2026"
    en_text = _text(en.find("main"))
    assert t("en", "home_why_estimates") in en_text
    assert t("en", "home_why_window") in en_text
    assert t("es", "home_why_estimates") in _text(es.find("main"))


@pytest.mark.parametrize(
    "case, reasons",
    [("both", 2), ("estimates_only", 1), ("window_only", 1)],
)
def test_why_provisional_lead_appears_once_per_home_page_with_the_reasons_as_items(tmp_path_factory, case, reasons):
    ds = {
        "both": _dataset(quality=_estimates_first(6)),
        "estimates_only": _dataset(first="2025-01", quality=_estimates_first(3)),  # calendar 2025, estimates remain
        "window_only": _dataset(),  # latest twelve months, all exact
    }[case]
    out = _build(tmp_path_factory, ds)
    for lang in LANGS:
        main = _soup(out, lang, "home").find("main")
        text = _text(main)
        assert text.count(t(lang, "home_why_lead")) == 1, f"{lang}/{case}"
        callout = main.find(class_="after-hero")
        items = [_text(li) for li in callout.find_all("li")]
        assert len(items) == reasons
        wanted = {"both": ("home_why_estimates", "home_why_window"), "estimates_only": ("home_why_estimates",), "window_only": ("home_why_window",)}[case]
        assert items == [t(lang, key) for key in wanted]
        assert callout.find("p").get_text(strip=True) == t(lang, "home_why_lead")


def test_home_provisional_with_exact_figures_says_so(tmp_path_factory):
    ds = _dataset()  # latest twelve months, all exact: provisional only because the window is not calendar 2025
    baseline = select_baseline(ds)
    assert baseline.provisional and baseline.estimated_share == 0
    out = _build(tmp_path_factory, ds)
    for lang in LANGS:
        soup = _soup(out, lang, "home")
        assert _text(_hero(soup).select_one(".badge")) == t(lang, "home_badge_provisional")
        text = _text(soup.find("main"))
        assert t(lang, "home_why_window") in text
        assert t(lang, "home_why_estimates") not in text


def test_home_final_baseline_is_labelled_final_with_no_reason_note(tmp_path_factory):
    ds = _dataset(first="2025-01")
    baseline = select_baseline(ds)
    assert baseline.label == "2025" and not baseline.provisional
    out = _build(tmp_path_factory, ds)
    for lang in LANGS:
        soup = _soup(out, lang, "home")
        assert _text(_hero(soup).select_one(".badge")) == t(lang, "home_badge_final")
        assert _text(_hero(soup).select_one(".eyebrow")) == t(lang, "home_eyebrow_final", period="2025")
        text = _text(soup.find("main"))
        assert "Why provisional" not in text and "¿Por qué provisional" not in text


def test_home_shows_an_estimated_badge_when_not_provisional_but_estimates_remain(tmp_path_factory, monkeypatch):
    ds = _dataset(first="2025-01", quality=_estimates_first(3))
    real = select_baseline(ds)
    assert real.label == "2025"
    fake = replace(real, provisional=False)  # a non-provisional baseline that still contains estimates
    monkeypatch.setattr(site, "select_baseline", lambda _ds: fake)
    out = _build(tmp_path_factory, ds)
    pct = round(100 * real.estimated_share)
    assert _text(_hero(_soup(out, "en", "home")).select_one(".badge")) == f"{pct}% estimated"
    assert _text(_hero(_soup(out, "es", "home")).select_one(".badge")) == f"{pct} % estimado"
    assert "Provisional" not in _text(_hero(_soup(out, "en", "home")))


def test_home_total_split_and_tiles_match_the_calculation(seed_site):
    baseline = select_baseline(sample_dataset())
    hero = _hero(_soup(seed_site, "en", "home"))
    assert _text(hero.select_one(".big")) == f"{baseline.tco2e_total:.1f} tonnes CO₂e"
    gas_share = round(100 * baseline.tco2e_gas / baseline.tco2e_total)
    assert hero.select_one(".split")["aria-label"] == t("en", "home_split_label", gas=gas_share, electricity=100 - gas_share)
    tiles = _text(hero)
    assert f"{baseline.gas_therms:,.0f} therms" in tiles
    assert f"{baseline.electricity_kwh:,.0f} kWh" in tiles
    assert f"{baseline.tco2e_gas:.1f} t" in tiles and f"{baseline.tco2e_electricity:.1f} t" in tiles


def test_empty_dataset_builds_and_says_no_complete_baseline_yet(empty_site):
    for lang in LANGS:
        for page in PAGES:
            assert _path(empty_site, lang, page).is_file(), f"{lang}/{page}"
        home = _soup(empty_site, lang, "home")
        text = _text(home.find("main"))
        assert t(lang, "home_nobaseline_heading") in text
        assert t(lang, "home_nobaseline_body") in text
        assert home.select_one("section.hero") is None
        assert len(home.find_all("h1")) == 1
        assert not re.search(r"\b(None|nan|undefined)\b|\{\{|\{%", text)
        assert t(lang, "data_none_yet") in _text(_soup(empty_site, lang, "data").find("main"))


def test_a_partial_dataset_with_no_complete_window_still_builds(tmp_path_factory):
    out = _build(tmp_path_factory, _dataset(count=7))
    for lang in LANGS:
        home = _soup(out, lang, "home")
        assert t(lang, "home_nobaseline_heading") in _text(home)
        assert len(_soup(out, lang, "data").select("tbody tr")) == 14  # both fuels, every month entered so far


# --- charts and tables -----------------------------------------------------------


def test_each_svg_is_followed_by_a_captioned_table(seed_site, travel_site):
    seen = 0
    for out in (seed_site, travel_site):
        for lang, page, soup in _all_pages(out):
            for svg in soup.find_all("svg"):
                seen += 1
                section = svg.find_parent("section")
                table = svg.find_next("table")
                assert table is not None, f"{lang}/{page}: an SVG with no table after it"
                assert table.find_parent("section") is section, f"{lang}/{page}: the table is in another section"
                caption = table.find("caption")
                assert caption is not None and caption.get_text(strip=True), f"{lang}/{page}: table without caption"
                assert svg.find("title") and svg.find("desc")
    assert seen >= 4  # at least the Home chart in each language, in both builds


def test_home_chart_matches_the_baseline_months_and_marks_estimates(tmp_path_factory):
    ds = _dataset(quality=_estimates_first(6))
    out = _build(tmp_path_factory, ds)
    soup = _soup(out, "en", "home")
    section = soup.find("section", id="monthly")
    svg = section.find("svg")
    bars = svg.find_all("g", class_="bar")
    assert len(bars) == 12
    dashed = [bool(g.find("rect", attrs={"stroke-dasharray": True})) for g in bars]
    assert dashed == [True] * 6 + [False] * 6  # the six chart-estimate months are dashed
    assert svg.find("title").get_text() == t("en", "chart_title_monthly")
    rows = section.find("table").select("tbody tr")
    assert len(rows) == 13  # twelve months and the total row
    labels = [_text(r.find("th")) for r in rows]
    assert labels[0] == "Nov 2025" and labels[11] == "Oct 2026" and labels[12] == t("en", "row_total")
    sources = [_text(r.find_all("td")[-1]) for r in rows[:12]]
    assert sources == [t("en", "dq_estimated")] * 6 + [t("en", "dq_exact")] * 6
    assert section.find(class_="legend")


def test_chart_text_uses_plain_ascii_co2e_and_has_a_scroll_container_and_swipe_hint(seed_site):
    for lang in LANGS:
        soup = _soup(seed_site, lang, "home")
        svg = soup.find("svg")
        assert "t CO2e" in svg.get_text()
        wrap = svg.find_parent(class_="chartwrap")
        assert wrap is not None
        assert wrap.get("tabindex") == "0" and wrap.get("role") == "region"  # keyboard reachable when it scrolls
        swipe = soup.find(class_="swipe")
        assert _text(swipe) == t(lang, "home_swipe")
    css = CSS_PATH.read_text(encoding="utf-8")
    compact = re.sub(r"\s+", "", css)
    assert "min-width:600px" in compact
    assert re.search(r"\.swipe\{[^}]*display:block", compact)


# --- the travel card ---------------------------------------------------------------


def test_travel_card_present_only_with_commute_data_and_says_not_an_offset(travel_site, seed_no_travel_site, tmp_path_factory):
    for lang, title, tail in (("en", TRAVEL_EN, "not an offset"), ("es", TRAVEL_ES, "no una compensación")):
        soup = _soup(travel_site, lang, "home")
        card = _section_for(soup, title)
        assert card is not None, lang
        text = _text(card)
        assert t(lang, "travel_stat_avoided") in text and tail in text
        assert t(lang, "travel_intro") in text
        assert t(lang, "travel_tag") in text
        assert t(lang, "travel_category") in text
        assert t(lang, "travel_not_netted") in text
        assert t(lang, "travel_charging_note") in text
        # It is a card of its own, outside the campus chart section, with no chart inside it.
        chart_section = soup.find("section", id="monthly")
        assert chart_section is not None and card is not chart_section
        assert card.find("svg") is None
        assert all(p is not chart_section for p in card.parents) and all(p is not card for p in chart_section.parents)
        assert not card.find_all("section")

    # The real seed without its travel entries: no card in either language, and no travel anywhere on the page.
    for lang in LANGS:
        soup = _soup(seed_no_travel_site, lang, "home")
        assert _section_for(soup, t(lang, "travel_heading")) is None
        assert soup.find(id="travel") is None

    # A vehicle with no commute rows is not enough.
    out = _build(tmp_path_factory, _dataset(vehicles=[HOME_EV]))
    assert _soup(out, "en", "home").find(id="travel") is None


SEED_TRAVEL_MONTHS = _months("2025-11", 12)


def _seed_travel_figures():
    """Miles, tonnes counted on the travel line and tonnes avoided, straight from the calc functions."""
    results = commute_results(sample_dataset())
    assert [r.period_end_month for r in results] == SEED_TRAVEL_MONTHS
    return (
        sum(r.miles for r in results),
        sum(r.tco2e_counted_here for r in results),
        sum(r.avoided_tco2e for r in results),
    )


@pytest.mark.parametrize(
    "lang, heading, avoided_wording",
    [
        ("en", TRAVEL_EN, "avoided emissions compared with a gasoline car, not an offset"),
        (
            "es",
            TRAVEL_ES,
            "emisiones evitadas en comparación con un auto de gasolina, no una compensación",
        ),
    ],
)
def test_sample_home_shows_the_travel_card_with_the_calculated_figures(seed_site, lang, heading, avoided_wording):
    miles, counted, avoided = _seed_travel_figures()
    assert miles == 6500
    soup = _soup(seed_site, lang, "home")
    card = _section_for(soup, heading)
    assert card is not None
    text = _text(card)

    # Headline figures: a tilde because the miles are an estimate; tonnes to one decimal, or two when under one.
    assert f"~{miles:,.0f}" in text and "~6,500" in text
    assert 0 < counted < 1 < avoided
    assert f"~{counted:,.2f} t" in text
    assert f"~{avoided:,.1f} t" in text
    stats = [(_text(div.find("b")), _text(div.find("span"))) for div in card.select(".stat > div")]
    assert stats == [
        ("~6,500", t(lang, "travel_stat_miles", quality=t(lang, "travel_dq_estimate"))),
        (f"~{counted:,.2f} t", t(lang, "travel_stat_charging")),
        (f"~{avoided:,.1f} t", avoided_wording),
    ]
    assert avoided_wording == t(lang, "travel_stat_avoided")

    # What the card says about its basis: the vehicle's label figure, the miles per working day, scope 3 category 6.
    assert t(lang, "travel_basis", kwh100="30.0") in text
    assert t(lang, "travel_miles_estimate_note", miles_per_day=str(SAMPLE_MILES_PER_DAY)) in text
    assert t(lang, "travel_category") in text and t(lang, "travel_tag") in text
    assert t(lang, "travel_not_netted") in text
    assert t(lang, "travel_charging_note") in text  # charged away from the campus meters: counted on this line
    assert t(lang, "travel_already_counted") not in text and t(lang, "travel_months_note", n=12) not in text
    assert t(lang, "travel_avoided_none") not in text
    assert card.find("svg") is None


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("page", PAGES)
def test_sample_pages_never_mention_commuting_or_dollars(seed_site, lang, page):
    html = _html(seed_site, lang, page)
    text = _soup(seed_site, lang, page).get_text(" ")
    for what, pattern in (
        ("commuting", re.compile(r"\bcommut\w*", re.IGNORECASE)),
        ("a dollar sign", re.compile(r"\$")),
    ):
        assert not pattern.search(text), f"{lang}/{page} text mentions {what}"
        assert not pattern.search(html), f"{lang}/{page} markup mentions {what}"
    assert offset_terms_outside_allowed(text) == []


def test_the_vehicles_private_text_never_reaches_a_built_page(seed_site):
    """`note` and `kwh_per_mile_source` in vehicles.csv are for whoever maintains the data and are never published.

    The sample carries both, with the label website in the source text. The pages are built from it in both
    languages, and neither field, nor the vehicle's label, may appear in any built HTML file.
    """
    vehicles = sample_dataset().vehicles
    assert vehicles and all(v.note.strip() and v.kwh_per_mile_source.strip() for v in vehicles), "the seed must carry both"
    private = [text for v in vehicles for text in (v.note, v.kwh_per_mile_source)]
    needles = re.compile(r"fueleconomy|sample staff car", re.IGNORECASE)
    pages = sorted(Path(seed_site).rglob("*.html"))
    assert len(pages) >= len(LANGS) * len(PAGES)  # both languages, every page
    assert {p.relative_to(seed_site).parts[0] == "es" for p in pages} == {True, False}
    for page in pages:
        html = page.read_text(encoding="utf-8")
        where = page.relative_to(seed_site)
        for text in private:
            # Raw markup and as a reader sees it: the source text has "&" and quotes, which the page would escape.
            assert text not in html and text not in unescape(html), f"{where} prints a vehicle's private text"
        assert not needles.search(html), f"{where} mentions the vehicle's source page or model"


def test_real_seed_travel_card_leaves_the_campus_figures_alone(seed_site, seed_no_travel_site):
    for lang in LANGS:
        assert str(_hero(_soup(seed_site, lang, "home"))) == str(_hero(_soup(seed_no_travel_site, lang, "home")))
        # The chart section and its table are the same with or without the travel entries.
        with_travel = _soup(seed_site, lang, "home").find("section", id="monthly")
        without = _soup(seed_no_travel_site, lang, "home").find("section", id="monthly")
        assert str(with_travel) == str(without)


def test_travel_is_never_added_to_the_campus_total(tmp_path_factory, travel_site):
    plain = _build(tmp_path_factory, _dataset(quality=_estimates_first(6)))
    for lang in LANGS:
        with_travel = _hero(_soup(travel_site, lang, "home"))
        without = _hero(_soup(plain, lang, "home"))
        assert str(with_travel) == str(without)
        chart_with = _soup(travel_site, lang, "home").find("section", id="monthly")
        chart_without = _soup(plain, lang, "home").find("section", id="monthly")
        assert str(chart_with) == str(chart_without)


@pytest.mark.parametrize("per_day", [80, 60, 100])
def test_miles_per_working_day_is_computed_from_the_data(tmp_path_factory, per_day):
    out = _build(tmp_path_factory, _with_travel(per_day))
    en = _text(_soup(out, "en", "home").find(id="travel"))
    es = _text(_soup(out, "es", "home").find(id="travel"))
    assert f"estimate at {per_day} miles per working day" in en
    assert f"{per_day} millas por día laborable" in es
    total = sum(per_day * _weekdays(m) for m in _months("2025-11", 12))
    assert f"{total:,}" in en  # the miles in twelve months, from the rows


def test_miles_per_working_day_is_a_whole_number_over_the_baseline_weekdays(tmp_path_factory):
    # 21,000 miles over the 260 weekdays of Nov 2025 to Oct 2026 is 80.77, shown as 81.
    rows = [CommuteMonth("ev-home", m, 21000 * _weekdays(m) / 260, "estimate") for m in _months("2025-11", 12)]
    out = _build(tmp_path_factory, _dataset(vehicles=[HOME_EV], commute=rows))
    assert "estimate at 81 miles per working day" in _text(_soup(out, "en", "home").find(id="travel"))


def test_odometer_miles_get_the_odometer_note_and_no_per_day_estimate(tmp_path_factory):
    out = _build(tmp_path_factory, _with_travel(quality="odometer"))
    for lang in LANGS:
        card = _text(_soup(out, lang, "home").find(id="travel"))
        assert t(lang, "travel_miles_odometer_note") in card
        assert t(lang, "travel_dq_odometer") in card
        assert "per working day" not in card and "por día laborable" not in card


def test_avoided_figure_is_shown_only_when_above_zero(tmp_path_factory):
    # A very thirsty car charging at home emits more than the gasoline car: nothing is avoided.
    heavy = Vehicle("ev-home", "Test EV", 5.0, "home", "RFCW")
    out = _build(tmp_path_factory, _with_travel(vehicle=heavy))
    for lang in LANGS:
        card = _text(_soup(out, lang, "home").find(id="travel"))
        assert t(lang, "travel_avoided_none") in card
        assert t(lang, "travel_stat_avoided") not in card


def test_church_charging_shows_already_counted_and_no_charging_emissions(tmp_path_factory):
    out = _build(tmp_path_factory, _with_travel(vehicle=CHURCH_EV))
    for lang in LANGS:
        card = _text(_soup(out, lang, "home").find(id="travel"))
        assert t(lang, "travel_already_counted") in card
        assert t(lang, "travel_charging_note") not in card
        assert t(lang, "travel_stat_charging") not in card
        assert t(lang, "travel_stat_avoided") in card  # all the gasoline car's emissions are avoided


def test_travel_card_says_when_fewer_than_twelve_months_are_entered(tmp_path_factory):
    out = _build(tmp_path_factory, _with_travel(count=5))
    for lang in LANGS:
        assert t(lang, "travel_months_note", n=5) in _text(_soup(out, lang, "home").find(id="travel"))
    full = _text(_soup(_build(tmp_path_factory, _with_travel()), "en", "home").find(id="travel"))
    assert "Entries so far" not in full


def test_travel_card_appears_even_before_a_baseline_exists(tmp_path_factory):
    ds = _dataset(count=7, vehicles=[HOME_EV], commute=_commute_rows("ev-home", 80, count=7))
    out = _build(tmp_path_factory, ds)
    soup = _soup(out, "en", "home")
    assert t("en", "home_nobaseline_heading") in _text(soup)
    assert soup.find(id="travel") is not None


# --- data page --------------------------------------------------------------------


def test_data_page_has_the_monthly_table_feed_links_and_the_license_line(seed_site, seed_no_travel_site):
    ds = sample_dataset()
    for lang in LANGS:
        soup = _soup(seed_site, lang, "data")
        table = soup.find("table")
        assert table.find("caption").get_text(strip=True) == t(lang, "data_table_caption")
        assert len(table.select("tbody tr")) == len(ds.readings)
        headers = [_text(th) for th in table.select("thead th")]
        assert t(lang, "col_month") in headers and t(lang, "col_tco2e") in headers
        hrefs = {a["href"] for a in soup.find_all("a", href=True)}
        for path in ("monthly.csv", "monthly.json", "annual.json", "openapi.json", "factors.json", "milestones.json", "index.json"):
            assert f"/api/v1/{path}" in hrefs, f"{lang}: {path}"
        # The sample has travel entries, so its Data page links the two travel files; the sample without them
        # does not.
        assert "/api/v1/travel.json" in hrefs and "/api/v1/travel.csv" in hrefs
        no_travel = {a["href"] for a in _soup(seed_no_travel_site, lang, "data").find_all("a", href=True)}
        assert "/api/v1/travel.json" not in no_travel and "/api/v1/travel.csv" not in no_travel
        assert "/api/v1/monthly.json" in no_travel
        text = _text(soup.find("main"))
        assert "CC BY 4.0" in text
        assert any(a["href"] == "https://creativecommons.org/licenses/by/4.0/" for a in soup.find_all("a", href=True))
        assert t(lang, "opendata_contact", contact=CONTACT) in text


def test_data_page_rows_say_where_each_figure_came_from(tmp_path_factory):
    ds = _dataset(count=12, quality=lambda i, fuel: ("chart_measured" if fuel == "gas" else "chart_estimate") if i < 2 else "bill")
    out = _build(tmp_path_factory, ds)
    text = _text(_soup(out, "en", "data").find("table"))
    assert t("en", "dq_chart_estimate") in text
    assert t("en", "dq_chart_measured") in text
    assert t("en", "dq_uncertainty", pct="5") in text
    assert t("en", "dq_bill") in text
    assert "Nov 2025" in text and "Oct 2026" in text


def test_data_page_links_and_lists_travel_files_only_with_commute_rows(travel_site):
    for lang in LANGS:
        soup = _soup(travel_site, lang, "data")
        hrefs = {a["href"] for a in soup.find_all("a", href=True)}
        assert "/api/v1/travel.json" in hrefs and "/api/v1/travel.csv" in hrefs
        card = _section_for(soup, t(lang, "travel_heading"))
        assert card is not None
        travel_table = card.find("table")
        assert travel_table.find("caption").get_text(strip=True) == t(lang, "travel_table_caption")
        assert len(travel_table.select("tbody tr")) == 12
        assert t(lang, "col_avoided") in _text(travel_table)


# --- methodology -------------------------------------------------------------------


def test_methodology_lists_factor_editions_urls_exclusions_and_the_billing_period_offset(seed_no_travel_site):
    ds = sample_dataset()  # the factors are the real ones; the page is built from the seed without its travel entries
    for lang in LANGS:
        soup = _soup(seed_no_travel_site, lang, "methodology")
        text = _text(soup.find("main"))
        table = soup.find("table")
        assert table.find("caption").get_text(strip=True) == t(lang, "method_factors_caption")
        rows = table.select("tbody tr")
        # No vehicle data: the gasoline-car row, which only serves the avoided-emissions comparison, is not listed.
        shown = [f for f in ds.factors if f.factor_id != "gasoline_car_per_mile"]
        assert len(shown) == len(ds.factors) - 1 and len(rows) == len(shown)
        urls = {a["href"] for a in table.find_all("a", href=True)}
        for factor in shown:
            row = next(r for r in rows if factor_name(lang, factor.factor_id) in _text(r))
            row_text = _text(row)
            # The Spanish page shows the Spanish edition and revision text; the English page the English text.
            # `or` keeps the expected text from being empty (an empty string is "in" any row): a factor without
            # Spanish wording falls back to the English, exactly as the page does.
            assert ((factor.edition_es or factor.edition) if lang == "es" else factor.edition) in row_text
            assert ((factor.revision_es or factor.revision) if lang == "es" else factor.revision) in row_text
            assert factor.retrieved in row_text
            assert factor.unit in row_text
            assert f"{factor.value:.10g}" in row_text
            assert factor.url in urls and factor.url in row_text
        assert t(lang, "method_excluded_body_no_travel") in text
        assert t(lang, "method_excluded_body") not in text
        assert t(lang, "method_boundary_body") in text
        assert t(lang, "method_billing_body") in text
        assert ("often end on different days" if lang == "en" else "a menudo terminan en días distintos") in text
        assert f"subregion {SETTINGS.egrid_subregion}" in text or f"subregión {SETTINGS.egrid_subregion}" in text
        assert t(lang, "method_grid_note") in text
        for quality in ("bill", "utility_export", "chart_estimate", "chart_measured"):
            assert t(lang, f"method_dq_{quality}") in text
        # No vehicle data, so no travel or avoided-emissions sections yet.
        assert t(lang, "method_travel_body") not in text
        assert t(lang, "method_avoided_heading") not in text


def test_methodology_describes_travel_and_the_avoided_figure_when_there_is_commute_data(travel_site):
    ds = _with_travel()
    for lang in LANGS:
        soup = _soup(travel_site, lang, "methodology")
        text = _text(soup.find("main"))
        assert t(lang, "method_travel_body") in text
        assert t(lang, "method_avoided_body") in text
        # With travel, the original wording applies and every factor is listed, the gasoline car included.
        assert t(lang, "method_excluded_body") in text
        assert t(lang, "method_term_scope") in text  # includes the scope 3 sentence about the travel line
        assert t(lang, "method_excluded_body_no_travel") not in text
        rows = soup.find("table").select("tbody tr")
        assert len(rows) == len(ds.factors) == 3
        assert any(t(lang, "factor_name_gasoline_car_per_mile") in _text(r) for r in rows)


def test_real_seed_methodology_describes_the_travel_line_and_lists_all_three_factors(seed_site):
    ds = sample_dataset()
    for lang in LANGS:
        soup = _soup(seed_site, lang, "methodology")
        text = _text(soup.find("main"))
        assert t(lang, "method_travel_body") in text and t(lang, "method_avoided_heading") in text
        assert t(lang, "method_avoided_body") in text
        assert t(lang, "method_excluded_body") in text and t(lang, "method_excluded_body_no_travel") not in text
        rows = soup.find("table").select("tbody tr")
        assert len(rows) == len(ds.factors) == 3
        for factor in ds.factors:
            row = next(r for r in rows if factor_name(lang, factor.factor_id) in _text(r))
            assert (factor.edition_es if lang == "es" else factor.edition) in _text(row)
            assert f"{factor.value:.10g}" in _text(row)


ENGLISH_FACTOR_FRAGMENTS = ("released", "Revision 2", "Revision 1 was", "Last Modified", "January 2025", "listed on", "page last updated", "2023 data")


def test_real_seed_spanish_methodology_shows_the_spanish_edition_and_revision_not_the_english(seed_site):
    ds = sample_dataset()
    english_page = _soup(seed_site, "en", "methodology")
    spanish_page = _soup(seed_site, "es", "methodology")
    english_text = _text(english_page.find("table"))
    spanish_text = _text(spanish_page.find("table"))
    for fragment in ENGLISH_FACTOR_FRAGMENTS:
        assert fragment in english_text, fragment  # the English page is unchanged
        assert fragment not in spanish_text, f"the Spanish factors table still says {fragment!r}"
    egrid = next(f for f in ds.factors if f.factor_id == "electricity_rfcw")
    # Structure, not exact wording, so later edits to the Spanish text do not break this: the page shows
    # the factor's own Spanish revision, and that text keeps the revision numbers and dates of the English.
    assert egrid.revision_es and egrid.revision_es in spanish_text
    assert egrid.revision_es != egrid.revision
    for date_text in ("2025-06-12", "2025-01-17"):
        assert date_text in egrid.revision_es
    for number in ("2", "1"):
        assert re.search(rf"\b{number}\b", egrid.revision_es), f"revision number {number} is missing"
    for factor in ds.factors:
        english_row = next(r for r in english_page.select("tbody tr") if factor_name("en", factor.factor_id) in _text(r))
        spanish_row = next(r for r in spanish_page.select("tbody tr") if factor_name("es", factor.factor_id) in _text(r))
        english_cells = [_text(c) for c in english_row.find_all(["th", "td"])]
        spanish_cells = [_text(c) for c in spanish_row.find_all(["th", "td"])]
        assert english_cells[2:4] == [factor.edition, factor.revision]
        assert spanish_cells[2:4] == [factor.edition_es, factor.revision_es]
        # Everything else in the row is the same data in both languages: value, retrieval date and link.
        assert english_cells[1] == spanish_cells[1] and english_cells[4:] == spanish_cells[4:]


def test_spanish_methodology_uses_the_spanish_text_when_present_and_the_english_when_not(tmp_path_factory):
    with_spanish = replace(FACTORS[0], edition_es="Edición de prueba", revision_es="Revisión de prueba")
    without_spanish = replace(FACTORS[1], edition_es="", revision_es="")
    half = replace(FACTORS[2], edition_es="Solo la edición", revision_es="")
    ds = replace(_with_travel(), factors=[with_spanish, without_spanish, half])
    out = _build(tmp_path_factory, ds)
    spanish = _soup(out, "es", "methodology").select("tbody tr")
    english = _soup(out, "en", "methodology").select("tbody tr")
    cells = lambda rows, factor_id, lang: [  # noqa: E731
        _text(c) for c in next(r for r in rows if factor_name(lang, factor_id) in _text(r)).find_all(["th", "td"])
    ]
    assert cells(spanish, "electricity_rfcw", "es")[2:4] == ["Edición de prueba", "Revisión de prueba"]
    assert cells(spanish, "natural_gas", "es")[2:4] == ["Hub test edition", "Rev test B"]  # the English fills the gap
    assert cells(spanish, "gasoline_car_per_mile", "es")[2:4] == ["Solo la edición", "Rev test C"]
    # The English page never shows the Spanish text.
    assert cells(english, "electricity_rfcw", "en")[2:4] == ["eGRID test edition", "Rev test A"]
    assert "Edición de prueba" not in _html(out, "en", "methodology")


TRAVEL_WORDS = re.compile(r"travel|viaje|vehicle|vehículo|rector|gasoline|gasolina|scope 3|alcance 3|avoided|evitad", re.IGNORECASE)


@pytest.mark.parametrize("which", ["seed_without_travel", "vehicle_without_commute_rows", "empty"])
def test_methodology_does_not_mention_travel_when_there_are_no_commute_rows(tmp_path_factory, seed_no_travel_site, which):
    if which == "seed_without_travel":
        out = seed_no_travel_site
    elif which == "empty":
        out = _build(tmp_path_factory, Dataset())
    else:
        out = _build(tmp_path_factory, _dataset(vehicles=[HOME_EV]))  # a vehicle alone is not travel
    for lang in LANGS:
        soup = _soup(out, lang, "methodology")
        text = _text(soup)
        assert not TRAVEL_WORDS.search(text), f"{lang}: {TRAVEL_WORDS.search(text).group(0)!r}"
        assert t(lang, "method_excluded_body_no_travel") in text
        assert t(lang, "method_term_scope_no_travel") in text
        assert soup.find(id="travel") is None
        if which != "empty":  # these datasets carry the gasoline factor in factors.csv, which must not be listed
            names = " ".join(_text(th) for th in soup.select("tbody th"))
            assert t(lang, "factor_name_gasoline_car_per_mile") not in names
            assert "gasoline" not in soup.get_text().lower() and "gasolina" not in soup.get_text().lower()


def test_no_page_mentions_travel_when_the_dataset_has_no_commute_rows(seed_no_travel_site):
    for lang, page, soup in _all_pages(seed_no_travel_site):
        main = _text(soup.find("main"))
        assert not TRAVEL_WORDS.search(main), f"{lang}/{page}: {TRAVEL_WORDS.search(main).group(0)!r}"


# --- about -------------------------------------------------------------------------


def test_about_mentions_solar_only_with_solar_connected_milestone(tmp_path_factory):
    without = _build(tmp_path_factory, _dataset(milestones=[BOILER]))
    with_solar = _build(tmp_path_factory, _dataset(milestones=[BOILER, SOLAR]))
    solar_word = re.compile(r"\bsolar\b", re.IGNORECASE)

    for lang, page, soup in _all_pages(without):
        assert not solar_word.search(_html(without, lang, page)), f"{lang}/{page} mentions solar without a milestone"

    for lang, page, soup in _all_pages(with_solar):
        mentions = bool(solar_word.search(_html(with_solar, lang, page)))
        assert mentions == (page == "about"), f"{lang}/{page}"
    for lang, date_text in (("en", "Mar 9, 2027"), ("es", "9 mar 2027")):
        about = _soup(with_solar, lang, "about")
        section = _section_for(about, t(lang, "about_solar_heading"))
        assert section is not None
        assert _text(section) == f"{t(lang, 'about_solar_heading')} {t(lang, 'about_solar_body', date=date_text)}"

    # A milestone of another kind that merely sounds similar does not trigger the section.
    other = Milestone(date(2027, 3, 9), "other", "Roof inspection", "Inspección del techo")
    out = _build(tmp_path_factory, _dataset(milestones=[BOILER, other]))
    assert _section_for(_soup(out, "en", "about"), "Solar") is None


def test_about_lists_the_sample_milestone_by_year_only(seed_site):
    # The sample row is dated 2019-01-01 only because a date is required; just the year is known.
    for lang, label in (("en", "Boiler replaced (2019)"), ("es", "Caldera reemplazada (2019)")):
        soup = _soup(seed_site, lang, "about")
        section = _section_for(soup, t(lang, "milestones_heading"))
        items = [_text(li) for li in section.find_all("li")]
        assert items == [f"2019 {label}"]
        assert section.find("time")["datetime"] == "2019"
        assert not re.search(r"\b(Jan|ene)\b|\b1\b", items[0]), "no invented day or month"


MILESTONE_DATES = {
    "year": {"en": "2027", "es": "2027", "datetime": "2027"},
    "month": {"en": "Mar 2027", "es": "mar 2027", "datetime": "2027-03"},
    "day": {"en": "Mar 9, 2027", "es": "9 mar 2027", "datetime": "2027-03-09"},
}


@pytest.mark.parametrize("precision", ["year", "month", "day"])
def test_about_shows_each_milestone_date_only_as_precisely_as_it_is_known(tmp_path_factory, precision):
    roof = Milestone(date(2027, 3, 9), "other", "Roof work", "Obra del techo", precision)
    out = _build(tmp_path_factory, _dataset(milestones=[roof]))
    expected = MILESTONE_DATES[precision]
    for lang, label in (("en", "Roof work"), ("es", "Obra del techo")):
        section = _section_for(_soup(out, lang, "about"), t(lang, "milestones_heading"))
        assert [_text(li) for li in section.find_all("li")] == [f"{expected[lang]} {label}"]
        assert section.find("time")["datetime"] == expected["datetime"]


@pytest.mark.parametrize("precision", ["year", "month", "day"])
def test_solar_section_does_not_invent_a_day_or_a_month(tmp_path_factory, precision):
    solar = Milestone(date(2027, 3, 9), "solar_connected", "Solar system connected", "Sistema solar conectado", precision)
    out = _build(tmp_path_factory, _dataset(milestones=[BOILER, solar]))
    key = "about_solar_body" if precision == "day" else "about_solar_body_period"
    for lang in LANGS:
        section = _section_for(_soup(out, lang, "about"), t(lang, "about_solar_heading"))
        assert _text(section) == f"{t(lang, 'about_solar_heading')} {t(lang, key, date=MILESTONE_DATES[precision][lang])}"


def test_about_lists_every_milestone_the_validator_accepts_and_the_validator_guards_solar(tmp_path_factory):
    ds = _dataset(milestones=[BOILER, SOLAR])
    assert [i for i in validate_dataset(ds) if i.code == "SOLAR_LABEL"] == []
    out = _build(tmp_path_factory, ds)
    items = [_text(li) for li in _section_for(_soup(out, "en", "about"), t("en", "milestones_heading")).find_all("li")]
    assert items == ["2021 Boiler installed (2021)", "Mar 9, 2027 Solar system connected"]

    # The data rule that keeps solar off every other milestone: this dataset would be refused before a build.
    sneaky = _dataset(milestones=[Milestone(date(2022, 5, 1), "other", "Solar quote", "Cotización solar")])
    assert [i.code for i in validate_dataset(sneaky) if i.code == "SOLAR_LABEL"] == ["SOLAR_LABEL", "SOLAR_LABEL"]


def test_about_without_milestones_has_no_empty_milestones_section(tmp_path_factory):
    out = _build(tmp_path_factory, _dataset(milestones=[]))
    assert _section_for(_soup(out, "en", "about"), t("en", "milestones_heading")) is None


def test_about_links_and_text(seed_site):
    for lang in LANGS:
        soup = _soup(seed_site, lang, "about")
        text = _text(soup.find("main"))
        assert t(lang, "about_why_body") in text
        assert t(lang, "about_campus_body") in text
        assert t(lang, "about_updates_body") in text
        assert f"{ORG_NAME} is in Springfield, IL." in text or f"{ORG_NAME} está en Springfield, IL." in text
        links = {a["href"]: _text(a) for a in soup.find("main").find_all("a", href=True)}
        # The sample gives no website, so the only link is the contact address: nothing points at anyone else.
        assert links == {f"mailto:{CONTACT}": CONTACT}
        assert t(lang, "about_website_intro") not in text


def test_about_links_the_parish_website_only_when_the_settings_give_one(tmp_path_factory):
    out = _build(tmp_path_factory, _dataset(), which=settings(website="https://www.example.org/"))
    for lang in LANGS:
        main = _soup(out, lang, "about").find("main")
        link = main.find("a", href="https://www.example.org/")
        assert link is not None and _text(link) == ORG_NAME
        assert t(lang, "about_website_intro") in _text(main)
        # It is the one address that leaves the site, besides the license and the factor sources.
        assert _soup(out, lang, "home").find("a", href="https://www.example.org/") is None


# --- the built site is plain static files ----------------------------------------------


def _is_local(url):
    return url.startswith("/") and not url.startswith("//")


def test_no_script_tags_and_no_external_src_or_stylesheet(seed_site, travel_site, empty_site):
    for out in (seed_site, travel_site, empty_site):
        for lang, page, soup in _all_pages(out):
            where = f"{lang}/{page}"
            raw = _html(out, lang, page).lower()
            assert "<script" not in raw and "javascript:" not in raw, where
            assert not soup.find_all(["script", "iframe", "object", "embed", "form", "style"]), where
            for node in soup.find_all(True):
                assert not [a for a in node.attrs if a.lower().startswith("on")], f"{where}: event handler on <{node.name}>"
                for attr, value in node.attrs.items():
                    if isinstance(value, str):
                        assert "<" not in value, f"{where}: markup inside the {attr} attribute of <{node.name}>"
            for node in soup.find_all(src=True):
                assert _is_local(node["src"]), f"{where}: external src {node['src']}"
            for link in soup.find_all("link"):
                assert _is_local(link["href"]), f"{where}: external <link> {link['href']}"
            for sheet in soup.find_all("link", rel="stylesheet"):
                assert sheet["href"] == "/assets/site.css"
    css = CSS_PATH.read_text(encoding="utf-8")
    assert "@import" not in css
    assert not re.search(r"url\(\s*[\"']?(https?:|//|data:)", css)


def test_render_site_copies_the_assets_the_css_refers_to(seed_site):
    assets = seed_site / "assets"
    # The sample keeps the template's default colours, so its stylesheet is the source file, byte for byte.
    assert (assets / "site.css").read_text(encoding="utf-8") == CSS_PATH.read_text(encoding="utf-8")
    for name in (
        "noto-sans-latin-wght-normal.woff2",
        "noto-sans-latin-wght-italic.woff2",
        "noto-serif-latin-wght-normal.woff2",
        "noto-serif-latin-wght-italic.woff2",
        "OFL.txt",
    ):
        assert (assets / "fonts" / name).is_file(), name
    assert sorted(p.name for p in (assets / "img").iterdir()) == ["favicon.svg", "mark.svg"]
    css = (assets / "site.css").read_text(encoding="utf-8")
    refs = re.findall(r"url\(\s*[\"']?([^)\"']+)", css)
    assert refs, "the CSS should load the self-hosted fonts"
    for ref in refs:
        assert not ref.startswith(("/", "http")), ref  # relative to the CSS file
        assert (assets / ref).is_file(), ref
    # The feed's changelog is not a website asset.
    assert not (assets / "api-changelog.md").exists()
    home = _soup(seed_site, "en", "home")
    assert home.find("img")["src"] == "/assets/img/mark.svg" and home.find("img")["alt"] == ""
    icon = home.find("link", rel="icon")
    assert icon["href"] == "/assets/img/favicon.svg" and icon["type"] == "image/svg+xml"


@pytest.mark.parametrize("name", ["mark.svg", "favicon.svg"])
def test_the_built_in_mark_is_a_small_self_contained_svg(name):
    text = (ROOT / "static" / "img" / name).read_text(encoding="utf-8")
    assert text.startswith("<svg") and 'xmlns="http://www.w3.org/2000/svg"' in text and len(text) < 2000
    for banned in ("<script", "<image", "href=", "url(", "<style", "<text"):
        assert banned not in text, f"{name} must not hold {banned}"
    BeautifulSoup(text, "html.parser")  # well formed enough to parse


def test_render_is_reproducible(tmp_path):
    ds = sample_dataset()
    first, second = tmp_path / "a", tmp_path / "b"
    render_site(ds, first, GENERATED_AT, SETTINGS)
    render_site(ds, second, GENERATED_AT, SETTINGS)
    files = sorted(p.relative_to(first) for p in first.rglob("*") if p.is_file())
    assert files == sorted(p.relative_to(second) for p in second.rglob("*") if p.is_file())
    for rel in files:
        assert (first / rel).read_bytes() == (second / rel).read_bytes(), rel


def test_page_list_is_exactly_the_four_pages_in_each_language(seed_site):
    html_files = sorted(str(p.relative_to(seed_site)) for p in seed_site.rglob("*.html"))
    assert html_files == sorted(
        ["index.html", "data/index.html", "methodology/index.html", "about/index.html"]
        + ["es/index.html", "es/data/index.html", "es/methodology/index.html", "es/about/index.html"]
    )


def test_render_site_refuses_to_build_while_the_language_tables_disagree(tmp_path, monkeypatch):
    broken = {lang: dict(table) for lang, table in STRINGS.items()}
    del broken["es"]["home_h1"]
    monkeypatch.setattr("carbon.i18n.STRINGS", broken)
    with pytest.raises(ValueError, match="home_h1"):
        render_site(Dataset(), tmp_path, GENERATED_AT, SETTINGS)


@pytest.mark.parametrize("bad", ["17 Nov 2026", "2026-13-01", "2026-11-17T00:00:00", "", "20261117"])
def test_render_site_rejects_a_generated_at_that_is_not_an_iso_date(tmp_path, bad):
    with pytest.raises(ValueError, match="generated_at"):
        render_site(Dataset(), tmp_path, bad, SETTINGS)


# --- absolute links with base_url ----------------------------------------------------------


@pytest.mark.parametrize("base", ["https://carbon.example.org", "https://carbon.example.org/"])
@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("page", PAGES)
def test_base_url_makes_every_alternate_absolute_and_adds_one_canonical(tmp_path_factory, base, lang, page):
    out = tmp_path_factory.mktemp("absolute")
    render_site(Dataset(), out, GENERATED_AT, settings(base_url=base))
    soup = _soup(out, lang, page)
    origin = "https://carbon.example.org"
    alternates = {a["hreflang"]: a["href"] for a in soup.find_all("link", rel="alternate")}
    assert alternates == {
        "en": origin + URLS["en"][page],
        "es": origin + URLS["es"][page],
        "x-default": origin + URLS["en"][page],
    }
    assert [c["href"] for c in soup.find_all("link", rel="canonical")] == [origin + URLS[lang][page]]


def test_base_url_changes_nothing_but_the_alternates_and_the_canonical(tmp_path):
    plain, absolute = tmp_path / "plain", tmp_path / "absolute"
    render_site(Dataset(), plain, GENERATED_AT, SETTINGS)
    render_site(Dataset(), absolute, GENERATED_AT, settings(base_url="https://carbon.example.org"))
    head_links = re.compile(r'<link rel="(?:alternate|canonical)"[^>]*>\n')
    for lang, page, soup in _all_pages(absolute):
        reference = _soup(plain, lang, page)
        # The visible language switch, the navigation and every asset stay root-relative, so the same build
        # works from a local preview as well as from the real host.
        assert [a["href"] for a in soup.find_all("a")] == [a["href"] for a in reference.find_all("a")]
        assert head_links.sub("", _html(absolute, lang, page)) == head_links.sub("", _html(plain, lang, page))


def test_without_base_url_alternates_stay_root_relative_and_there_is_no_canonical(seed_site):
    for lang, page, soup in _all_pages(seed_site):
        assert soup.find("link", rel="canonical") is None
        assert all(a["href"].startswith("/") for a in soup.find_all("link", rel="alternate"))


@pytest.mark.parametrize(
    "bad",
    ["carbon.example.org", "ftp://carbon.example.org", "https://", "https://a.org?x=1", "https://a.org/#top", "https://a b.org", "https://a.org\n", "https://a.org/a b", "https://a.org//x", "/repo-name"],
)
def test_render_site_rejects_a_base_url_that_is_not_an_address_with_an_optional_path(tmp_path, bad):
    with pytest.raises(ValueError, match="base URL"):
        render_site(Dataset(), tmp_path, GENERATED_AT, settings(base_url=bad))
    assert not any(tmp_path.iterdir()), "nothing is written for a base URL that cannot be used"


# --- parity and content rules ------------------------------------------------------------


def test_english_and_spanish_pages_have_same_heading_counts(seed_site, travel_site, empty_site):
    for out in (seed_site, travel_site, empty_site):
        for page in PAGES:
            en, es = _soup(out, "en", page), _soup(out, "es", page)
            assert [h.name for h in en.find_all(re.compile(r"^h[1-6]$"))] == [h.name for h in es.find_all(re.compile(r"^h[1-6]$"))], page
            for tag in ("section", "table", "svg", "li", "a", "tr", "nav"):
                assert len(en.find_all(tag)) == len(es.find_all(tag)), f"{page}: {tag}"
            assert [s.get("id") for s in en.find_all("section")] == [s.get("id") for s in es.find_all("section")]


def test_english_and_spanish_pages_show_the_same_numbers(seed_site, travel_site):
    number = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
    for out in (seed_site, travel_site):
        for page in PAGES:
            en = number.findall(_text(_soup(out, "en", page).find("main")))
            spanish = _text(_soup(out, "es", page).find("main"))
            spanish = spanish.replace("(mil kilogramos)", "(1,000 kilogramos)")  # the one number the strings spell out
            assert sorted(en) == sorted(number.findall(spanish)), page


def test_co2e_uses_a_subscript_element_and_never_the_missing_glyph(seed_site, travel_site):
    # The self-hosted Noto subsets have no U+2082, so "CO₂e" is built from <sub>2</sub> in text and spelled
    # "CO2e" in attributes and in the SVG.
    assert "<sub>2</sub>e" in _html(seed_site, "en", "home")
    for out in (seed_site, travel_site):
        for lang, page, _ in _all_pages(out):
            assert "₂" not in _html(out, lang, page), f"{lang}/{page}"
    assert "CO<sub>2</sub>e" in _html(seed_site, "en", "methodology")


def test_no_u2082_in_built_pages(seed_site, travel_site, empty_site):
    for out in (seed_site, travel_site, empty_site):
        for lang, page, _ in _all_pages(out):
            html = _html(out, lang, page)
            assert "₂" not in html, f"{lang}/{page}"
            assert "CO₂e" not in html, f"{lang}/{page}"


# The Latin subset of the self-hosted Noto fonts covers these ranges (U+0000 to U+00FF and a few more).
FONT_SUBSET = [
    (0x0000, 0x00FF), (0x0131, 0x0131), (0x0152, 0x0153), (0x02BB, 0x02BC), (0x02C6, 0x02C6), (0x02DA, 0x02DA),
    (0x02DC, 0x02DC), (0x0304, 0x0304), (0x0308, 0x0308), (0x0329, 0x0329), (0x2000, 0x206F), (0x20AC, 0x20AC),
    (0x2122, 0x2122), (0x2191, 0x2191), (0x2193, 0x2193), (0x2212, 0x2212), (0x2215, 0x2215), (0xFEFF, 0xFEFF),
    (0xFFFD, 0xFFFD),
]


def test_built_pages_only_use_characters_the_fonts_cover(seed_site, travel_site):
    for out in (seed_site, travel_site):
        for lang, page, soup in _all_pages(out):
            for char in set(soup.get_text()):
                assert any(lo <= ord(char) <= hi for lo, hi in FONT_SUBSET), f"{lang}/{page}: U+{ord(char):04X} {char!r}"


HOUSE = re.compile(r"\bhouse\b|\bcasa\b", re.IGNORECASE)
COMMUTE = re.compile(r"\bcommut\w*|\bconmut\w*|desplazamiento al trabajo|viaje al trabajo|trayecto al trabajo", re.IGNORECASE)
VENDORS = re.compile(r"\b(lease|leases|leasing|vendor|vendors|comed|nicor|arrendamiento|alquiler|proveedor|proveedores)\b", re.IGNORECASE)


def test_built_pages_contain_no_forbidden_content(seed_site, travel_site, empty_site, tmp_path_factory):
    with_solar = _build(tmp_path_factory, replace(_with_travel(vehicle=CHURCH_EV), milestones=[BOILER, SOLAR]))
    for out in (seed_site, travel_site, empty_site, with_solar):
        for lang, page, soup in _all_pages(out):
            where = f"{lang}/{page}"
            html = _html(out, lang, page)
            text = soup.get_text(" ")
            for pattern, what in ((HOUSE, "the house"), (COMMUTE, "commuting"), (VENDORS, "a vendor or lease")):
                assert not pattern.search(text), f"{where} mentions {what}"
                assert not pattern.search(html.replace("https://", "")), f"{where} markup mentions {what}"
            assert not re.search(r"\$\s?\d|\d\s?\$|usd|dollar|dólar", text, re.IGNORECASE), f"{where}: dollar amount"
            assert not re.search(r"\d(?:[ -]?\d){8,}", text), f"{where}: long digit run"
            assert offset_terms_outside_allowed(text) == [], f"{where}: offset wording"
            lowered = text.lower()
            for phrase in ("carbon neutral", "carbon-neutral", "neutral en carbono", "zero emissions", "net zero", "has achieved"):
                assert phrase not in lowered, f"{where}: {phrase}"
            assert "meter number" not in lowered and "account number" not in lowered, where


# --- the stylesheet ---------------------------------------------------------------------------


def _tokens():
    css = CSS_PATH.read_text(encoding="utf-8")
    root = re.search(r":root\s*\{([^}]*)\}", css)
    assert root, "static/site.css must define its colour tokens in :root"
    return {name: value.upper() for name, value in re.findall(r"--([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{6})\b", root.group(1))}


def test_css_tokens_match_the_chart_colours_and_the_settings_neutrals():
    tokens = _tokens()
    assert tokens["primary"] == DEFAULT_PRIMARY.upper() == SETTINGS.primary_color
    assert tokens["accent"] == DEFAULT_ACCENT.upper() == SETTINGS.accent_color
    assert tokens["hatch"] == site.hatch_colour(DEFAULT_ACCENT)
    assert tokens["ink"] == INK.upper()
    assert tokens["muted"] == MUTED.upper()
    # The backgrounds the brand contrast check judges against are the stylesheet's own.
    assert {name: tokens[name] for name in NEUTRALS} == {name: value.upper() for name, value in NEUTRALS.items()}
    assert brand_contrast_problems(tokens["primary"], tokens["accent"]) == []


def test_css_tokens_meet_contrast():
    tokens = {**_tokens(), "white": "#FFFFFF"}
    # Every text and background pair the stylesheet uses, with where it is used.
    pairs = [
        ("ink", "white", "body text on cards, the header and the footer"),
        ("primary", "white", "links, the current navigation item, summary toggles"),
        ("muted", "white", "secondary text on cards, table captions and headers"),
        ("white", "primary", "the scope tag and the active language"),
        ("ink", "accent", "the skip link when it has focus, and the sample-data banner"),
        ("ink", "bg", "body text on the page background"),
        ("muted", "bg", "the intro paragraph on the page background"),
        ("ink", "soft", "badge and callout text"),
        ("muted", "soft", "tile detail text"),
        ("primary", "soft", "links inside callouts"),
        ("primary", "bg", "links on the page background"),
        ("ink", "tint", "the travel card's text"),
        ("muted", "tint", "the travel card's secondary text"),
        ("primary", "tint", "links in the travel card"),
    ]
    for fg, bg, where in pairs:
        ratio = contrast_ratio(tokens[fg], tokens[bg])
        assert ratio >= 4.5, f"{fg} on {bg} is {ratio:.2f}, under 4.5 ({where})"


def test_css_respects_reduced_motion_and_has_print_rules():
    compact = re.sub(r"\s+", "", CSS_PATH.read_text(encoding="utf-8"))
    assert "@mediaprint" in compact
    assert "scroll-behavior" not in compact
    # Any motion is opt-in: every transition sits inside the no-preference query.
    opt_in = re.findall(r"@media\(prefers-reduced-motion:no-preference\)\{(?:[^{}]*\{[^{}]*\})*\}", compact)
    assert opt_in, "expected a (prefers-reduced-motion: no-preference) block"
    assert compact.count("transition") == sum(block.count("transition") for block in opt_in)
    assert compact.count("animation") == sum(block.count("animation") for block in opt_in)


# --- navigation fit and print fit of the factors table (Task 9b, F1 and F2) ---------------------


def _css_rules():
    """Every rule of the stylesheet as (enclosing @media conditions, selector, {property: value}).

    A tiny reader, enough for this stylesheet: comments are dropped, @font-face is skipped, @media blocks are
    entered, and a comma list of selectors becomes one entry per selector.
    """
    css = re.sub(r"/\*.*?\*/", "", CSS_PATH.read_text(encoding="utf-8"), flags=re.S)
    rules = []

    def walk(text, context):
        pos = 0
        while True:
            start = text.find("{", pos)
            if start == -1:
                return
            head, depth, i = text[pos:start].strip(), 1, start + 1
            while depth:
                depth += (text[i] == "{") - (text[i] == "}")
                i += 1
            body = text[start + 1 : i - 1]
            if head.startswith("@media"):
                walk(body, context + (re.sub(r"\s+", " ", head),))
            elif not head.startswith("@"):
                decls = {}
                for part in body.split(";"):
                    if ":" in part:
                        name, value = part.split(":", 1)
                        decls[name.strip().lower()] = value.strip()
                for selector in head.split(","):
                    rules.append((context, re.sub(r"\s+", " ", selector.strip()), decls))
            pos = i

    walk(css, ())
    return rules


def _declarations(selector, *, within=None):
    """The merged declarations of every rule for `selector`; `within` is None (outside any @media) or a media text."""
    merged = {}
    for context, sel, decls in _css_rules():
        if sel == selector and ((within is None and not context) or (within is not None and any(within in c for c in context))):
            merged.update(decls)
    return merged


def test_the_css_reader_finds_rules_inside_and_outside_media_blocks():
    assert _declarations(".bar")["display"] == "flex"
    assert "min-width" in _declarations(".chartwrap .chart", within="min-width: 561px")
    assert _declarations("nav.sub", within="print") == {"display": "none"}
    assert "display" not in _declarations("nav.sub")  # that rule is inside @media print only


def test_the_navigation_wraps_onto_a_second_row_and_never_scrolls_sideways():
    """F1: at 375px the About link was clipped inside a scrolling strip, and focus could not bring it into view."""
    wrap = _declarations("nav.sub .wrap")
    assert wrap.get("display") == "flex"
    assert wrap.get("flex-wrap") == "wrap", "the four links must wrap when they do not fit in one row"
    for context, selector, decls in _css_rules():
        if selector.startswith("nav.sub") or selector == "nav":
            for prop in ("overflow", "overflow-x", "overflow-y"):
                assert decls.get(prop, "visible") not in ("auto", "scroll"), f"{selector} {prop}: {decls[prop]}"
    link = _declarations("nav.sub a")
    assert "nowrap" not in link.get("white-space", ""), "a link that cannot wrap could push the page wider than the screen"


def test_the_navigation_links_are_at_least_44px_tall_touch_targets():
    link = _declarations("nav.sub a")
    assert link.get("min-height", "").endswith("px")
    assert float(link["min-height"][:-2]) >= 44
    assert link.get("display") in ("flex", "inline-flex", "block", "inline-block"), "min-height needs a box, not an inline"


def test_the_navigation_keeps_its_visible_focus_style():
    ring = _declarations("a:focus-visible")
    assert ring.get("outline") == "3px solid var(--ink)"
    assert ring.get("box-shadow") == "0 0 0 5px var(--accent)"
    inner = _declarations("nav.sub a:focus-visible")
    assert inner.get("outline-offset") == "-5px" and inner.get("box-shadow") == "none"  # the ring stays inside the link
    assert not [sel for _, sel, d in _css_rules() if "nav" in sel and "order" in d]


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("page", PAGES)
def test_keyboard_order_is_skip_link_brand_language_navigation_then_content(seed_site, lang, page):
    soup = _soup(seed_site, lang, page)
    body_links = []
    for a in soup.find("body").find_all("a", href=True):
        if a.find_parent("main") is None and a.find_parent("footer") is None:
            body_links.append(a)
    labels = [a.get_text(strip=True) for a in body_links]
    nav_labels = [t(lang, f"nav_{name}") for name in ("home", "data", "methodology", "about")]
    other_language = "es" if lang == "en" else "en"
    assert labels[0] == t(lang, "skip_to_content")
    assert labels[1:3] == [t(lang, "site_name") + t(lang, "site_tagline"), t(other_language, f"lang_name_{other_language}")]
    assert labels[3:] == nav_labels
    assert not soup.find(attrs={"tabindex": re.compile(r"^[1-9]")}), "no positive tabindex reorders the keyboard"
    # The navigation comes after the language switch and before the page content in the source.
    html = _html(seed_site, lang, page)
    assert html.index('class="lang"') < html.index('<nav class="sub"') < html.index("<main")


@pytest.mark.parametrize("lang", LANGS)
def test_the_factors_table_is_marked_so_the_print_rules_can_fit_it(seed_site, lang):
    table = _soup(seed_site, lang, "methodology").find("table")
    assert "factors" in table.get("class", [])


def test_print_fits_the_factors_table_inside_the_card_and_lets_addresses_break():
    """F2: in print the table was 713px wide in a 661px column, so the Source link crossed the card's border."""
    table = _declarations("table.factors", within="print")
    assert table.get("table-layout") == "fixed" and table.get("width") == "100%"
    assert float(table["font-size"].removesuffix("pt")) <= 10
    cells = _declarations("table.factors td", within="print")
    assert cells.get("overflow-wrap") == "anywhere", "long web addresses must be allowed to break anywhere"
    assert _declarations("table.factors td.url", within="print").get("min-width") == "0", "screen min-width must not apply"
    # Fixed layout takes its column widths from the header row: all six are set and they add up to 100% or less.
    widths = [
        _declarations(f"table.factors thead th:nth-child({n})", within="print").get("width", "") for n in range(1, 7)
    ]
    assert all(w.endswith("%") for w in widths), widths
    assert sum(float(w[:-1]) for w in widths) <= 100
    # Screen layout is untouched: the same table still scrolls inside its own region on a phone.
    assert _declarations("table.factors") == {} and _declarations("table")["min-width"] == "560px"
    assert _declarations(".tablewrap")["overflow-x"] == "auto"


def test_calendar_helper_agrees_with_the_standard_library():
    # The test's own weekday count must match calendar.monthrange so the per-day tests above prove something.
    assert sum(_weekdays(m) for m in _months("2025-11", 12)) == 260
    year, month = 2026, 2
    first_weekday, days = calendar.monthrange(year, month)
    assert _weekdays("2026-02") == sum(1 for d in range(days) if (first_weekday + d) % 7 < 5)


# =================================================================================================
# What parish.toml decides: languages, the travel line, the grid subregion, the base path, the brand
# and the sample-data banner.
# =================================================================================================

ENGLISH_ONLY = settings(languages=("en",))


@pytest.fixture(scope="module")
def english_site(tmp_path_factory):
    return _build(tmp_path_factory, sample_dataset(ENGLISH_ONLY), which=ENGLISH_ONLY)


def test_an_english_only_site_has_the_four_english_pages_and_no_es_folder(english_site):
    html_files = sorted(str(p.relative_to(english_site)) for p in english_site.rglob("*.html"))
    assert html_files == sorted(["index.html", "data/index.html", "methodology/index.html", "about/index.html"])
    assert not (english_site / "es").exists()


@pytest.mark.parametrize("page", PAGES)
def test_an_english_only_page_has_no_language_switch_no_alternates_and_no_link_to_es(english_site, page):
    soup = _soup(english_site, "en", page)
    assert soup.html["lang"] == "en"
    assert soup.find(class_="lang") is None
    assert soup.find_all("link", rel="alternate") == []
    assert not [a["href"] for a in soup.find_all(href=True) if a["href"].startswith("/es")]
    assert not soup.find_all(attrs={"hreflang": True})
    # Nothing Spanish is left on the page: no Spanish label, month or sentence.
    text = _text(soup)
    assert "Español" not in text and "Caldera" not in text and "Línea base" not in text


def test_an_english_only_site_says_the_same_things_in_english_as_the_bilingual_one(english_site, seed_site):
    strip = re.compile(r'<link rel="alternate"[^>]*>\n|[ ]*<div class="lang".*?</div>\n', re.DOTALL)
    for page in PAGES:
        assert strip.sub("", _html(english_site, "en", page)) == strip.sub("", _html(seed_site, "en", page)), page


def test_an_english_only_site_keeps_canonical_links_when_a_base_url_is_set(tmp_path_factory):
    out = _build(tmp_path_factory, Dataset(), which=settings(languages=("en",), base_url="https://carbon.example.org"))
    soup = _soup(out, "en", "data")
    assert [c["href"] for c in soup.find_all("link", rel="canonical")] == ["https://carbon.example.org/data/"]
    assert soup.find_all("link", rel="alternate") == []


def test_an_english_only_site_builds_from_milestones_without_spanish_labels(tmp_path_factory):
    ds = _dataset(milestones=[Milestone(date(2019, 1, 1), "boiler", "Boiler replaced (2019)", "", "year")])
    assert validate_dataset(ds, ENGLISH_ONLY) == []
    out = _build(tmp_path_factory, ds, which=ENGLISH_ONLY)
    assert "2019 Boiler replaced (2019)" in _text(_soup(out, "en", "about"))


# --- travel switched off ----------------------------------------------------------------------------------

NO_TRAVEL = settings(travel_enabled=False)


@pytest.fixture(scope="module")
def travel_off_site(tmp_path_factory):
    # The dataset is built by hand and still holds a vehicle and twelve months of miles: the setting alone
    # must keep every trace of it off the site.
    return _build(tmp_path_factory, _with_travel(), which=NO_TRAVEL)


def test_with_travel_switched_off_no_page_has_a_travel_card_section_or_file_link(travel_off_site):
    for lang, page, soup in _all_pages(travel_off_site):
        where = f"{lang}/{page}"
        assert soup.find(id="travel") is None, where
        assert _section_for(soup, t(lang, "travel_heading")) is None, where
        text = _text(soup)
        assert not TRAVEL_WORDS.search(text), f"{where}: {TRAVEL_WORDS.search(text).group(0)!r}"
        assert not [a["href"] for a in soup.find_all("a", href=True) if "travel" in a["href"]], where
    for lang in LANGS:
        methodology = _text(_soup(travel_off_site, lang, "methodology"))
        assert t(lang, "method_excluded_body_no_travel") in methodology
        assert t(lang, "method_term_scope_no_travel") in methodology
        rows = _soup(travel_off_site, lang, "methodology").select("table.factors tbody tr")
        assert len(rows) == 2, "the grid and natural gas, and no gasoline-car comparison"


def test_with_travel_switched_off_the_campus_pages_are_those_of_the_same_data_without_travel(travel_off_site, tmp_path_factory):
    without = _build(tmp_path_factory, replace(_with_travel(), vehicles=[], commute=[]))
    for lang in LANGS:
        for page in PAGES:
            assert _html(travel_off_site, lang, page) == _html(without, lang, page), f"{lang}/{page}"


def test_the_travel_label_is_the_parishs_own_on_every_page_that_names_it(tmp_path_factory):
    named = settings(travel_label_en="Pastor's visits", travel_label_es="Visitas pastorales")
    out = _build(tmp_path_factory, _with_travel(), which=named)
    for lang, label in (("en", "Pastor's visits"), ("es", "Visitas pastorales")):
        for page in ("home", "data", "methodology"):
            section = _soup(out, lang, page).find(id="travel")
            assert _text(section.find("h2")) == label, f"{lang}/{page}"
        data = _text(_soup(out, lang, "data"))
        assert f"{label}, " in data
        assert TRAVEL_EN not in _html(out, lang, "home") and TRAVEL_ES not in _html(out, lang, "home")


# --- the grid subregion ------------------------------------------------------------------------------------

FAKE_GRID = _factor("electricity_zzzt", 0.9, "kg CO2e/kWh", "Made-up grid edition", "Rev test Z", "https://example.org/zzzt.pdf", "2026-01-05")
OTHER_GRID = _factor("electricity_aaat", 0.1, "kg CO2e/kWh", "Another made-up edition", "Rev test Y", "https://example.org/aaat.pdf", "2026-01-06")


def test_methodology_shows_only_the_configured_subregions_grid_factor_from_a_file_with_many(tmp_path_factory):
    here = settings(egrid_subregion="ZZZT")
    ds = replace(_dataset(), factors=[*FACTORS, FAKE_GRID, OTHER_GRID], grid_subregion="ZZZT")
    out = _build(tmp_path_factory, ds, which=here)
    for lang in LANGS:
        soup = _soup(out, lang, "methodology")
        rows = [_text(r) for r in soup.select("table.factors tbody tr")]
        assert len(rows) == 2
        grid = next(r for r in rows if factor_name(lang, "electricity_zzzt") in r)
        assert "ZZZT" in grid and "0.9 kg CO2e/kWh" in grid and "Made-up grid edition" in grid
        page = _text(soup)
        assert "RFCW" not in page and "AAAT" not in page and "eGRID test edition" not in page
        assert t(lang, "method_grid_note", subregion="ZZZT") in page


def test_a_different_subregion_changes_the_electricity_figures_and_leaves_the_gas_alone(tmp_path_factory):
    factors = [*FACTORS, FAKE_GRID]
    there = _build(tmp_path_factory, replace(_dataset(), factors=factors, grid_subregion="ZZZT"), which=settings(egrid_subregion="ZZZT"))
    here = _build(tmp_path_factory, replace(_dataset(), factors=factors))
    tiles = lambda out: [_text(v) for v in _soup(out, "en", "home").select(".tile .v")]  # noqa: E731
    gas_there, electricity_there = tiles(there)
    gas_here, electricity_here = tiles(here)
    assert gas_there == gas_here
    kwh = sum(1000.0 + 10 * i for i in range(12))
    assert electricity_here == f"{kwh * 0.4 / 1000:,.1f} t" and electricity_there == f"{kwh * 0.9 / 1000:,.1f} t"


# --- the base path (a GitHub Pages project site lives under /repo-name/) -----------------------------------

PREFIX = "/parish-carbon"
PREFIXED = settings(base_url="https://someone.github.io/parish-carbon")
_CSS_URL = re.compile(r"url\(\s*[\"']?([^)\"']+)")


def _internal_references(soup):
    """Every address a page points at with href or src, as (tag, attribute, value)."""
    found = []
    for node in soup.find_all(True):
        for attr in ("href", "src", "poster", "data", "action", "srcset"):
            value = node.get(attr)
            if isinstance(value, str):
                found.append((node.name, attr, value))
    return found


@pytest.fixture(scope="module")
def prefixed_site(tmp_path_factory):
    return _build(tmp_path_factory, sample_dataset(), which=PREFIXED)


def test_under_a_base_path_no_internal_link_or_asset_escapes_the_prefix(prefixed_site):
    seen = 0
    for lang, page, soup in _all_pages(prefixed_site):
        for tag, attr, value in _internal_references(soup):
            where = f"{lang}/{page}: <{tag} {attr}={value!r}>"
            if value.startswith(("#", "mailto:")):
                continue
            if value.startswith("https://someone.github.io"):
                assert value.startswith("https://someone.github.io" + PREFIX + "/"), where
                continue
            if value.startswith(("http://", "https://")):
                assert tag == "a", where  # an outside link: the license, a factor's source
                continue
            assert value.startswith(PREFIX + "/"), where
            assert not value.startswith("//"), where
            seen += 1
    assert seen > 100


def test_under_a_base_path_every_internal_address_is_a_file_the_build_wrote(prefixed_site):
    # Serve the folder at the prefix in the mind's eye: strip it and the file must be there. The feed files are
    # written by carbon.feed, not by render_site, so links into /api/ are only checked for their prefix.
    for lang, page, soup in _all_pages(prefixed_site):
        for tag, attr, value in _internal_references(soup):
            if not value.startswith(PREFIX + "/") or value.startswith(PREFIX + "/api/"):
                continue
            relative = value[len(PREFIX) + 1 :]
            target = prefixed_site / relative
            if value.endswith("/"):
                target = target / "index.html"
            assert target.is_file(), f"{lang}/{page}: {value} points at nothing"


def test_under_a_base_path_the_head_points_at_prefixed_fonts_styles_icon_and_absolute_alternates(prefixed_site):
    for lang, page, soup in _all_pages(prefixed_site):
        assert [s["href"] for s in soup.find_all("link", rel="stylesheet")] == [PREFIX + "/assets/site.css"]
        preloads = [link["href"] for link in soup.find_all("link", rel="preload")]
        assert preloads and all(href.startswith(PREFIX + "/assets/fonts/") for href in preloads)
        assert soup.find("link", rel="icon")["href"] == PREFIX + "/assets/img/favicon.svg"
        assert soup.find("header").find("img")["src"] == PREFIX + "/assets/img/mark.svg"
        base = "https://someone.github.io" + PREFIX
        alternates = {a["hreflang"]: a["href"] for a in soup.find_all("link", rel="alternate")}
        assert alternates == {"en": base + URLS["en"][page], "es": base + URLS["es"][page], "x-default": base + URLS["en"][page]}
        assert [c["href"] for c in soup.find_all("link", rel="canonical")] == [base + URLS[lang][page]]
        assert soup.find(class_="brand")["href"] == PREFIX + URLS[lang]["home"]
        nav = [a["href"] for a in soup.find("nav").find_all("a")]
        assert nav == [PREFIX + URLS[lang][name] for name in PAGES]
        switch = soup.find(class_="lang").find("a")
        assert switch["href"] == PREFIX + URLS["es" if lang == "en" else "en"][page]


def test_under_a_base_path_the_data_page_links_the_feed_under_the_prefix(prefixed_site):
    for lang in LANGS:
        hrefs = {a["href"] for a in _soup(prefixed_site, lang, "data").find_all("a", href=True)}
        for name in ("index.json", "monthly.csv", "monthly.json", "annual.json", "travel.json", "travel.csv", "factors.json", "milestones.json", "openapi.json"):
            assert f"{PREFIX}/api/v1/{name}" in hrefs, name
        assert not [h for h in hrefs if h.startswith("/api/")]


def test_the_stylesheet_reaches_its_fonts_by_relative_urls_so_it_works_under_any_path(prefixed_site):
    css = (prefixed_site / "assets" / "site.css").read_text(encoding="utf-8")
    refs = _CSS_URL.findall(css)
    assert len(refs) == 4
    for ref in refs:
        assert not ref.startswith(("/", "http", "//")), ref
        assert (prefixed_site / "assets" / ref).is_file(), ref
    # No inline style or SVG on any page reaches outside either.
    for lang, page, soup in _all_pages(prefixed_site):
        for ref in _CSS_URL.findall(_html(prefixed_site, lang, page)):
            assert ref.startswith("#"), f"{lang}/{page}: url({ref})"


def test_a_base_path_changes_only_addresses_never_what_the_pages_say(prefixed_site, seed_site):
    for lang, page, soup in _all_pages(prefixed_site):
        assert _text(soup.find("body")) == _text(_soup(seed_site, lang, page).find("body")), f"{lang}/{page}"
        plain = _html(seed_site, lang, page)
        unprefixed = _html(prefixed_site, lang, page).replace("https://someone.github.io" + PREFIX, "").replace(PREFIX + "/", "/")
        head_links = re.compile(r'<link rel="canonical"[^>]*>\n')
        assert head_links.sub("", unprefixed) == plain, f"{lang}/{page}"


@pytest.mark.parametrize(
    "base_url, origin, prefix",
    [
        ("https://someone.github.io/parish-carbon", "https://someone.github.io", "/parish-carbon"),
        ("https://someone.github.io/parish-carbon/", "https://someone.github.io", "/parish-carbon"),
        ("https://example.org/green/st-example", "https://example.org", "/green/st-example"),
        ("https://carbon.example.org", "https://carbon.example.org", ""),
        ("https://carbon.example.org/", "https://carbon.example.org", ""),
        ("http://localhost:8000", "http://localhost:8000", ""),
        ("", "", ""),
    ],
)
def test_a_base_url_is_split_into_its_origin_and_its_path_prefix(base_url, origin, prefix):
    which = settings(base_url=base_url)
    assert (which.origin, which.path_prefix) == (origin, prefix)
    assert site.page_url("es", "data", which.path_prefix) == prefix + "/es/data/"


def test_at_a_domain_root_and_with_no_base_url_links_are_root_relative_so_a_local_preview_works(seed_site, tmp_path_factory):
    at_root = _build(tmp_path_factory, sample_dataset(), which=settings(base_url="https://carbon.example.org"))
    for out in (seed_site, at_root):
        for lang, page, soup in _all_pages(out):
            for tag, attr, value in _internal_references(soup):
                if tag == "link" and soup.find("link", href=value).get("rel") in (["alternate"], ["canonical"]):
                    continue
                if value.startswith(("#", "mailto:", "http://", "https://")):
                    continue
                assert value.startswith("/") and not value.startswith("//"), f"{lang}/{page}: {value}"
                if not value.startswith("/api/"):
                    target = out / value.lstrip("/")
                    assert (target / "index.html" if value.endswith("/") else target).is_file(), value


# --- the brand: two colours and an optional logo ------------------------------------------------------------

REBRANDED = settings(primary_color="#7A1F3D", accent_color="#FFE9A8")


@pytest.fixture(scope="module")
def rebranded_site(tmp_path_factory):
    return _build(tmp_path_factory, sample_dataset(), which=REBRANDED)


def test_the_brand_colours_from_the_settings_are_written_into_the_built_stylesheet(rebranded_site):
    built = (rebranded_site / "assets" / "site.css").read_text(encoding="utf-8")
    root = re.search(r":root\s*\{([^}]*)\}", built).group(1)
    tokens = dict(re.findall(r"--([a-z0-9-]+)\s*:\s*(#[0-9A-Fa-f]{6})\b", root))
    assert tokens["primary"] == "#7A1F3D" and tokens["accent"] == "#FFE9A8"
    assert tokens["hatch"] == site.hatch_colour("#FFE9A8") != "#FFE9A8"
    assert DEFAULT_PRIMARY not in built and DEFAULT_ACCENT not in built
    # Only those three values differ from the source stylesheet.
    source = CSS_PATH.read_text(encoding="utf-8")
    changed = [(a, b) for a, b in zip(source.splitlines(), built.splitlines()) if a != b]
    assert len(changed) == 3 and len(source.splitlines()) == len(built.splitlines())
    assert all(a.strip().startswith(("--primary", "--accent", "--hatch")) for a, _ in changed)


def test_the_brand_colours_reach_the_chart_and_the_theme_colour_and_nothing_keeps_the_defaults(rebranded_site):
    for lang, page, soup in _all_pages(rebranded_site):
        html = _html(rebranded_site, lang, page)
        assert DEFAULT_PRIMARY not in html and DEFAULT_ACCENT not in html, f"{lang}/{page}"
        assert soup.find("meta", attrs={"name": "theme-color"})["content"] == "#7A1F3D"
    chart = _soup(rebranded_site, "en", "home").find("svg")
    fills = {node.get("fill") for node in chart.find_all(True) if node.get("fill")}
    assert "#7A1F3D" in fills and "#FFE9A8" in fills


def test_the_stylesheet_uses_the_brand_only_through_its_tokens():
    css = CSS_PATH.read_text(encoding="utf-8")
    outside_root = css.replace(re.search(r":root\s*\{[^}]*\}", css).group(0), "")
    assert DEFAULT_PRIMARY.lower() not in outside_root.lower() and DEFAULT_ACCENT.lower() not in outside_root.lower()
    assert "var(--primary)" in outside_root and "var(--accent)" in outside_root


def test_a_stylesheet_that_lost_a_brand_token_is_refused_instead_of_silently_ignoring_the_settings():
    css = CSS_PATH.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="--primary"):
        site.brand_stylesheet(css.replace("--primary: ", "--main: ", 1), REBRANDED)


@pytest.mark.parametrize(
    "primary, accent, culprit",
    [
        ("#8FBF9F", "#F2C14E", "primary_color"),  # a pale primary: links and button text would vanish on white
        ("#1F6B4A", "#7A5A00", "accent_color"),  # a dark accent: the dark text on it would vanish
        ("#C9A227", "#F2C14E", "primary_color"),  # too light, and also too close to the accent
    ],
)
def test_brand_colours_that_would_fail_aa_contrast_are_named_in_plain_words(primary, accent, culprit):
    problems = brand_contrast_problems(primary, accent)
    assert problems and any(culprit in line for line in problems)
    for line in problems:
        assert "[brand]" in line and "to 1" in line and ("darker" in line or "lighter" in line)


@pytest.mark.parametrize("primary, accent", [("#1F6B4A", "#F2C14E"), ("#7A1F3D", "#FFE9A8"), ("#1D3F8A", "#FFD23F"), ("#000000", "#FFFFFF")])
def test_brand_colours_with_enough_contrast_pass_every_pair_the_stylesheet_uses(primary, accent):
    assert brand_contrast_problems(primary, accent) == []
    tokens = {**_tokens(), "white": "#FFFFFF", "primary": primary, "accent": accent}
    for fg, bg in (("primary", "white"), ("primary", "bg"), ("primary", "soft"), ("primary", "tint"), ("white", "primary"), ("ink", "accent")):
        assert contrast_ratio(tokens[fg], tokens[bg]) >= 4.5, f"{fg} on {bg}"
    assert contrast_ratio(primary, accent) >= 3


def test_two_colours_too_alike_to_tell_the_bars_apart_are_refused():
    problems = brand_contrast_problems("#1F6B4A", "#6FA58A")
    assert any("too alike" in line for line in problems)


def test_a_logo_from_the_settings_replaces_the_mark_in_the_header_on_every_page(tmp_path_factory, tmp_path):
    logo = tmp_path / "Our Logo.PNG"
    logo.write_bytes(b"\x89PNG\r\n\x1a\n not really an image, only bytes to copy")
    out = _build(tmp_path_factory, sample_dataset(), which=settings(logo=logo, base_url="https://someone.github.io/parish-carbon"))
    assert (out / "assets" / "img" / "logo.png").read_bytes() == logo.read_bytes()
    for lang, page, soup in _all_pages(out):
        image = soup.find("header").find("img")
        assert image["src"] == PREFIX + "/assets/img/logo.png" and image["alt"] == ""
        assert soup.find("link", rel="icon")["href"] == PREFIX + "/assets/img/favicon.svg"


# --- the sample-data banner ----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sample_flagged_site(tmp_path_factory):
    return _build(tmp_path_factory, sample_dataset(), which=settings(sample_data=True))


def test_while_sample_data_is_true_every_page_says_so_before_anything_else(sample_flagged_site):
    for lang, page, soup in _all_pages(sample_flagged_site):
        where = f"{lang}/{page}"
        banners = soup.find_all(class_="sample")
        assert len(banners) == 1, where
        banner = banners[0]
        assert banner.get("role") == "note"
        assert _text(banner) == f"{t(lang, 'sample_banner_lead')} {t(lang, 'sample_banner')}", where
        assert _text(banner.find("strong")) == t(lang, "sample_banner_lead")
        # It comes right after the skip link and before the header, so it is the first thing read or seen.
        body = [node for node in soup.body.find_all(recursive=False)]
        assert [node.name for node in body[:3]] == ["a", "div", "header"] and body[1] is banner, where
        assert banner.find("a") is None  # nothing to tab to: the keyboard order is unchanged
    assert "made up" in t("en", "sample_banner") and "inventadas" in t("es", "sample_banner")


def test_with_sample_data_false_no_page_has_the_banner_or_its_words(seed_site):
    for lang, page, soup in _all_pages(seed_site):
        assert soup.find(class_="sample") is None, f"{lang}/{page}"
        assert t(lang, "sample_banner_lead") not in _text(soup) and t(lang, "sample_banner") not in _text(soup)


def test_the_banner_is_the_only_difference_the_sample_flag_makes(sample_flagged_site, seed_site):
    banner = re.compile(r'<div class="sample" role="note">.*?</div>\n', re.DOTALL)
    for lang in LANGS:
        for page in PAGES:
            flagged = _html(sample_flagged_site, lang, page)
            assert banner.search(flagged)
            assert banner.sub("", flagged) == _html(seed_site, lang, page), f"{lang}/{page}"


def test_the_banner_is_styled_with_readable_tokens_and_is_not_hidden_in_print():
    banner = _declarations(".sample")
    assert banner.get("background") == "var(--accent)" and banner.get("color") == "var(--ink)"
    tokens = _tokens()
    assert contrast_ratio(tokens["ink"], tokens["accent"]) >= 4.5
    printed = [d for media, sel, d in _css_rules() if any("print" in m for m in media) and ".sample" in sel]
    assert not any(d.get("display") == "none" for d in printed)
