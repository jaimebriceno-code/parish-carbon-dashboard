"""Tests for the English and Spanish string table: carbon.i18n.

The table grows as the page templates need more strings, so these tests are generic: they scan every string in
`STRINGS` rather than a fixed list of keys, except where a rule names specific keys (the travel card wording).
"""

import re
import string

import pytest

from carbon import i18n
from carbon.i18n import (
    ALLOWED_OFFSET_PHRASES,
    OFFSET_TERMS,
    ORG_PLACEHOLDERS,
    STRINGS,
    offset_terms_outside_allowed,
    parity_errors,
    t,
)
from carbon.models import COMMUTE_QUALITIES, DATA_QUALITIES
from carbon.site import _factor_name
from tests.support import SETTINGS, TRAVEL_EN, TRAVEL_ES, sample_dataset, settings

LANGS = ("en", "es")


def said(lang, key, **fmt):
    """The string as the sample parish's site says it: its own words filled in, then `fmt`."""
    return t(lang, key, **{**SETTINGS.words(lang), **fmt})


def all_strings():
    """Every (lang, key, text) in the table."""
    return [(lang, key, text) for lang in LANGS for key, text in sorted(STRINGS[lang].items())]


def placeholders(text):
    return {name for _, name, _, _ in string.Formatter().parse(text) if name is not None}


# --- shape and parity --------------------------------------------------------


def test_strings_has_exactly_english_and_spanish():
    assert set(STRINGS) == {"en", "es"}


def test_parity_errors_empty():
    assert parity_errors() == []


def test_parity_errors_reports_a_key_missing_from_spanish(monkeypatch):
    monkeypatch.delitem(STRINGS["es"], "nav_data")
    errors = parity_errors()
    assert any("nav_data" in e and "es" in e for e in errors), errors


def test_parity_errors_reports_a_key_missing_from_english(monkeypatch):
    monkeypatch.delitem(STRINGS["en"], "nav_data")
    errors = parity_errors()
    assert any("nav_data" in e and "en" in e for e in errors), errors


def test_parity_errors_reports_a_key_only_in_one_language(monkeypatch):
    monkeypatch.setitem(STRINGS["en"], "zz_only_english", "Only here")
    assert any("zz_only_english" in e for e in parity_errors())


def test_parity_errors_reports_mismatched_placeholders(monkeypatch):
    monkeypatch.setitem(STRINGS["es"], "updated_on", "Datos actualizados")
    errors = parity_errors()
    assert any("updated_on" in e and "date" in e for e in errors), errors


def test_parity_errors_reports_empty_strings(monkeypatch):
    monkeypatch.setitem(STRINGS["es"], "nav_data", "   ")
    errors = parity_errors()
    assert any("nav_data" in e and "empty" in e for e in errors), errors


def test_parity_errors_reports_positional_placeholders(monkeypatch):
    monkeypatch.setitem(STRINGS["en"], "nav_data", "Data {}")
    monkeypatch.setitem(STRINGS["es"], "nav_data", "Datos {}")
    assert any("nav_data" in e and "positional" in e for e in parity_errors())


def test_parity_errors_reports_a_missing_language(monkeypatch):
    monkeypatch.delitem(STRINGS, "es")
    assert parity_errors(), "a table without Spanish must not pass"


def test_building_the_table_rejects_a_duplicate_key():
    with pytest.raises(ValueError, match="dup"):
        i18n._build_table((("dup", "One", "Uno"), ("dup", "Two", "Dos")))


# --- t() ---------------------------------------------------------------------


def test_missing_key_raises():
    with pytest.raises(KeyError):
        t("en", "no_such_key")
    with pytest.raises(KeyError):
        t("es", "no_such_key")


def test_unknown_language_raises():
    with pytest.raises(KeyError):
        t("fr", "nav_data")


def test_t_returns_the_text_in_each_language():
    assert t("en", "nav_data") == "Data"
    assert t("es", "nav_data") == "Datos"


def test_t_formats_named_placeholders():
    assert t("en", "updated_on", date="2026-11-17") == "Data last updated 2026-11-17"
    assert t("es", "updated_on", date="2026-11-17") == "Datos actualizados el 2026-11-17"


def test_t_with_a_missing_placeholder_value_raises():
    with pytest.raises(KeyError):
        t("en", "updated_on")


