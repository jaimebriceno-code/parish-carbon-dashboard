"""Tests for the parish settings file (carbon.settings): what `parish.toml` may say, and how a mistake is reported.

The person who edits this file has no coding background and only ever sees the messages, so the tests pin three
things for every key: a good value loads, a bad one is refused, and the sentence names the section and the key.
"""

import re
import tomllib
from dataclasses import replace

import pytest

from carbon.settings import (
    DEFAULT_CAMPUS,
    DEFAULT_TRAVEL_LABEL,
    KNOWN_KEYS,
    SAMPLE_NAME,
    Settings,
    SettingsError,
    derive_slug,
    load_settings,
    normalize_base_url,
    split_base_url,
)
from tests.support import ROOT, SAMPLE_SETTINGS_PATH, write_settings

REAL = {"sample_data": False, "organization.name": "Grace Chapel"}  # a parish that has made the file its own


def _load(tmp_path, changes=None, **kw):
    return load_settings(write_settings(tmp_path, changes), **kw)


def _problems(tmp_path, changes):
    with pytest.raises(SettingsError) as caught:
        _load(tmp_path, changes)
    return caught.value.problems


# --- the file as shipped -------------------------------------------------------------------------------------


def test_the_sample_settings_load_with_every_value():
    s = load_settings(SAMPLE_SETTINGS_PATH)
    assert s == Settings(
        name="St. Example Church",
        city="Springfield",
        state="IL",
        zip="62701",
        contact_email="office@example.org",
        egrid_subregion="RFCW",
        slug="st-example-church-springfield-il",
        website="",
        campus_en="our church campus",
        campus_es="el campus de nuestra iglesia",
        base_url="",
        languages=("en", "es"),
        primary_color="#1F6B4A",
        accent_color="#F2C14E",
        logo=None,
        baseline_year=2025,
        travel_enabled=True,
        travel_label_en="Clergy and staff church travel",
        travel_label_es="Viajes del clero y del personal por la iglesia",
        forbidden_words=(),
        sample_data=True,
    )
    assert s.electricity_factor_id == "electricity_rfcw"
    assert (s.origin, s.path_prefix) == ("", "")


def test_every_setting_is_shown_in_the_template_file_and_explained_by_a_comment_above_it():
    # The fixture is a copy of the parish.toml the template ships. (The repository's own file is not held to
    # this: once it is a parish's, they may delete an optional line or rewrite a note.)
    text = SAMPLE_SETTINGS_PATH.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    found = {"": tuple(k for k, v in data.items() if not isinstance(v, dict))}
    found.update({k: tuple(v) for k, v in data.items() if isinstance(v, dict)})
    assert found == KNOWN_KEYS, "the template must show every setting, and only those"

    lines = text.splitlines()
    is_key = re.compile(r"[a-z_]+ = ")
    for number, line in enumerate(lines):
        if not is_key.match(line):
            continue
        above = number - 1
        while is_key.match(lines[above]):  # keys that share one note, such as city, state and zip
            above -= 1
        assert lines[above].startswith("# ") and len(lines[above]) > 20, f"{line.split(' = ')[0]} has no comment above it"
    # Plain words: the comments never send a volunteer to a developer's vocabulary.
    comments = " ".join(line for line in lines if line.startswith("#")).lower().replace("parish.toml", "")
    for jargon in ("toml", "boolean", "string", "array", "regex", "repository", "wcag", "css", "json", "config"):
        assert jargon not in comments, f"the comments say {jargon!r}"


def test_the_repositorys_own_settings_file_loads_and_the_set_of_settings_is_small():
    shipped = load_settings(ROOT / "parish.toml")
    assert shipped.languages[0] == "en"
    assert sum(len(keys) for keys in KNOWN_KEYS.values()) <= 24


# --- defaults --------------------------------------------------------------------------------------------------


def test_optional_keys_may_be_left_out_and_get_plain_defaults(tmp_path):
    left_out = {
        "sample_data": None, "organization.slug": None, "organization.website": None, "organization.campus_en": None,
        "organization.campus_es": None, "site.base_url": None, "site.languages": None, "brand.primary_color": None,
        "brand.accent_color": None, "brand.logo": None, "baseline.calendar_year": None, "travel.enabled": None,
        "travel.label_en": None, "travel.label_es": None, "privacy.forbidden_words": None,
    }
    s = _load(tmp_path, left_out)
    assert s.sample_data is True, "a file that does not say is treated as the sample"
    assert (s.campus_en, s.campus_es) == (DEFAULT_CAMPUS["en"], DEFAULT_CAMPUS["es"])
    assert (s.travel_label_en, s.travel_label_es) == (DEFAULT_TRAVEL_LABEL["en"], DEFAULT_TRAVEL_LABEL["es"])
    assert DEFAULT_TRAVEL_LABEL["en"] == "Clergy and staff church travel"
    assert s.languages == ("en", "es") and s.travel_enabled is True and s.forbidden_words == ()
    assert s.baseline_year == 2025 and s.base_url == "" and s.logo is None and s.website == ""
    assert s == replace(load_settings(SAMPLE_SETTINGS_PATH))


