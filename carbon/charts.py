"""Accessible stacked bar charts as inline SVG, and the WCAG contrast check for the brand colours.

`bar_chart_svg` returns one self-contained `<svg>` string for a page template to drop in. It is built to be
read without colour and without a mouse:

- The SVG has `role="img"` and is labelled by its own `<title>` and `<desc>`. The page puts a data table
  beside every chart, so the SVG does not try to carry every number in its description.
- Series differ by pattern as well as colour (solid primary colour, hatched accent colour, dotted, striped), each
  with an ink outline, so a photocopy or a colour-blind reader can still tell them apart. The two brand colours
  are passed in by the caller (they come from `parish.toml`); the rest are fixed.
- Bars flagged as estimated get a dashed outline and the others a solid one, so "estimate" is not shown by
  colour alone.
- No scripts, no `<style>`, no links and no external references: every colour and font is an attribute, so the
  chart renders the same in print and when the page's CSS fails to load. Every string from the caller is
  escaped. Output is deterministic: the element ids come from a hash of the inputs, never from a counter or a
  clock, so two builds with the same data are byte-identical and two different charts on one page do not clash.

Bars stack in the order the series are given: the first series sits on the baseline.
"""

from __future__ import annotations

import hashlib
import math
import re
from html import escape

# The fixed colours, as used by the site CSS, and the default brand pair (a parish sets its own in parish.toml).
# tests/test_charts.py checks every text and background pair.
INK = "#2B3440"
MUTED = "#56606E"
GRID = "#E3E6E9"
PALE = "#CBD5DC"
WHITE = "#FFFFFF"
DEFAULT_PRIMARY = "#1F6B4A"
DEFAULT_ACCENT = "#F2C14E"

MAX_SERIES = 4  # one style per series: solid, hatched, dotted, striped

FONT_FAMILY = "Noto Sans, sans-serif"
BAR_FILL_RATIO = 0.62  # bar width as a share of its slot
OUTLINE_WIDTH = "1.4"
DASH = "4 3"  # estimated bars
MIN_WIDTH = 760
BAR_SLOT = 48  # minimum horizontal room per bar before the chart widens
HEIGHT = 330
TOP = 14
RIGHT = 8
MAX_INTERVALS = 5  # at most this many gridline intervals on the vertical axis


# --- colour contrast -----------------------------------------------------------

_HEX_COLOUR = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _rgb(colour: str) -> tuple[int, int, int]:
    match = _HEX_COLOUR.match(colour.strip()) if isinstance(colour, str) else None
    if not match:
        raise ValueError(f"not a hex colour: {colour!r}")
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    return int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16)


