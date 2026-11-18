"""Tests for the content guard (carbon.guard): the forbidden-content scan the build runs before it publishes.

The guard reports finding codes, never the text that matched, so a pasted account number cannot reach a log.
Every pattern is proven twice: it fires on a planted example, and it stays quiet on the legitimate text the site
really contains (ISO dates, the ZIP code, thousands separators, "greenhouse gas", SVG coordinates).
The only wording rule that varies by parish is its own list of forbidden words, so those tests pass a list in.
"""

import pytest

from carbon.guard import SCANNED_SUFFIXES, scan_text, scan_tree

# --- every pattern fires on a planted example ---------------------------------------------------------

# A parish's own list from parish.toml, [privacy] forbidden_words: single words and a phrase, in two languages.
WORDS = ("rectory", "rectories", "casa parroquial", "Old Annex")


@pytest.mark.parametrize(
    "text",
    [
        "Charger installed at the rectory",
        "THE RECTORY",
        "the rectory's meter",
        "rectory",
        "(rectory)",
        "Two rectories on the block",
        "Se carga en la casa parroquial",
        "CASA PARROQUIAL.",
        "la casa\nparroquial",
        "the old annex roof",
    ],
)
def test_each_forbidden_word_or_phrase_is_flagged_whole_and_in_any_capitals(text):
    assert scan_text(text, WORDS) == ["FORBIDDEN_WORD"]


@pytest.mark.parametrize(
    "text",
    [
        "the directory of rectoryless parishes",  # the word only inside longer words
        "rectory_meter",  # an underscore joins it to another word
        "la casa del párroco",  # one word of the phrase without the other
        "an older annex",
        "rectors and directories",
    ],
)
def test_a_forbidden_word_inside_a_longer_word_or_half_a_phrase_is_not_flagged(text):
    assert scan_text(text, WORDS) == []


def test_with_no_forbidden_words_nothing_is_refused_for_its_wording_alone():
    # The default list is empty: a word is only forbidden when the parish lists it.
    for text in ("the rectory", "the house", "la casa", "a commuter shuttle", "Commuting by car"):
        assert scan_text(text) == []
        assert scan_text(text, ()) == []


def test_forbidden_words_are_found_in_page_text_attributes_and_other_files(tmp_path):
    _write(tmp_path, "es/index.html", "<p>Se carga en la casa parroquial.</p>")
    _write(tmp_path, "es/about/index.html", '<img src="/x.png" alt="la rectory">')
    _write(tmp_path, "index.html", "<p>Two <em>rectories</em> on the block.</p>")
    _write(tmp_path, "api/v1/milestones.json", '{"label_en": "Old annex boiler"}')
    _write(tmp_path, "data/index.html", "<p>Nothing to see.</p>")
    assert scan_tree(tmp_path, WORDS) == {
        "es/index.html": ["FORBIDDEN_WORD"],
        "es/about/index.html": ["FORBIDDEN_WORD"],
        "index.html": ["FORBIDDEN_WORD"],
        "api/v1/milestones.json": ["FORBIDDEN_WORD"],
    }
    assert scan_tree(tmp_path) == {}


def test_a_forbidden_phrase_split_by_inline_markup_is_still_found(tmp_path):
    _write(tmp_path, "index.html", "<p>la casa <strong>parroquial</strong></p>")
    assert scan_tree(tmp_path, WORDS) == {"index.html": ["FORBIDDEN_WORD"]}


def test_forbidden_words_with_accents_match_however_the_accent_is_stored():
    import unicodedata

    word = "sacristía"
    decomposed = unicodedata.normalize("NFD", "La SACRISTÍA vieja")
    assert scan_text(decomposed, [word]) == ["FORBIDDEN_WORD"]
    assert scan_text("La sacristia vieja", [word]) == []  # a different spelling is a different word


@pytest.mark.parametrize("text", ["costs $5", "$ 5", "$1,200", "total: $\n9", "US$12"])
def test_a_dollar_sign_followed_by_a_digit_is_flagged(text):
    assert scan_text(text) == ["DOLLAR_AMOUNT"]


@pytest.mark.parametrize("text", ["USD 5", "usd5", "Usd 1,200", "total: USD\n9", "paid USD  40.00"])
def test_usd_followed_by_a_digit_is_flagged_as_a_dollar_amount(text):
    assert scan_text(text) == ["DOLLAR_AMOUNT"]


