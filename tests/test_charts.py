"""Tests for the stacked bar chart (carbon.charts.bar_chart_svg) and the colour contrast check.

Charts are inline SVG. They are parsed with ElementTree, which also proves they are well-formed XML (so every
label and title was escaped).
"""

import re
import xml.etree.ElementTree as ET

import pytest

from carbon import charts
from carbon.charts import MAX_SERIES, bar_chart_svg, contrast_ratio

LABELS = ["Jan", "Feb", "Mar"]
SERIES = {"Electricity": [1.0, 2.0, 3.0], "Natural gas": [4.0, 3.0, 2.0]}


def parse(svg):
    return ET.fromstring(svg)


def chart(**kw):
    args = dict(title="Monthly emissions", desc="Stacked bars by month.", labels=LABELS, series=SERIES, unit="t CO2e")
    args.update(kw)
    return bar_chart_svg(
        args.pop("title"),
        args.pop("desc"),
        args.pop("labels"),
        args.pop("series"),
        args.pop("unit"),
        **args,
    )


def bars(root):
    return root.findall(".//g[@class='bar']")


def num(el, attr):
    return float(el.get(attr))


# --- structure and accessibility ----------------------------------------------


def test_bar_chart_svg_has_role_title_desc_and_no_script():
    svg = chart()
    root = parse(svg)
    assert root.tag == "svg"
    assert root.get("role") == "img"
    title = root.find("title")
    desc = root.find("desc")
    assert title is not None and title.text == "Monthly emissions"
    assert desc is not None and desc.text == "Stacked bars by month."
    assert root.get("aria-labelledby") == f"{title.get('id')} {desc.get('id')}"
    lowered = svg.lower()
    assert "<script" not in lowered
    assert "javascript:" not in lowered
    assert not re.search(r"\son\w+\s*=", lowered), "no event handler attributes"
    for attr in ("href=", "src=", "http:", "https:"):
        assert attr not in lowered, f"chart must be self-contained, found {attr}"
    assert "<style" not in lowered


def test_bar_chart_svg_escapes_labels():
    svg = bar_chart_svg(
        'Emissions <script>alert("x")</script>',
        'Tom & Jerry "quote" <b>bold</b>',
        ['<img src=x onerror=alert(1)>', "A&B", "Mar\n2026"],
        {"Gas <i>&</i>": [1.0, 2.0, 3.0]},
        "t <CO2e>",
    )
    assert "<script" not in svg.lower()
    assert "<img" not in svg
    assert "<b>" not in svg and "<i>" not in svg
    root = parse(svg)  # raises if anything was left unescaped
    assert root.find("title").text == 'Emissions <script>alert("x")</script>'
    assert root.find("desc").text == 'Tom & Jerry "quote" <b>bold</b>'
    texts = [(el.text or "") + "".join(c.text or "" for c in el) for el in root.iter("text")]
    assert any("<img src=x onerror=alert(1)>" in x for x in texts)
    assert any("A&B" in x for x in texts)
    assert any("t <CO2e>" in x for x in texts)


@pytest.mark.parametrize(
    "labels, series",
    [([], {}), ([], {"A": []}), (["Jan"], {}), (["Jan", "Feb"], {"A": [], "B": []})],
)
def test_bar_chart_svg_handles_empty_series(labels, series):
    svg = bar_chart_svg("Monthly emissions", "No months yet.", labels, series, "t CO2e")
    root = parse(svg)
    assert root.tag == "svg" and root.get("role") == "img"
    assert root.find("title").text == "Monthly emissions"
    assert root.find("desc").text == "No months yet."
    assert "no data" in svg.lower()
    assert len(svg) < 1500
    assert bars(root) == []
    assert "<script" not in svg.lower()


def test_empty_chart_escapes_title_and_description():
    svg = bar_chart_svg('T <script>alert(1)</script> & "q"', "D <b>x</b> & y", [], {}, "t")
    assert "<script" not in svg.lower() and "<b>" not in svg
    root = parse(svg)
    assert root.find("title").text == 'T <script>alert(1)</script> & "q"'
    assert root.find("desc").text == "D <b>x</b> & y"


def test_empty_chart_uses_the_caller_s_text_escaped():
    svg = bar_chart_svg("T", "D", [], {}, "t", no_data_text="Todavía no hay <datos> & más")
    root = parse(svg)
    texts = [el.text for el in root.iter("text")]
    assert "Todavía no hay <datos> & más" in texts
    assert "no data" not in svg.lower()


# --- the bars ----------------------------------------------------------------


def test_bar_chart_svg_draws_one_bar_group_per_label_with_a_rect_per_series():
    root = parse(chart())
    groups = bars(root)
    assert len(groups) == 3
    for group in groups:
        assert len(group.findall("rect")) == 2
        assert group.find("title") is not None