@pytest.mark.parametrize("key", ["organization.name", "organization.city", "organization.state", "organization.zip", "organization.contact_email", "grid.egrid_subregion"])
@pytest.mark.parametrize("how", ["missing", "empty"])
def test_the_six_things_only_the_parish_knows_are_required(tmp_path, key, how):
    problems = _problems(tmp_path, {key: None if how == "missing" else ""})
    section, name = key.split(".")
    assert any(f"[{section}] {name}" in line and "missing or empty" in line for line in problems), problems


# --- each key refuses a bad value in a sentence that names it ------------------------------------------------


@pytest.mark.parametrize(
    "key, bad, must_say",
    [
        ("organization.zip", "6270", "five-digit ZIP"),
        ("organization.zip", "62701-1234", "five-digit ZIP"),
        ("organization.zip", 62701, "between quote marks"),
        ("organization.state", "Illinois", "two-letter"),
        ("organization.contact_email", "office at example.org", "email address"),
        ("organization.slug", "St Example!", "small letters, digits and hyphens"),
        ("organization.website", "www.example.org", "https://"),
        ("organization.name", ["St. Example"], "between quote marks"),
        ("site.languages", ["es"], '["en"]'),
        ("site.languages", ["en", "fr"], '["en", "es"]'),
        ("site.languages", "en", "list of quoted words"),
        ("site.base_url", "someone.github.io/repo", "https://"),
        ("site.base_url", "https://example.org/a path", "no spaces"),
        ("brand.primary_color", "green", "six digits"),
        ("brand.accent_color", "#FC0", "six digits"),
        ("brand.logo", "logo.gif", "image file"),
        ("brand.logo", "missing-logo.png", "no such file"),
        ("grid.egrid_subregion", "RFC West", "Power Profiler"),
        ("baseline.calendar_year", "2025", "plain number"),
        ("baseline.calendar_year", 25, "plain number"),
        ("travel.enabled", "yes", "true or false"),
        ("sample_data", "false", "true or false"),
        ("privacy.forbidden_words", "rectory", "list of quoted words"),
        ("privacy.forbidden_words", ["rect*"], "plain word"),
    ],
)
def test_a_bad_value_is_refused_in_a_sentence_that_names_the_key(tmp_path, key, bad, must_say):
    problems = _problems(tmp_path, {key: bad})
    section, _, name = key.rpartition(".")
    label = f"[{section}] {name}" if section else name
    assert len(problems) == 1, problems
    assert label in problems[0] and must_say in problems[0], problems[0]
    assert problems[0].endswith(".") and "Traceback" not in problems[0] and "Error" not in problems[0]


def test_every_problem_is_reported_at_once(tmp_path):
    problems = _problems(tmp_path, {"organization.zip": "1", "organization.state": "Ill", "brand.primary_color": "blue", "travel.enabled": 1})
    assert len(problems) == 4


def test_a_misspelt_key_is_named_with_the_closest_real_one(tmp_path):
    path = write_settings(tmp_path)
    path.write_text(path.read_text(encoding="utf-8").replace("languages =", "langauges ="), encoding="utf-8")
    with pytest.raises(SettingsError) as caught:
        load_settings(path)
    assert caught.value.problems == ["[site] langauges is not a setting this file knows. Did you mean languages?"]


def test_an_unknown_section_and_a_key_above_the_first_section_are_explained(tmp_path):
    path = write_settings(tmp_path)
    path.write_text('name = "Lost"\n' + path.read_text(encoding="utf-8") + '\n[hosting]\nserver = "x"\n', encoding="utf-8")
    with pytest.raises(SettingsError) as caught:
        load_settings(path)
    joined = " ".join(caught.value.problems)
    assert "name is written above the first [section] line, but it belongs under [organization]" in joined
    assert "[hosting] is not a section this file knows" in joined and "[organization]" in joined