def _relative_luminance(colour: str) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in _rgb(colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 2.1 contrast ratio between two hex colours ("#435EB6", "435eb6" or "#fff"), from 1.0 to 21.0.

    Order does not matter. Normal text needs at least 4.5 for level AA. Raises ValueError for anything that
    is not a 3 or 6 digit hex colour.
    """
    first, second = _relative_luminance(fg), _relative_luminance(bg)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


# --- helpers -------------------------------------------------------------------


def _esc(text: object) -> str:
    return escape(str(text), quote=True)


def _fmt_num(value: float) -> str:
    """A number without trailing zeros: 5, 0.5, 12.25."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _uid(*parts: object) -> str:
    """A short id prefix derived from the inputs, so output is reproducible and charts do not clash."""
    digest = hashlib.sha1(repr(parts).encode("utf-8")).hexdigest()
    return "c" + digest[:8]


def _axis(max_total: float) -> tuple[float, float]:
    """(step, top) for a vertical axis starting at 0 with a round step and at most MAX_INTERVALS intervals."""
    if max_total <= 0:
        return 1.0, 1.0
    exponent = math.floor(math.log10(max_total))
    for power in (exponent - 1, exponent, exponent + 1):
        for base in (1, 2, 5):
            step = base * 10.0**power
            intervals = math.ceil(round(max_total / step, 9))
            if intervals <= MAX_INTERVALS:
                return step, round(max(intervals, 1) * step, 9)
    raise AssertionError("unreachable: step 10**(exponent + 1) always fits")  # pragma: no cover


def _pattern(uid: str, index: int, primary: str, accent: str) -> tuple[str, str]:
    """(fill value, <pattern> markup or "") for the series at `index`. Series 0 is a solid colour."""
    pid = f"{uid}-p{index}"
    if index == 0:
        return primary, ""
    if index == 1:  # the accent colour with diagonal ink stripes
        body = (
            f'<pattern id="{pid}" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
            f'<rect width="6" height="6" fill="{accent}"/>'
            f'<line x1="0" y1="0" x2="0" y2="6" stroke="{INK}" stroke-width="1.6" stroke-opacity=".55"/></pattern>'
        )
    elif index == 2:  # pale grey-blue with ink dots
        body = (
            f'<pattern id="{pid}" width="7" height="7" patternUnits="userSpaceOnUse">'
            f'<rect width="7" height="7" fill="{PALE}"/>'
            f'<circle cx="3.5" cy="3.5" r="1.4" fill="{INK}"/></pattern>'
        )
    else:  # white with horizontal stripes in the primary colour
        body = (
            f'<pattern id="{pid}" width="6" height="6" patternUnits="userSpaceOnUse">'
            f'<rect width="6" height="6" fill="{WHITE}"/>'
            f'<rect y="1" width="6" height="2.4" fill="{primary}"/></pattern>'
        )
    return f"url(#{pid})", body


def _text(x: float, y: float, content: str, *, anchor: str, cls: str, fill: str = MUTED, extra: str = "") -> str:
    return (
        f'<text x="{x:.2f}" y="{y:.2f}" text-anchor="{anchor}" class="{cls}" fill="{fill}" '
        f'font-family="{FONT_FAMILY}" font-size="13" font-weight="600"{extra}>{content}</text>'
    )


def _empty_svg(uid: str, title: str, desc: str, no_data_text: str) -> str:
    return (
        f'<svg viewBox="0 0 {MIN_WIDTH} 120" role="img" aria-labelledby="{uid}-t {uid}-d" class="chart chart-empty">'
        f'<title id="{uid}-t">{_esc(title)}</title><desc id="{uid}-d">{_esc(desc)}</desc>'
        f'<rect x="1" y="1" width="{MIN_WIDTH - 2}" height="118" rx="10" fill="none" stroke="{INK}" '
        f'stroke-width="{OUTLINE_WIDTH}" stroke-dasharray="{DASH}"/>'
        + _text(MIN_WIDTH / 2, 66, _esc(no_data_text), anchor="middle", cls="axis nodata")
        + "</svg>"
    )


# --- the chart -----------------------------------------------------------------


def bar_chart_svg(
    title: str,
    desc: str,
    labels: list[str],
    series: dict[str, list[float]],
    unit: str,
    *,
    estimated: list[bool] | None = None,
    no_data_text: str = "No data",
    primary: str = DEFAULT_PRIMARY,
    accent: str = DEFAULT_ACCENT,
) -> str:
    """Stacked bar chart as one inline `<svg>` string.

    `labels` name the bars, left to right; a label may hold one newline to make a second, darker line (for
    example "Jan" over "2026"). `series` maps a series name to one non-negative value per label; the first
    series is at the bottom of each stack. `unit` titles the vertical axis. `estimated`, when given, has one
    flag per label: True draws that bar's outline dashed, False solid (None draws every outline solid).
    `no_data_text` is the visible message when there is nothing to chart, for the page to pass in its own
    language. `primary` and `accent` are the two brand colours (hex), used for the first and second series.

    With no series, or only empty ones, a short "no data" SVG is returned instead of raising. Mismatched
    lengths, negative or non-finite values, and more than MAX_SERIES series raise ValueError.
    """
    _rgb(primary), _rgb(accent)  # a colour that is not hex raises here, before it reaches the markup
    uid = _uid(title, desc, tuple(labels), tuple((k, tuple(v)) for k, v in series.items()), unit, estimated)
    if not series or all(len(values) == 0 for values in series.values()):
        return _empty_svg(uid, title, desc, no_data_text)

    names = list(series)
    if len(names) > MAX_SERIES:
        raise ValueError(f"at most {MAX_SERIES} series are supported, got {len(names)}")
    n = len(labels)
    for name, values in series.items():
        if len(values) != n:
            raise ValueError(f"series {name!r} has {len(values)} values for {n} labels")
        for value in values:
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"series {name!r} has a value that is not a finite, non-negative number: {value!r}")
    if estimated is not None and len(estimated) != n:
        raise ValueError(f"estimated has {len(estimated)} flags for {n} labels")

    totals = [sum(series[name][i] for name in names) for i in range(n)]
    step, top = _axis(max(totals))
    ticks = [round(i * step, 9) for i in range(int(round(top / step)) + 1)]
    tick_text = [_fmt_num(v) for v in ticks]

    label_lines = [label.split("\n", 1) for label in labels]
    max_lines = max(len(lines) for lines in label_lines)
    left = max(46.0, 22.0 + 8.5 * max(len(t) for t in tick_text))
    bottom = 18.0 + 16.0 * max_lines
    width = max(MIN_WIDTH, left + RIGHT + BAR_SLOT * n)
    plot_h = HEIGHT - TOP - bottom
    slot = (width - left - RIGHT) / n
    bar_w = slot * BAR_FILL_RATIO

    def y_of(value: float) -> float:
        return TOP + plot_h * (1 - value / top)

    fills = []
    patterns = []
    for i in range(len(names)):
        fill, pattern = _pattern(uid, i, primary, accent)
        fills.append(fill)
        if pattern:
            patterns.append(pattern)

    out = [
        f'<svg viewBox="0 0 {_fmt_num(width)} {HEIGHT}" role="img" aria-labelledby="{uid}-t {uid}-d" class="chart">',
        f'<title id="{uid}-t">{_esc(title)}</title><desc id="{uid}-d">{_esc(desc)}</desc>',
    ]
    if patterns:
        out.append("<defs>" + "".join(patterns) + "</defs>")

    for value, text in zip(ticks, tick_text):
        y = y_of(value)
        out.append(
            f'<line x1="{left:.2f}" x2="{width - RIGHT:.2f}" y1="{y:.2f}" y2="{y:.2f}" class="grid" '
            f'stroke="{GRID}" stroke-width="1"/>'
        )
        out.append(_text(left - 8, y + 4.5, text, anchor="end", cls="axis ytick"))
    out.append(
        _text(
            12,
            TOP + 6,
            _esc(unit),
            anchor="end",
            cls="axis unit",
            extra=f' transform="rotate(-90 12 {TOP + 6})"',
        )
    )

    for i, lines in enumerate(label_lines):
        x = left + slot * i + (slot - bar_w) / 2
        is_estimated = bool(estimated[i]) if estimated is not None else False
        dash = f' stroke-dasharray="{DASH}"' if is_estimated else ""
        spoken = " ".join(lines)
        detail = ", ".join(f"{name} {_fmt_num(round(series[name][i], 3))}" for name in names)
        out.append(
            f'<g class="bar"><title>{_esc(spoken)}: {_fmt_num(round(totals[i], 3))} {_esc(unit)} ({_esc(detail)})</title>'
        )
        cumulative = 0.0
        for name, fill in zip(names, fills):
            value = series[name][i]
            if value <= 0:
                continue
            y_bottom = round(y_of(cumulative), 2)
            cumulative += value
            y_top = round(y_of(cumulative), 2)
            out.append(
                f'<rect x="{x:.2f}" y="{y_top:.2f}" width="{bar_w:.2f}" height="{y_bottom - y_top:.2f}" '
                f'fill="{fill}" stroke="{INK}" stroke-width="{OUTLINE_WIDTH}"{dash} data-series="{_esc(name)}"/>'
            )
        out.append("</g>")

        cx = x + bar_w / 2
        base_y = HEIGHT - bottom + 16
        if len(lines) == 1:
            out.append(_text(cx, base_y, _esc(lines[0]), anchor="middle", cls="axis xlabel"))
        else:
            first = f'<tspan x="{cx:.2f}">{_esc(lines[0])}</tspan>'
            second = f'<tspan x="{cx:.2f}" dy="15" fill="{INK}">{_esc(lines[1])}</tspan>'
            out.append(_text(cx, base_y, first + second, anchor="middle", cls="axis xlabel"))

    out.append("</svg>")
    return "".join(out)