def test_t_ignores_unused_format_arguments():
    assert t("en", "nav_data", date="2026-11-17") == "Data"


# --- the shape of every string -----------------------------------------------


def test_strings_are_plain_text_with_named_placeholders_only():
    # Templates autoescape, so markup inside a string would be shown literally. Links and bold labels are
    # built in the template from separate keys.
    for lang, key, text in all_strings():
        assert not re.search(r"[<>]", text), f"{lang}:{key} contains markup"
        assert not re.search(r"&\w+;", text), f"{lang}:{key} contains an entity"
        assert text == text.strip(), f"{lang}:{key} has leading or trailing space"
        for name in placeholders(text):
            assert name and not name.isdigit(), f"{lang}:{key} has a positional placeholder"


def test_every_page_group_has_strings():
    keys = set(STRINGS["en"])
    for prefix in ("site_", "nav_", "home_", "travel_", "data_", "method_", "about_", "dq_", "month_short_"):
        assert any(k.startswith(prefix) for k in keys), f"no strings with prefix {prefix}"


def test_month_names_cover_all_twelve_months():
    for lang in LANGS:
        names = [t(lang, f"month_short_{m:02d}") for m in range(1, 13)]
        assert len(set(names)) == 12, names


def test_every_data_quality_has_a_label_and_an_explanation():
    for lang in LANGS:
        for quality in DATA_QUALITIES:
            assert t(lang, f"dq_{quality}")
            assert t(lang, f"method_dq_{quality}")
        for quality in COMMUTE_QUALITIES:
            assert t(lang, f"travel_dq_{quality}")


def test_every_factor_in_the_sample_data_has_a_display_name():
    for factor in sample_dataset().factors:
        for lang in LANGS:
            name = _factor_name(lang, factor.factor_id)
            assert name and name != factor.factor_id, factor.factor_id


def test_any_subregions_electricity_factor_is_named_with_its_code_and_an_unknown_factor_by_its_id():
    assert _factor_name("en", "electricity_rfcw") == "Electricity, regional grid RFCW (scope 2)"
    assert _factor_name("es", "electricity_zzzt") == "Electricidad, red regional ZZZT (alcance 2)"
    assert _factor_name("en", "something_else") == "something_else"


# --- nothing in the table belongs to one parish -------------------------------------------------------------


def test_the_parish_placeholders_are_filled_from_the_settings_in_both_languages():
    other = settings(
        name="Grace Chapel", city="Rivertown", state="OH", zip="44000", egrid_subregion="ZZZT", baseline_year=2027,
        campus_en="our chapel and school", campus_es="nuestra capilla y escuela",
        travel_label_en="Pastor's visits", travel_label_es="Visitas pastorales",
    )
    assert t("en", "site_name", **other.words("en")) == "Grace Chapel"
    assert t("en", "site_tagline", **other.words("en")) == "Carbon dashboard · Rivertown, OH"
    assert t("en", "home_lead", **other.words("en")).startswith("Our chapel and school, from natural gas")
    assert t("es", "home_lead", **other.words("es")).startswith("Nuestra capilla y escuela, por calefacción")
    assert "We count our chapel and school:" in t("en", "method_boundary_body", **other.words("en"))
    assert "subregion ZZZT" in t("en", "method_grid_note", **other.words("en"))
    assert "calendar year 2027" in t("en", "home_why_window", **other.words("en"))
    assert "año calendario 2027" in t("es", "method_baseline_body", **other.words("es"))
    assert t("en", "travel_heading", **other.words("en")) == "Pastor's visits"
    assert t("es", "data_file_travel", **other.words("es")) == "Visitas pastorales, por mes"
    assert "Grace Chapel · Rivertown, OH 44000" in t("en", "footer_text", contact="a@b.org", license="L", **other.words("en"))


def test_settings_words_cover_exactly_the_parish_placeholders():
    for lang in LANGS:
        assert set(SETTINGS.words(lang)) == set(ORG_PLACEHOLDERS)


def test_every_string_can_be_said_with_the_parish_words_and_its_own_values():
    for lang, key, text in all_strings():
        own = {name: "x" for name in placeholders(text) - ORG_PLACEHOLDERS}
        spoken = said(lang, key, **own)
        assert "{" not in spoken and "}" not in spoken, f"{lang}:{key}"