def test_a_file_that_is_not_valid_is_explained_without_a_traceback(tmp_path):
    path = write_settings(tmp_path)
    path.write_text(path.read_text(encoding="utf-8").replace('city = "Springfield"', 'city = "Springfield'), encoding="utf-8")
    with pytest.raises(SettingsError) as caught:
        load_settings(path)
    (problem,) = caught.value.problems
    assert problem.startswith("parish.toml could not be read:") and "missing quote mark" in problem and "line" in problem


def test_a_missing_file_says_where_it_belongs(tmp_path):
    with pytest.raises(SettingsError) as caught:
        load_settings(tmp_path / "parish.toml")
    assert "parish.toml was not found" in caught.value.problems[0]


def test_settings_error_is_a_value_error_whose_text_holds_every_problem(tmp_path):
    with pytest.raises(ValueError, match="zip") as caught:
        _load(tmp_path, {"organization.zip": "1", "organization.state": "Ill"})
    assert "state" in str(caught.value)


# --- values are tidied, never guessed --------------------------------------------------------------------------


def test_state_subregion_and_colours_are_stored_in_capitals_and_text_is_trimmed(tmp_path):
    s = _load(tmp_path, {"organization.state": "il", "grid.egrid_subregion": "rfcw", "brand.primary_color": "#1f6b4a", "organization.name": "  St. Example Church "})
    assert (s.state, s.egrid_subregion, s.primary_color, s.name) == ("IL", "RFCW", "#1F6B4A", "St. Example Church")
    assert s.electricity_factor_id == "electricity_rfcw"


@pytest.mark.parametrize(
    "name, city, state, slug",
    [
        ("St. Example Church", "Springfield", "IL", "st-example-church-springfield-il"),
        ("Iglesia de San José", "Santa Fe", "NM", "iglesia-de-san-jose-santa-fe-nm"),
        ("  Christ  Church (Cathedral) ", "St. Louis", "MO", "christ-church-cathedral-st-louis-mo"),
    ],
)
def test_a_blank_slug_is_made_from_the_name_city_and_state(tmp_path, name, city, state, slug):
    assert derive_slug(name, city, state) == slug
    changes = {"organization.name": name, "organization.city": city, "organization.state": state, "organization.slug": ""}
    assert _load(tmp_path, changes).slug == slug
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug)


def test_a_slug_that_is_given_is_kept(tmp_path):
    assert _load(tmp_path, {"organization.slug": "st-example"}).slug == "st-example"


# --- the base URL ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "given, stored, origin, prefix",
    [
        ("https://someone.github.io/parish-carbon/", "https://someone.github.io/parish-carbon", "https://someone.github.io", "/parish-carbon"),
        ("https://someone.github.io/parish-carbon", "https://someone.github.io/parish-carbon", "https://someone.github.io", "/parish-carbon"),
        ("https://carbon.example.org/", "https://carbon.example.org", "https://carbon.example.org", ""),
        ("http://127.0.0.1:8000", "http://127.0.0.1:8000", "http://127.0.0.1:8000", ""),
        ("https://example.org/a/b.c/d_e~f", "https://example.org/a/b.c/d_e~f", "https://example.org", "/a/b.c/d_e~f"),
    ],
)
def test_a_base_url_may_have_a_path_and_is_stored_without_a_trailing_slash(tmp_path, given, stored, origin, prefix):
    s = _load(tmp_path, {"site.base_url": given})
    assert (s.base_url, s.origin, s.path_prefix) == (stored, origin, prefix)
    assert normalize_base_url(given) == stored and split_base_url(given) == (origin, prefix)


@pytest.mark.parametrize("bad", ["example.org", "ftp://example.org", "https://", "https://a.org?x=1", "https://a.org/#top", "https://a.org//x", "https://a.org/a b", "/repo"])
def test_split_base_url_refuses_what_is_not_an_address(bad):
    with pytest.raises(ValueError, match="base URL"):
        split_base_url(bad)


def test_a_blank_base_url_is_allowed_and_means_root_relative_links(tmp_path):
    s = _load(tmp_path, {"site.base_url": ""})
    assert (s.base_url, s.origin, s.path_prefix) == ("", "", "")
    assert split_base_url(None) == ("", "") and normalize_base_url("") == ""


def test_the_fallback_base_url_is_used_only_when_the_file_leaves_it_blank(tmp_path):
    fallback = "https://someone.github.io/parish-carbon"
    assert _load(tmp_path / "a", fallback_base_url=fallback).base_url == fallback
    assert _load(tmp_path / "b", fallback_base_url=fallback + "/").base_url == fallback
    own = _load(tmp_path / "c", {"site.base_url": "https://carbon.example.org"}, fallback_base_url=fallback)
    assert own.base_url == "https://carbon.example.org"
    assert _load(tmp_path / "d", fallback_base_url="").base_url == "" == _load(tmp_path / "e", fallback_base_url=None).base_url