def test_a_usd_amount_is_found_in_page_text_too(tmp_path):
    _write(tmp_path, "data/index.html", "<p>The bill was USD 120 this month.</p>")
    assert scan_tree(tmp_path) == {"data/index.html": ["DOLLAR_AMOUNT"]}


@pytest.mark.parametrize(
    "text",
    [
        "1234567890",
        "123456789",
        "acct 1234 5678 90",
        "1234-5678-90",
        "12-34-56-78-9",
        "ref 1 2 3 4 5 6 7 8 9",
    ],
)
def test_a_run_of_nine_or_more_digits_is_flagged_even_with_single_spaces_or_hyphens(text):
    assert scan_text(text) == ["LONG_DIGIT_RUN"]


@pytest.mark.parametrize(
    "text",
    [
        "We bought an offset",
        "Carbon OFFSETS",
        "offset",
        "La compensación",
        "para compensar",
        "Compensa las emisiones",
        "avoided emissions, not an offset, and an offset again",
        "no una compensación y otra compensación",
    ],
)
def test_the_offset_words_are_flagged_outside_the_allowed_phrases(text):
    assert scan_text(text) == ["OFFSET_TERM"]


@pytest.mark.parametrize(
    "text",
    [
        "avoided emissions compared with a gasoline car, not an offset",
        "Avoided emissions compared with a gasoline car; NOT AN OFFSET.",
        "emisiones evitadas en comparación con un auto de gasolina, no una compensación",
        "not an\n  offset",
        "not an offset",
    ],
)
def test_the_two_allowed_offset_phrases_are_not_flagged(text):
    assert scan_text(text) == []


def test_several_problems_give_each_code_once_in_a_sorted_list():
    text = "rectory rectory $5 $6 1234567890 1234567891 offset offset"
    assert scan_text(text, WORDS) == ["DOLLAR_AMOUNT", "FORBIDDEN_WORD", "LONG_DIGIT_RUN", "OFFSET_TERM"]
    assert scan_text(text) == ["DOLLAR_AMOUNT", "LONG_DIGIT_RUN", "OFFSET_TERM"]


def test_findings_are_codes_and_never_the_matched_text():
    text = "acct 1234 5678 90 at the rectory, $5,000, offset"
    found = scan_text(text, WORDS)
    assert "FORBIDDEN_WORD" in found and "rectory" not in " ".join(found).lower()
    assert found == sorted(set(found))
    for code in found:
        assert code.isupper() and code.replace("_", "").isalpha()
        assert not any(ch.isdigit() for ch in code)
    assert "1234" not in " ".join(found) and "5,000" not in " ".join(found)


# --- and stays quiet on legitimate text ------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Data last updated 2026-11-17",
        "2025-06-25",
        "St. Example Church · Springfield, IL 62701",
        "ZIP 62701",
        "33,430 kWh",
        "6,029.25 therms",
        "0.4155151 kg CO2e/kWh",
        "greenhouse gas emissions",
        "Greenhouse Gases",
        "warehouse and household",
        "warehouse and household",
        "greenhouse",
        "Greenhouse gas inventory",
        "greenhouses and casabe",
        "Casablanca, a housewarming and a housekeeper",
        "price in USD and USDA",
        "Nov 2025 2,525 12 1.05",
        "$schema and $ref",
        "price in $ and USD",
    ],
)
def test_legitimate_text_has_no_findings(text):
    assert scan_text(text) == []


def test_eight_digits_with_separators_are_not_a_run():
    assert scan_text("2026-11-17") == []
    assert scan_text("1234 5678") == []
    assert scan_text("1234-567-8") == []


# --- scan_tree --------------------------------------------------------------------------------------------


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_scan_tree_covers_html_json_csv_and_md_and_returns_relative_posix_paths(tmp_path):
    _write(tmp_path, "index.html", "<p>The rectory</p>")
    _write(tmp_path, "api/v1/a.json", '{"label": "costs $5"}')
    _write(tmp_path, "api/v1/b.csv", "id,label\n1,the old annex\n")
    _write(tmp_path, "api/v1/CHANGELOG.md", "An offset was bought.")
    _write(tmp_path, "api/v1/ok.json", '{"label": "fine"}')
    assert SCANNED_SUFFIXES == (".html", ".json", ".csv", ".md")
    assert scan_tree(tmp_path, WORDS) == {
        "index.html": ["FORBIDDEN_WORD"],
        "api/v1/a.json": ["DOLLAR_AMOUNT"],
        "api/v1/b.csv": ["FORBIDDEN_WORD"],
        "api/v1/CHANGELOG.md": ["OFFSET_TERM"],
    }