def test_bar_chart_svg_stacks_series_in_the_order_given():
    root = parse(chart(labels=["Jan"], series={"Electricity": [2.0], "Natural gas": [3.0]}))
    first, second = bars(root)[0].findall("rect")
    assert first.get("data-series") == "Electricity"
    assert second.get("data-series") == "Natural gas"
    # the first series sits on the baseline, the second directly above it
    baseline = max(num(first, "y") + num(first, "height"), num(second, "y") + num(second, "height"))
    assert num(first, "y") + num(first, "height") == pytest.approx(baseline, abs=0.01)
    assert num(second, "y") + num(second, "height") == pytest.approx(num(first, "y"), abs=0.01)
    # heights are in proportion to the values
    assert num(second, "height") / num(first, "height") == pytest.approx(3.0 / 2.0, rel=0.02)

    reversed_root = parse(chart(labels=["Jan"], series={"Natural gas": [3.0], "Electricity": [2.0]}))
    bottom, top = bars(reversed_root)[0].findall("rect")
    assert bottom.get("data-series") == "Natural gas" and top.get("data-series") == "Electricity"


def test_bar_chart_svg_estimated_bars_are_dashed_and_others_solid():
    root = parse(chart(estimated=[True, False, True]))
    flags = [True, False, True]
    for group, is_estimated in zip(bars(root), flags):
        for rect in group.findall("rect"):
            assert rect.get("stroke"), "every bar has an outline"
            assert rect.get("stroke-width")
            assert (rect.get("stroke-dasharray") is not None) is is_estimated


def test_bar_chart_svg_without_estimated_draws_every_outline_solid():
    root = parse(chart())
    for rect in root.iter("rect"):
        assert rect.get("stroke-dasharray") is None
    for group in bars(root):
        for rect in group.findall("rect"):
            assert rect.get("stroke")


def test_bar_chart_svg_estimated_must_match_the_number_of_labels():
    with pytest.raises(ValueError):
        chart(estimated=[True, False])


def test_bar_chart_svg_series_differ_by_pattern_as_well_as_colour():
    four = {f"S{i}": [1.0, 2.0, 3.0] for i in range(MAX_SERIES)}
    root = parse(chart(series=four))
    fills = []
    for rect in bars(root)[0].findall("rect"):
        fills.append(rect.get("fill"))
    assert len(set(fills)) == MAX_SERIES, "each series has its own fill"
    pattern_ids = {p.get("id") for p in root.iter("pattern")}
    patterned = [f for f in fills if f.startswith("url(#")]
    assert len(patterned) >= MAX_SERIES - 1, "all but at most one series use a pattern, so they differ without colour"
    for fill in patterned:
        assert fill[len("url(#") : -1] in pattern_ids, "every pattern fill refers to a defined pattern"


def test_bar_chart_svg_skips_zero_height_segments():
    root = parse(chart(labels=["Jan"], series={"A": [0.0], "B": [2.0]}))
    rects = bars(root)[0].findall("rect")
    assert [r.get("data-series") for r in rects] == ["B"]


def test_bar_chart_svg_all_zero_values_still_draws_an_axis():
    root = parse(chart(series={"A": [0.0, 0.0, 0.0]}))
    assert len(bars(root)) == 3
    assert [el.text for el in root.iter("text") if el.text == "0"], "axis starts at 0"


def test_bar_chart_svg_axis_reaches_the_tallest_stack():
    root = parse(chart(series={"A": [3.0, 9.0, 14.2], "B": [1.0, 1.0, 1.0]}))
    ticks = [float(el.text) for el in root.iter("text") if el.get("class") == "axis ytick"]
    assert ticks[0] == 0
    assert max(ticks) >= 15.2
    assert ticks == sorted(ticks)
    top = min(num(r, "y") for r in root.iter("rect") if r.get("data-series"))
    grid_ys = [num(line, "y1") for line in root.iter("line") if line.get("class") == "grid"]
    assert top >= min(grid_ys) - 0.01, "no bar pokes above the top gridline"


def test_bar_chart_svg_labels_the_axis_with_the_unit_and_the_months():
    root = parse(chart(unit="t CO₂e"))
    texts = ["".join(el.itertext()) for el in root.iter("text")]
    assert "t CO₂e" in texts
    for label in LABELS:
        assert label in texts


def test_bar_chart_svg_two_line_labels_become_two_lines():
    root = parse(chart(labels=["Dec", "Jan\n2026", "Feb"]))
    two_line = [el for el in root.iter("text") if len(el.findall("tspan")) == 2]
    assert len(two_line) == 1
    assert [tspan.text for tspan in two_line[0].findall("tspan")] == ["Jan", "2026"]


def test_bar_chart_svg_bar_titles_give_the_values_for_mouse_users():
    root = parse(chart())
    title = bars(root)[0].find("title").text
    assert "Jan" in title and "Electricity" in title and "Natural gas" in title and "5" in title


def test_bar_chart_svg_is_deterministic_and_ids_are_unique_across_charts():
    assert chart() == chart()
    one = parse(chart(title="One"))
    two = parse(chart(title="Two", desc="Another chart on the same page."))
    ids_one = {el.get("id") for el in one.iter() if el.get("id")}
    ids_two = {el.get("id") for el in two.iter() if el.get("id")}
    assert ids_one and ids_two and not (ids_one & ids_two)