# --- languages -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value, expected", [(["en"], ("en",)), (["en", "es"], ("en", "es"))])
def test_languages_is_english_or_english_and_spanish(tmp_path, value, expected):
    assert _load(tmp_path, {"site.languages": value}).languages == expected


def test_an_english_only_site_needs_no_spanish_wording(tmp_path):
    s = _load(tmp_path, {"site.languages": ["en"], "organization.campus_es": None, "travel.label_es": None})
    assert s.languages == ("en",) and s.campus("en") == "our church campus"


# --- brand: colours and their contrast, the logo -----------------------------------------------------------------


def test_colours_with_too_little_contrast_are_refused_when_the_file_is_read(tmp_path):
    problems = _problems(tmp_path, {"brand.primary_color": "#9AD0B0"})
    assert any("[brand] primary_color #9AD0B0" in line and "at least 4.5 to 1" in line and "darker" in line for line in problems)
    # Easy to tell from its accent, but still too pale for text: one problem, about the primary colour only.
    (problem,) = _problems(tmp_path, {"brand.primary_color": "#4F7FA8", "brand.accent_color": "#FFF3C4"})
    assert "primary_color #4F7FA8" in problem and "too light" in problem
    problems = _problems(tmp_path, {"brand.accent_color": "#5A4300"})
    assert any("[brand] accent_color #5A4300" in line and "lighter" in line for line in problems)


def test_good_colours_of_any_hue_load(tmp_path):
    s = _load(tmp_path, {"brand.primary_color": "#7a1f3d", "brand.accent_color": "#ffe9a8"})
    assert (s.primary_color, s.accent_color) == ("#7A1F3D", "#FFE9A8")


def test_a_logo_is_found_beside_the_settings_file_and_must_be_an_image_that_exists(tmp_path):
    (tmp_path / "art").mkdir()
    (tmp_path / "art" / "logo.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    s = _load(tmp_path, {"brand.logo": "art/logo.svg"})
    assert s.logo == tmp_path / "art" / "logo.svg" and s.logo.is_file()


# --- travel, privacy, baseline, sample flag --------------------------------------------------------------------


def test_travel_can_be_switched_off_and_renamed(tmp_path):
    s = _load(tmp_path, {"travel.enabled": False, "travel.label_en": "Pastor's visits", "travel.label_es": "Visitas pastorales"})
    assert s.travel_enabled is False
    assert (s.travel_label("en"), s.travel_label("es")) == ("Pastor's visits", "Visitas pastorales")


def test_forbidden_words_are_kept_as_given_and_may_be_phrases_with_accents(tmp_path):
    words = ["rectory", "rectories", "casa parroquial", "sacristía", "St. Mark's" .replace(".", "")]
    assert _load(tmp_path, {"privacy.forbidden_words": words}).forbidden_words == tuple(words)


def test_the_baseline_year_is_a_plain_year(tmp_path):
    assert _load(tmp_path, {"baseline.calendar_year": 2027}).baseline_year == 2027
    assert Settings.__dataclass_fields__["baseline_year"].default == 2025


def test_sample_data_false_needs_the_parishs_own_name(tmp_path):
    (problem,) = _problems(tmp_path, {"sample_data": False})
    assert "sample_data is false" in problem and SAMPLE_NAME in problem
    s = _load(tmp_path, REAL)
    assert s.sample_data is False and s.name == "Grace Chapel" and s.slug == "grace-chapel-springfield-il"


# --- the words the pages are filled with ----------------------------------------------------------------------


def test_words_give_each_language_its_own_campus_and_travel_label_and_a_capitalised_campus(tmp_path):
    s = _load(tmp_path, {"organization.campus_en": "our church and school campus", "organization.campus_es": "nuestro campus de iglesia y escuela"})
    en, es = s.words("en"), s.words("es")
    assert (en["campus"], en["Campus"]) == ("our church and school campus", "Our church and school campus")
    assert (es["campus"], es["Campus"]) == ("nuestro campus de iglesia y escuela", "Nuestro campus de iglesia y escuela")
    assert en["travel_label"] == "Clergy and staff church travel" and es["travel_label"].startswith("Viajes del clero")
    for words in (en, es):
        assert (words["name"], words["city"], words["state"], words["zip"]) == ("St. Example Church", "Springfield", "IL", "62701")
        assert (words["subregion"], words["year"]) == ("RFCW", "2025")