def test_scan_tree_ignores_other_file_types_and_empty_trees(tmp_path):
    assert scan_tree(tmp_path) == {}
    _write(tmp_path, "assets/site.css", ".house { margin: 0 0 1000 1000 } /* commute $5 */")
    _write(tmp_path, "assets/img/a.svg", '<svg viewBox="0 0 1000 1000"><text>house</text></svg>')
    _write(tmp_path, ".htaccess", "# house commute offset $5")
    _write(tmp_path, ".carbon-build", "house")
    assert scan_tree(tmp_path) == {}


def test_scan_tree_scans_html_visible_text_not_markup_css_or_svg_coordinates(tmp_path):
    html = (
        "<!doctype html><html><head><title>Our footprint</title>"
        "<style>.rectory { margin: 0 0 1000 1000 } /* $5 */</style>"
        "<script>var rectory = 'offset'; var n = 1234567890;</script></head><body>"
        '<svg viewBox="0 0 1000 1000" width="1000" height="1000"><path d="M 10 20 L 300 400 L 5000 6000 Z"/>'
        "<text>greenhouse gas</text></svg>"
        '<a class="rectory offset" href="/data/2026-11-17/123456789012/">ZIP 62701</a></body></html>'
    )
    _write(tmp_path, "index.html", html)
    assert scan_tree(tmp_path, WORDS) == {}
    # The same markup read as raw text is exactly the false positive the visible-text scan avoids.
    assert scan_text(html) != []


def test_html_digits_in_separate_elements_are_not_one_run(tmp_path):
    cells = "".join(f"<td>{n}</td>" for n in (2025, 950, 40, 1))
    axis = "".join(f"<text>{n}</text>" for n in range(10))
    _write(tmp_path, "index.html", f"<table><tr><th>Nov</th>{cells}</tr></table><svg>{axis}</svg>")
    assert scan_tree(tmp_path) == {}


def test_html_digits_in_one_text_node_are_still_a_run(tmp_path):
    _write(tmp_path, "index.html", "<p>Reference 1234 5678 90 on file</p>")
    assert scan_tree(tmp_path) == {"index.html": ["LONG_DIGIT_RUN"]}


def test_html_offset_phrase_split_by_inline_markup_is_still_allowed(tmp_path):
    _write(tmp_path, "index.html", "<p>avoided emissions compared with a gasoline car, <em>not an</em> offset</p>")
    assert scan_tree(tmp_path) == {}


def test_html_attributes_and_comments_that_readers_can_see_are_scanned(tmp_path):
    _write(tmp_path, "a.html", '<img src="/x.png" alt="the rectory">')
    _write(tmp_path, "b.html", '<a href="/x/" aria-label="costs $5">x</a>')
    _write(tmp_path, "c.html", "<!-- rectory notes --><p>ok</p>")
    _write(tmp_path, "d.html", '<meta name="description" content="an offset">')
    _write(tmp_path, "e.html", '<p title="acct 1234 5678 90">x</p>')
    assert scan_tree(tmp_path, WORDS) == {
        "a.html": ["FORBIDDEN_WORD"],
        "b.html": ["DOLLAR_AMOUNT"],
        "c.html": ["FORBIDDEN_WORD"],
        "d.html": ["OFFSET_TERM"],
        "e.html": ["LONG_DIGIT_RUN"],
    }


def test_scan_tree_flags_a_file_that_is_not_utf8_because_it_cannot_be_read(tmp_path):
    (tmp_path / "bad.json").write_bytes(b'{"a": "\xff\xfe"}')
    assert scan_tree(tmp_path) == {"bad.json": ["NOT_UTF8"]}


def test_scan_tree_reads_the_raw_text_of_json_csv_and_md(tmp_path):
    # Not HTML, so nothing is parsed away: a long digit run anywhere in the file counts.
    _write(tmp_path, "x.json", '{"id": 1234567890}')
    _write(tmp_path, "y.csv", "a,b\n1234-5678-90,2\n")
    _write(tmp_path, "z.md", "- acct 1234 5678 90")
    assert scan_tree(tmp_path) == {
        "x.json": ["LONG_DIGIT_RUN"],
        "y.csv": ["LONG_DIGIT_RUN"],
        "z.md": ["LONG_DIGIT_RUN"],
    }