def test_no_string_names_a_parish_a_place_a_utility_or_a_diocese():
    # Proper names come from parish.toml. The only capitalised names the table may hold are the agencies,
    # standards and units the method rests on.
    named = re.compile(
        r"\bSt\.?\s|\bSaint\b|\bSan\b|\bSanta\b|Episcopal|Diocese of|Diócesis de|Chicago|Illinois|\bIL\b|Parish of|"
        r"\b(ComEd|Nicor|Ameren|PG&E|Duke|Xcel)\b|daycare|guardería|\brector",
        re.IGNORECASE,
    )
    for lang, key, text in all_strings():
        assert not named.search(text), f"{lang}:{key} names something that belongs in parish.toml: {text!r}"



# Spanish strings that are legitimately the same as the English: proper names, units and symbols.
SAME_IN_BOTH_LANGUAGES = {
    "site_name",
    "page_title",
    "lang_switch_label",
    "lang_name_en",
    "lang_name_es",
    "col_kwh",
    "col_therms",
    "col_factor",
    "col_tco2e",
    "row_total",
    "chart_y_axis",
    "unit_therms",
    "unit_kwh",
    "travel_heading",  # the parish's own label, a placeholder in both
}


def test_spanish_is_translated_not_copied():
    untranslated = [
        key
        for key in STRINGS["en"]
        if key in STRINGS["es"] and STRINGS["en"][key] == STRINGS["es"][key] and key not in SAME_IN_BOTH_LANGUAGES
    ]
    assert untranslated == [], f"Spanish text is identical to English for: {untranslated}"


# --- the offset rule ---------------------------------------------------------


def test_the_allowed_offset_phrases_are_the_agreed_ones():
    assert ALLOWED_OFFSET_PHRASES == ("not an offset", "no una compensación")


def test_offset_only_appears_inside_not_an_offset():
    for lang, key, text in all_strings():
        remaining = text
        for phrase in ALLOWED_OFFSET_PHRASES:
            remaining = re.sub(re.escape(phrase), "", remaining, flags=re.IGNORECASE)
        for term in OFFSET_TERMS:
            assert term not in remaining.lower(), f"{lang}:{key} uses '{term}' outside the allowed phrase: {text!r}"


def test_offset_terms_outside_allowed_helper():
    assert offset_terms_outside_allowed("avoided emissions, not an offset") == []
    assert offset_terms_outside_allowed("NOT AN OFFSET") == []
    assert offset_terms_outside_allowed("emisiones evitadas, no una compensación") == []
    assert offset_terms_outside_allowed("this is an offset") == ["offset"]
    assert offset_terms_outside_allowed("carbon offsets") == ["offset"]
    assert "compensación" in offset_terms_outside_allowed("una compensación de carbono")
    assert "compensar" in offset_terms_outside_allowed("para compensar")
    assert offset_terms_outside_allowed("Nothing to see") == []


def test_the_avoided_emissions_text_carries_the_required_wording():
    assert "avoided emissions compared with a gasoline car, not an offset" in t("en", "travel_stat_avoided").lower()
    assert (
        "emisiones evitadas en comparación con un auto de gasolina, no una compensación"
        in t("es", "travel_stat_avoided").lower()
    )


# --- the travel card ---------------------------------------------------------


def test_travel_card_title_and_charging_sentence_are_exact():
    assert said("en", "travel_heading") == TRAVEL_EN == "Clergy and staff church travel"
    assert said("es", "travel_heading") == TRAVEL_ES == "Viajes del clero y del personal por la iglesia"
    assert t("en", "travel_charging_note") == "Charging electricity is counted on this line, not in the campus meters."
    assert t("es", "travel_charging_note") == "La electricidad de carga se cuenta en esta línea, no en los medidores del campus."


def test_travel_card_says_scope_3_and_is_never_added_to_the_campus_total():
    assert "scope 3" in t("en", "travel_tag").lower()
    assert "alcance 3" in t("es", "travel_tag").lower()
    assert "never added" in t("en", "travel_intro").lower()
    assert "nunca se suman" in t("es", "travel_intro").lower()


# --- words the public text must never use -------------------------------------

COMMUTE = re.compile(r"\bcommut\w*|\bconmut\w*|desplazamiento al trabajo|viaje al trabajo|trayecto al trabajo", re.IGNORECASE)