def test_bar_chart_svg_grows_wider_for_many_months():
    wide = parse(chart(labels=[f"m{i}" for i in range(24)], series={"A": [1.0] * 24}))
    narrow = parse(chart())
    assert float(wide.get("viewBox").split()[2]) > float(narrow.get("viewBox").split()[2])


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(series={"A": [1.0, 2.0]}),  # fewer values than labels
        dict(series={"A": [1.0, 2.0, 3.0, 4.0]}),  # more values than labels
        dict(series={"A": [1.0, -2.0, 3.0]}),  # negative
        dict(series={"A": [1.0, float("nan"), 3.0]}),
        dict(series={"A": [1.0, float("inf"), 3.0]}),
        dict(series={f"S{i}": [1.0, 1.0, 1.0] for i in range(MAX_SERIES + 1)}),
    ],
)
def test_bar_chart_svg_rejects_bad_input(kwargs):
    with pytest.raises(ValueError):
        chart(**kwargs)


# --- contrast ----------------------------------------------------------------

WHITE = "#ffffff"
INK = "#2B3440"
BLUE = "#1F6B4A"  # the template's default primary colour (the name is kept from the first design)
YELLOW = "#F2C14E"  # the template's default accent colour
MUTED = "#56606E"
SOFT = "#EEF1F2"
PAGE = "#F5F6F7"


def test_contrast_ratio_known_values():
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast_ratio("#ffffff", "#ffffff") == pytest.approx(1.0)
    assert contrast_ratio("#ffffff", "#000000") == pytest.approx(21.0), "order does not matter"
    assert contrast_ratio("fff", "000") == pytest.approx(21.0), "short form without a hash"
    assert contrast_ratio("#777777", "#ffffff") == pytest.approx(4.478, abs=0.01)
    assert contrast_ratio("#435EB6", "#FFFFFF") == contrast_ratio("435eb6", "ffffff")


@pytest.mark.parametrize("bad", ["", "#12", "#12345", "#gggggg", "blue", "#1234567"])
def test_contrast_ratio_rejects_bad_colours(bad):
    with pytest.raises(ValueError):
        contrast_ratio(bad, "#ffffff")
    with pytest.raises(ValueError):
        contrast_ratio("#ffffff", bad)


def test_brand_text_pairs_meet_aa():
    assert contrast_ratio(INK, WHITE) >= 4.5
    assert contrast_ratio(BLUE, WHITE) >= 4.5


@pytest.mark.parametrize(
    "fg, bg",
    [
        (INK, WHITE),
        (INK, SOFT),
        (BLUE, WHITE),
        (MUTED, WHITE),
        (MUTED, SOFT),
        (MUTED, PAGE),
        (WHITE, BLUE),
        (INK, YELLOW),
    ],
)
def test_every_text_and_background_pair_in_the_design_meets_aa(fg, bg):
    # WCAG 2.1 AA, normal text: 4.5. These are all the text/background pairs the reviewed Home page draft uses.
    assert contrast_ratio(fg, bg) >= 4.5, f"{fg} on {bg} is {contrast_ratio(fg, bg):.2f}"


def test_chart_colour_constants_are_the_tested_brand_colours():
    assert charts.INK.lower() == INK.lower()
    assert charts.DEFAULT_PRIMARY.lower() == BLUE.lower()
    assert charts.DEFAULT_ACCENT.lower() == YELLOW.lower()
    assert charts.MUTED.lower() == MUTED.lower()


def test_chart_text_uses_only_tested_colours_on_a_white_card():
    tested = {INK.lower(), MUTED.lower()}
    root = parse(chart(labels=["Dec", "Jan\n2026", "Feb"]))
    fills = {(el.get("fill") or "").lower() for el in root.iter("text")} | {
        (el.get("fill") or "").lower() for el in root.iter("tspan") if el.get("fill")
    }
    assert fills and fills <= tested, fills


# --- the two brand colours are the caller's (they come from parish.toml) -------------------------------------


def test_the_brand_colours_passed_in_are_the_ones_drawn_and_the_defaults_are_not():
    svg = chart(primary="#7A1F3D", accent="#FFE9A8")
    assert 'fill="#7A1F3D"' in svg and 'fill="#FFE9A8"' in svg
    assert charts.DEFAULT_PRIMARY not in svg and charts.DEFAULT_ACCENT not in svg
    # Outlines, axis text and gridlines keep the fixed colours whatever the brand is.
    assert f'stroke="{charts.INK}"' in svg and f'fill="{charts.MUTED}"' in svg


def test_without_brand_colours_the_chart_uses_the_template_defaults():
    svg = chart()
    assert f'fill="{charts.DEFAULT_PRIMARY}"' in svg and f'fill="{charts.DEFAULT_ACCENT}"' in svg


@pytest.mark.parametrize("bad", ["green", "#12", 'red" onload="x', ""])
def test_a_brand_colour_that_is_not_hex_is_refused_before_it_reaches_the_markup(bad):
    with pytest.raises(ValueError):
        chart(primary=bad)
    with pytest.raises(ValueError):
        chart(accent=bad)
