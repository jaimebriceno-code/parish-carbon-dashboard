"""The content guard: the forbidden-content scan the build runs on everything it is about to publish.

Tests can only check the data that exists today. A mistyped milestone label or a pasted bill figure next month is
caught here, inside the build, before a single file reaches the output folder.

What it looks for (each finding is a code, never the text that matched, so a pasted account number cannot land
in a build log):

- FORBIDDEN_WORD  one of the parish's own words from `parish.toml`, `[privacy] forbidden_words`, as a whole word or
                  phrase, in any mix of capitals ("rectory" does not match "directory", and each form, such as a
                  plural, is listed separately). The list is empty unless the parish fills it: it is for anything
                  that must never be named on the public site, such as a building outside the boundary.
- DOLLAR_AMOUNT   a dollar sign, or "USD", followed by a digit, with optional whitespace between. Dollars are never
                  published.
- LONG_DIGIT_RUN  nine or more digits, optionally separated by single spaces or hyphens: an account or meter
                  number, however it is spaced. ISO dates (8 digits), a five-digit ZIP code and figures with
                  thousands separators or decimals are shorter or broken by other characters, so they never match.
- OFFSET_TERM     "offset", "compensación", "compensar" or "compensa" anywhere except inside the two allowed
                  phrases in `carbon.i18n.ALLOWED_OFFSET_PHRASES`.
- NOT_UTF8        a file the guard cannot read as UTF-8, so it cannot vouch for it.

What it reads: `scan_tree` scans every `.html`, `.json`, `.csv` and `.md` file under a folder. HTML is judged by
what a reader gets: the visible text (BeautifulSoup `get_text(" ")`, so SVG coordinates, CSS and scripts cannot
cause false positives), plus attribute text that assistive technology and search engines read (`alt`, `title`,
`aria-label`, `aria-description` and a `<meta>`'s `content`) and comments. The other files are scanned as raw text.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from pathlib import Path

from bs4 import BeautifulSoup, Comment

from carbon.i18n import offset_terms_outside_allowed

FORBIDDEN_WORD = "FORBIDDEN_WORD"
DOLLAR_AMOUNT = "DOLLAR_AMOUNT"
LONG_DIGIT_RUN = "LONG_DIGIT_RUN"
OFFSET_TERM = "OFFSET_TERM"
NOT_UTF8 = "NOT_UTF8"

SCANNED_SUFFIXES: tuple[str, ...] = (".html", ".json", ".csv", ".md")
# Attributes whose value is read as text by people, screen readers or search engines.
HTML_TEXT_ATTRIBUTES: tuple[str, ...] = ("alt", "title", "aria-label", "aria-description", "content")

_DOLLAR_RE = re.compile(r"\$\s*\d")
_USD_RE = re.compile(r"usd\s*\d", re.IGNORECASE)
_DIGIT_RUN_RE = re.compile(r"\d(?:[ -]?\d){8,}")
_WHITESPACE_RE = re.compile(r"\s+")


def forbidden_pattern(words: Iterable[str]) -> re.Pattern[str] | None:
    """One case-insensitive pattern matching any of `words` as a whole word or phrase; None for an empty list.

    "Whole" means not touching a letter, digit or underscore on either side, and the spaces inside a phrase
    match any run of whitespace, so a phrase wrapped over two lines is still found.
    """
    parts = []
    for word in words:
        pieces = [re.escape(piece) for piece in unicodedata.normalize("NFC", word).split()]
        if pieces:
            parts.append(r"\s+".join(pieces))
    if not parts:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)", re.IGNORECASE)


def _word_findings(text: str, forbidden: re.Pattern[str] | None = None) -> set[str]:
    """Every rule except the digit run, which has to be applied to one run of text at a time."""
    found: set[str] = set()
    if forbidden is not None and forbidden.search(unicodedata.normalize("NFC", text)):
        found.add(FORBIDDEN_WORD)
    if _DOLLAR_RE.search(text) or _USD_RE.search(text):
        found.add(DOLLAR_AMOUNT)
    # Collapse whitespace first, so an allowed phrase wrapped over two lines or set with a no-break space
    # is still recognised.
    if offset_terms_outside_allowed(_WHITESPACE_RE.sub(" ", text)):
        found.add(OFFSET_TERM)
    return found


def _digit_findings(text: str) -> set[str]:
    return {LONG_DIGIT_RUN} if _DIGIT_RUN_RE.search(text) else set()


def scan_text(text: str, forbidden_words: Iterable[str] = ()) -> list[str]:
    """The sorted, de-duplicated finding codes for one piece of text; [] means clean. Never returns the text."""
    return sorted(_word_findings(text, forbidden_pattern(forbidden_words)) | _digit_findings(text))


def _html_findings(html: str, forbidden: re.Pattern[str] | None = None) -> set[str]:
    """Findings for a page, judged on what a reader gets rather than on the markup."""
    soup = BeautifulSoup(html, "html.parser")
    # (`string=Comment` would match every string, hence the isinstance test.)
    pieces = [str(piece) for piece in soup.find_all(string=lambda text: isinstance(text, Comment))]
    for tag in soup.find_all(True):
        for name in HTML_TEXT_ATTRIBUTES:
            value = tag.get(name)
            if isinstance(value, str):
                pieces.append(value)

    # Words are judged on the visible text joined with spaces, so a phrase split by inline markup still reads
    # as one. Digits are judged one text node at a time: the cells of a table row or the ticks of a chart axis
    # sit side by side in the joined text ("2025 950 40") but are never one number.
    found = _word_findings(soup.get_text(" ") + " " + " ".join(pieces), forbidden)
    for piece in [*soup.stripped_strings, *pieces]:
        found |= _digit_findings(piece)
    return found


def scan_tree(root: Path, forbidden_words: Iterable[str] = ()) -> dict[str, list[str]]:
    """Scan every .html, .json, .csv and .md file under `root`, refusing `forbidden_words` as well as the fixed rules.

    Returns {relative posix path: sorted finding codes} for the files with findings; {} means the tree is clean.
    """
    root = Path(root)
    forbidden = forbidden_pattern(forbidden_words)
    report: dict[str, list[str]] = {}
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SCANNED_SUFFIXES)
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            report[path.relative_to(root).as_posix()] = [NOT_UTF8]
            continue
        if path.suffix.lower() == ".html":
            found = _html_findings(text, forbidden)
        else:
            found = _word_findings(text, forbidden) | _digit_findings(text)
        if found:
            report[path.relative_to(root).as_posix()] = sorted(found)
    return report