def test_the_commute_pattern_finds_the_word_in_both_languages():
    assert COMMUTE.search("a daily commute")
    assert COMMUTE.search("Commuting")
    assert COMMUTE.search("desplazamiento al trabajo")


def test_public_wording_says_travel_and_never_commuting():
    # The travel line is church business, not anyone's trip to work; "commute" is only the data files' name for it.
    for lang, key, text in all_strings():
        assert not COMMUTE.search(text), f"{lang}:{key} mentions commuting: {text!r}"


def test_solar_is_mentioned_only_in_the_solar_section():
    # The About page shows solar only when a solar_connected milestone exists, so no other string may mention it.
    for lang, key, text in all_strings():
        if re.search(r"\bsolar(es)?\b", text, re.IGNORECASE):
            assert key.startswith("about_solar_"), f"{lang}:{key} mentions solar outside the solar section"
    assert any(k.startswith("about_solar_") for k in STRINGS["en"])


def test_no_string_mentions_leases_or_vendors():
    pattern = re.compile(r"\b(lease|leases|leasing|vendor|vendors|arrendamiento|arriendo|alquiler|proveedor|proveedores)\b", re.IGNORECASE)
    for lang, key, text in all_strings():
        assert not pattern.search(text), f"{lang}:{key}: {text!r}"


def test_no_carbon_neutral_or_net_zero_claims():
    claim = re.compile(r"carbon[- ]neutral|carbono neutral|neutral en carbono|neutralidad", re.IGNORECASE)
    for lang, key, text in all_strings():
        assert not claim.search(text), f"{lang}:{key} makes a carbon neutral claim: {text!r}"


def test_no_string_says_net_zero_at_all():
    # A target belongs to whoever set it. The template makes no such statement for any parish.
    net_zero = re.compile(r"net[- ]zero|cero emisiones netas|emisiones netas cero|cero neto", re.IGNORECASE)
    for lang, key, text in all_strings():
        assert not net_zero.search(text), f"{lang}:{key}: {text!r}"


def test_why_we_measure_makes_no_claim_about_anyone_else():
    for lang in LANGS:
        body = said(lang, "about_why_body")
        assert "{" not in body
    assert "We cannot reduce what we do not measure" in t("en", "about_why_body")
    assert "No podemos reducir lo que no medimos" in t("es", "about_why_body")


# --- accuracy of the methodology and card copy ---------------------------------------------------------------


def test_no_string_says_energy_amounts_never_change():
    # Estimates are replaced by exact bill figures, so energy amounts do change; only the factors are applied to them.
    never_change = {
        "en": re.compile(r"\bnever\s+chang|energy amounts? (do not|don't|will not|won't) change", re.IGNORECASE),
        "es": re.compile(r"\bnunca\s+cambia|no cambian las cantidades de energ", re.IGNORECASE),
    }
    for lang, key, text in all_strings():
        assert not never_change[lang].search(text), f"{lang}:{key}: {text!r}"
    assert "Changing the factors does not change the energy amounts" in t("en", "method_factors_recalc")
    assert "recalculate all months" in t("en", "method_factors_recalc")
    assert "Cambiar los factores no cambia las cantidades de energía" in t("es", "method_factors_recalc")
    assert "recalculamos cada mes ya registrado" in t("es", "method_factors_recalc")


def test_excluded_body_mentions_meters_not_yet_received():
    assert "any meters we have not yet received bills for" in t("en", "method_excluded_body")
    assert "cualquier medidor del que todavía no hayamos recibido facturas" in t("es", "method_excluded_body")


def test_travel_intro_es_does_not_start_with_manejo():
    intro = t("es", "travel_intro")
    assert not intro.lower().startswith("manejo"), intro
    assert intro.startswith("Viajes en auto eléctrico por asuntos de la iglesia.")
    assert "nunca se suman" in intro.lower()


def test_miles_unit_present_in_travel_note():
    assert "{miles_per_day} miles per working day" in STRINGS["en"]["travel_miles_estimate_note"]
    assert "{miles_per_day} millas por día laborable" in STRINGS["es"]["travel_miles_estimate_note"]
    assert t("en", "travel_miles_estimate_note", miles_per_day=80).startswith("Miles are an estimate at 80 miles per working day")


def test_us_spellings_in_english_strings():
    british = re.compile(r"\b(licence|licences|labelled|labelling)\b", re.IGNORECASE)
    for key, text in sorted(STRINGS["en"].items()):
        assert not british.search(text), f"en:{key} uses a British spelling: {text!r}"
    assert "license" in said("en", "footer_text", contact="x@example.org", license="CC BY 4.0").lower()
    assert "labeled" in said("en", "method_baseline_body")


def test_no_dollar_amounts_or_long_digit_runs_in_strings():
    for lang, key, text in all_strings():
        assert not re.search(r"\$\s*\d", text), f"{lang}:{key} has a dollar amount"
        assert not re.search(r"\d(?:[ -]?\d){8,}", text), f"{lang}:{key} has a long digit run"


def test_strings_added_for_the_pages_exist_in_both_languages_with_the_same_placeholders():
    # Added in Task 6: an estimated badge for a baseline that is not provisional, travel coverage and cell text,
    # and the empty Data page message.
    assert t("en", "home_badge_estimated", pct=12) == "12% estimated"
    assert t("es", "home_badge_estimated", pct=12) == "12 % estimado"
    assert t("en", "travel_months_note", n=5) == "Entries so far cover 5 of the twelve months."
    assert t("es", "travel_months_note", n=5) == "Las entradas hasta ahora cubren 5 de los doce meses."
    for key in ("travel_cell_already_counted", "data_none_yet"):
        assert STRINGS["en"][key] and STRINGS["es"][key] and STRINGS["en"][key] != STRINGS["es"][key]
    assert parity_errors() == []


# --- Task 6 fix round 1: the lead-in, the no-travel wording and the approximate solar date ------------------

TRAVEL_WORDS = re.compile(r"travel|viaje|vehicle|vehículo|rector|gasoline|gasolina|scope 3|alcance 3|avoided|evitad", re.IGNORECASE)


def test_why_provisional_lead_is_its_own_string_and_the_reasons_do_not_repeat_it():
    assert t("en", "home_why_lead") == "Why provisional?"
    assert t("es", "home_why_lead") == "¿Por qué provisional?"
    for lang in LANGS:
        for key in ("home_why_estimates", "home_why_window"):
            assert "provisional?" not in said(lang, key).lower(), f"{lang}:{key} still opens with the lead-in"
    assert said("en", "home_why_estimates").startswith("Figures marked as estimates")
    assert said("en", "home_why_window").startswith("This baseline covers")
    assert said("es", "home_why_estimates").startswith("Las cifras marcadas como estimaciones")
    assert said("es", "home_why_window").startswith("Esta línea base cubre")


def test_no_travel_variants_exist_and_never_mention_travel_in_either_language():
    for lang in LANGS:
        for key in ("method_excluded_body_no_travel", "method_term_scope_no_travel"):
            assert not TRAVEL_WORDS.search(t(lang, key)), f"{lang}:{key}: {t(lang, key)!r}"
    # The original wording stays for a dataset that has travel.
    assert "the travel line below" in t("en", "method_excluded_body")
    assert "Scope 3" in t("en", "method_term_scope") and "alcance 3" in t("es", "method_term_scope").lower()


def test_no_travel_excluded_body_keeps_the_other_exclusions():
    assert "Water, refrigerants" in t("en", "method_excluded_body_no_travel")
    assert "meters we have not yet received bills for" in t("en", "method_excluded_body_no_travel")
    assert "El agua, los refrigerantes" in t("es", "method_excluded_body_no_travel")
    assert "cualquier medidor del que todavía no hayamos recibido facturas" in t("es", "method_excluded_body_no_travel")


def test_no_travel_scope_text_still_defines_scope_1_and_2():
    for lang in LANGS:
        text = t(lang, "method_term_scope_no_travel")
        assert ("Scope 1" in text and "Scope 2" in text) if lang == "en" else ("alcance 1" in text.lower() and "alcance 2" in text.lower())


def test_solar_period_sentence_says_in_not_on_for_a_month_or_a_year():
    assert t("en", "about_solar_body_period", date="Mar 2027").startswith("A solar system was connected on our campus in Mar 2027.")
    assert t("es", "about_solar_body_period", date="2027").startswith("Se conectó un sistema solar en nuestro campus en 2027.")
    assert t("en", "about_solar_body", date="Mar 9, 2027").startswith("A solar system was connected on our campus on Mar 9, 2027.")
