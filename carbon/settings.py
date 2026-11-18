"""The parish settings file, `parish.toml`: the one file a parish edits besides the data CSVs.

`load_settings(path)` reads it with `tomllib`, checks every key and returns a frozen `Settings`. Anything wrong
raises `SettingsError`, whose `problems` are plain-English sentences that each name the section and the key, so
a volunteer who has never seen a traceback can fix the file from the message alone. Every problem is reported
at once, not just the first.

What lives here and nowhere else
--------------------------------
- Who the parish is (name, place, contact) and how its campus is described in a sentence.
- Where the site is served (`[site] base_url`, an origin with an optional path such as
  https://someone.github.io/repo-name) and which languages it is built in.
- The two brand colors. They are checked here against the fixed backgrounds and text colors of
  `static/site.css` (the `NEUTRALS` below; a test keeps the two in step), so a color that would make text
  hard to read stops the build with a sentence, not a failed accessibility audit later.
- The eGRID subregion (which `electricity_<subregion>` row of `data/factors.csv` prices the electricity), the
  calendar year the baseline moves to once its twelve bills are in, whether the travel line is shown and what it
  is called, extra words the content guard must refuse, and the sample-data flag.
"""

from __future__ import annotations

import difflib
import re
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from carbon.calc import CALENDAR_BASELINE_YEAR, grid_factor_id
from carbon.charts import DEFAULT_ACCENT, DEFAULT_PRIMARY, INK, WHITE, contrast_ratio

SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "es")

DEFAULT_CAMPUS = {"en": "our church campus", "es": "el campus de nuestra iglesia"}
DEFAULT_TRAVEL_LABEL = {
    "en": "Clergy and staff church travel",
    "es": "Viajes del clero y del personal por la iglesia",
}
DEFAULT_BASELINE_YEAR = CALENDAR_BASELINE_YEAR
SAMPLE_NAME = "St. Example Church"

# The fixed colors of static/site.css that the two brand colors sit on or next to. tests/test_site.py checks
# these against the stylesheet, so the contrast check below always judges the colors the pages really use.
NEUTRALS: dict[str, str] = {
    "card": WHITE,
    "bg": "#F5F6F7",
    "tint": "#FBFCFC",
    "soft": "#EEF1F2",
}
TEXT_CONTRAST = 4.5  # WCAG 2.1 AA for normal text
GRAPHIC_CONTRAST = 3.0  # WCAG 2.1 AA for the parts of a graphic that must be told apart

# Section -> the keys it may hold. Anything else is reported as a typo, with the closest known name.
KNOWN_KEYS: dict[str, tuple[str, ...]] = {
    "": ("sample_data",),
    "organization": ("name", "slug", "city", "state", "zip", "contact_email", "website", "campus_en", "campus_es"),
    "site": ("base_url", "languages"),
    "brand": ("primary_color", "accent_color", "logo"),
    "grid": ("egrid_subregion",),
    "baseline": ("calendar_year",),
    "travel": ("enabled", "label_en", "label_es"),
    "privacy": ("forbidden_words",),
}
LOGO_SUFFIXES: tuple[str, ...] = (".svg", ".png", ".jpg", ".jpeg", ".webp")

_HOST = r"(?:[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*|\[[0-9A-Fa-f:]+\])"
_BASE_URL_RE = re.compile(rf"(https?://{_HOST}(?::\d{{1,5}})?)((?:/[A-Za-z0-9._~-]+)*)/?")
_HEX_RE = re.compile(r"#[0-9A-Fa-f]{6}")
_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_EMAIL_RE = re.compile(r"[^@\s<>\"]+@[^@\s<>\"]+\.[^@\s<>\"]+")
_SUBREGION_RE = re.compile(r"[A-Za-z0-9]{2,8}")
_WORD_RE = re.compile(r"[^\W_]+(?:[ '’-][^\W_]+)*")
_ZIP_RE = re.compile(r"\d{5}")
_STATE_RE = re.compile(r"[A-Za-z]{2}")
_WEBSITE_RE = re.compile(r"https?://\S+")


class SettingsError(ValueError):
    """`parish.toml` cannot be used. `problems` holds one plain sentence per problem."""

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


@dataclass(frozen=True)
class Settings:
    """Everything `parish.toml` says, already checked. Built by `load_settings`; tests may build one directly."""

    name: str
    city: str
    state: str  # two capital letters
    zip: str  # five digits, as text
    contact_email: str
    egrid_subregion: str  # capitals, e.g. "RFCW"
    slug: str = ""  # filled from name, city and state when left blank
    website: str = ""
    campus_en: str = DEFAULT_CAMPUS["en"]
    campus_es: str = DEFAULT_CAMPUS["es"]
    base_url: str = ""  # "" for a local build; else origin plus optional path, no trailing slash
    languages: tuple[str, ...] = ("en", "es")
    primary_color: str = DEFAULT_PRIMARY
    accent_color: str = DEFAULT_ACCENT
    logo: Path | None = None  # an image file to show in the header in place of the built-in mark
    baseline_year: int = DEFAULT_BASELINE_YEAR
    travel_enabled: bool = True
    travel_label_en: str = DEFAULT_TRAVEL_LABEL["en"]
    travel_label_es: str = DEFAULT_TRAVEL_LABEL["es"]
    forbidden_words: tuple[str, ...] = ()
    sample_data: bool = True

    def __post_init__(self) -> None:
        if not self.slug:
            object.__setattr__(self, "slug", derive_slug(self.name, self.city, self.state))

    # --- where the site lives ---------------------------------------------------------------------------

    @property
    def origin(self) -> str:
        """ "https://host" (no path), or "" when no base URL is set."""
        return split_base_url(self.base_url)[0]

    @property
    def path_prefix(self) -> str:
        """The path the site is served under, such as "/repo-name"; "" at a domain root or with no base URL."""
        return split_base_url(self.base_url)[1]

    # --- what the calculation needs ---------------------------------------------------------------------

    @property
    def electricity_factor_id(self) -> str:
        return grid_factor_id(self.egrid_subregion)

    # --- wording ----------------------------------------------------------------------------------------

    def campus(self, lang: str) -> str:
        return self.campus_es if lang == "es" else self.campus_en

    def travel_label(self, lang: str) -> str:
        return self.travel_label_es if lang == "es" else self.travel_label_en

    def words(self, lang: str) -> dict[str, str]:
        """The values the string table's organization placeholders are filled with, for one language."""
        campus = self.campus(lang)
        return {
            "name": self.name,
            "city": self.city,
            "state": self.state,
            "zip": self.zip,
            "campus": campus,
            "Campus": campus[:1].upper() + campus[1:],
            "travel_label": self.travel_label(lang),
            "subregion": self.egrid_subregion,
            "year": str(self.baseline_year),
        }


# --- small pure helpers ---------------------------------------------------------------------------------


def derive_slug(name: str, city: str, state: str) -> str:
    """ "St. Example Church", "Springfield", "IL" -> "st-example-church-springfield-il" (ASCII, lowercase)."""
    parts = []
    for text in (name, city, state):
        plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
        part = re.sub(r"[^a-z0-9]+", "-", plain).strip("-")
        if part:
            parts.append(part)
    return "-".join(parts)


def split_base_url(base_url: str | None) -> tuple[str, str]:
    """(origin, path prefix) of a base URL: ("https://someone.github.io", "/repo-name").

    "" or None gives ("", ""): a local build with root-relative links. The prefix never ends in a slash and is
    "" at a domain root. Raises ValueError for anything that is not http(s)://host[:port][/path].
    """
    if base_url is None or base_url == "":
        return "", ""
    match = _BASE_URL_RE.fullmatch(base_url) if isinstance(base_url, str) else None
    if match is None:
        raise ValueError(
            "the base URL must look like https://carbon.example.org or https://someone.github.io/repo-name "
            f"(no spaces, no ? or #), but it is {base_url!r}"
        )
    return match.group(1), match.group(2)


def normalize_base_url(base_url: str | None) -> str:
    """The base URL without a trailing slash; "" stays "". Raises ValueError as `split_base_url` does."""
    origin, prefix = split_base_url(base_url)
    return origin + prefix


def brand_contrast_problems(primary: str, accent: str) -> list[str]:
    """Why these two brand colors would fail WCAG AA on the site, as plain sentences; [] when they are fine."""
    problems = []
    for key, color in NEUTRALS.items():
        ratio = contrast_ratio(primary, color)
        if ratio < TEXT_CONTRAST:
            place = "white" if key == "card" else f"the page's light background {color}"
            problems.append(
                f"[brand] primary_color {primary} is too light to read as link and button text on {place}: "
                f"the contrast is {ratio:.1f} to 1 and it must be at least {TEXT_CONTRAST} to 1. Choose a darker color."
            )
            break  # one sentence about the primary color is enough
    ratio = contrast_ratio(INK, accent)
    if ratio < TEXT_CONTRAST:
        problems.append(
            f"[brand] accent_color {accent} is too dark: the site puts dark text ({INK}) on it, and the contrast is "
            f"{ratio:.1f} to 1 where at least {TEXT_CONTRAST} to 1 is needed. Choose a lighter color."
        )
    ratio = contrast_ratio(primary, accent)
    if ratio < GRAPHIC_CONTRAST:
        problems.append(
            f"[brand] primary_color {primary} and accent_color {accent} are too alike: they sit side by side in the "
            f"charts and the contrast between them is {ratio:.1f} to 1 where at least {GRAPHIC_CONTRAST:.0f} to 1 is "
            "needed. Make the primary color darker or the accent color lighter."
        )
    return problems


# --- reading the file -----------------------------------------------------------------------------------


class _Reader:
    """Pulls typed values out of the parsed TOML and collects a sentence for each thing that is wrong."""

    def __init__(self, data: dict):
        self.data = data
        self.problems: list[str] = []

    @staticmethod
    def _label(section: str, key: str) -> str:
        return f"[{section}] {key}" if section else key

    def _raw(self, section: str, key: str):
        table = self.data.get(section, {}) if section else self.data
        return table.get(key) if isinstance(table, dict) else None

    def text(self, section: str, key: str, *, required: bool = False, default: str = "") -> str:
        value = self._raw(section, key)
        label = self._label(section, key)
        if value is None or value == "":
            if required:
                self.problems.append(f"{label} is missing or empty. Type it between the quote marks.")
            return default
        if not isinstance(value, str):
            self.problems.append(f"{label} must be text between quote marks, but it is {value!r}.")
            return default
        value = value.strip()
        if "\n" in value or "\r" in value:
            self.problems.append(f"{label} must be on one line.")
            return default
        return value if value else default

    def boolean(self, section: str, key: str, default: bool) -> bool:
        value = self._raw(section, key)
        if value is None:
            return default
        if not isinstance(value, bool):
            self.problems.append(
                f"{self._label(section, key)} must be true or false (lower case, no quote marks), but it is {value!r}."
            )
            return default
        return value

    def text_list(self, section: str, key: str) -> list[str] | None:
        value = self._raw(section, key)
        if value is None:
            return None
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            self.problems.append(
                f'{self._label(section, key)} must be a list of quoted words in square brackets, such as ["en", "es"], '
                f"but it is {value!r}."
            )
            return None
        return [item.strip() for item in value]


def _unknown_key_problems(data: dict) -> list[str]:
    problems = []
    for key, value in data.items():
        if isinstance(value, dict):
            if key not in KNOWN_KEYS:
                known = [f"[{name}]" for name in KNOWN_KEYS if name]
                problems.append(f"[{key}] is not a section this file knows. The sections are {', '.join(known)}.")
                continue
            for inner in value:
                if inner not in KNOWN_KEYS[key]:
                    close = difflib.get_close_matches(inner, KNOWN_KEYS[key], n=1)
                    hint = f" Did you mean {close[0]}?" if close else f" The keys there are {', '.join(KNOWN_KEYS[key])}."
                    problems.append(f"[{key}] {inner} is not a setting this file knows.{hint}")
        elif key not in KNOWN_KEYS[""]:
            owner = next((name for name, keys in KNOWN_KEYS.items() if name and key in keys), None)
            if owner:
                problems.append(
                    f"{key} is written above the first [section] line, but it belongs under [{owner}]. Move that line."
                )
            else:
                problems.append(f"{key} is not a setting this file knows. Check the spelling.")
    return problems


def load_settings(path: Path, *, fallback_base_url: str | None = None) -> Settings:
    """Read and check `parish.toml`. Raises `SettingsError` listing every problem in plain English.

    `fallback_base_url` is used only when the file leaves `[site] base_url` blank (the publishing workflow
    passes the address GitHub Pages gives the site). A relative `[brand] logo` is looked up beside the file.
    """
    path = Path(path)
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        raise SettingsError([f"The settings file {path.name} was not found. It belongs in the top folder of the repository."]) from None
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(
            [
                f"{path.name} could not be read: {exc}. The usual cause is a missing quote mark, a missing closing "
                "bracket, or a line that is not in the form key = \"value\"."
            ]
        ) from None
    except UnicodeDecodeError:
        raise SettingsError([f"{path.name} is not saved as plain UTF-8 text."]) from None

    reader = _Reader(data)
    problems = reader.problems
    problems.extend(_unknown_key_problems(data))

    sample_data = reader.boolean("", "sample_data", True)

    # [organization]
    name = reader.text("organization", "name", required=True)
    city = reader.text("organization", "city", required=True)
    state = reader.text("organization", "state", required=True)
    if state and not _STATE_RE.fullmatch(state):
        problems.append(f'[organization] state must be the two-letter postal code, such as "IL", but it is "{state}".')
    state = state.upper()
    zip_code = reader.text("organization", "zip", required=True)
    if zip_code and not _ZIP_RE.fullmatch(zip_code):
        problems.append(
            f'[organization] zip must be a five-digit ZIP code in quote marks, such as "62701" (no +4), but it is "{zip_code}".'
        )
    contact = reader.text("organization", "contact_email", required=True)
    if contact and not _EMAIL_RE.fullmatch(contact):
        problems.append(f'[organization] contact_email must be an email address, such as "office@example.org", but it is "{contact}".')
    slug = reader.text("organization", "slug")
    if slug and not _SLUG_RE.fullmatch(slug):
        problems.append(
            f'[organization] slug may only use small letters, digits and hyphens, such as "st-example-springfield-il", '
            f'but it is "{slug}". Leave it empty ("") to have it made from the name, city and state.'
        )
    website = reader.text("organization", "website")
    if website and not _WEBSITE_RE.fullmatch(website):
        problems.append(f'[organization] website must start with https:// or http://, but it is "{website}". Leave it empty ("") if there is none.')

    # [site]
    languages = reader.text_list("site", "languages")
    if languages is None:
        languages = list(SUPPORTED_LANGUAGES)
    elif languages not in (["en"], ["en", "es"]):
        problems.append(
            f'[site] languages must be ["en"] for English only or ["en", "es"] for English and Spanish, but it is {languages!r}.'
        )
        languages = ["en"]
    base_url = reader.text("site", "base_url")
    if not base_url and fallback_base_url:
        try:
            base_url = normalize_base_url(fallback_base_url)
        except ValueError as exc:
            problems.append(f"--base-url: {exc}.")
            base_url = ""
    elif base_url:
        try:
            base_url = normalize_base_url(base_url)
        except ValueError as exc:
            problems.append(f"[site] base_url: {exc}. Leave it empty (\"\") to let GitHub fill it in.")
            base_url = ""

    campus_en = reader.text("organization", "campus_en", default=DEFAULT_CAMPUS["en"])
    campus_es = reader.text("organization", "campus_es", default=DEFAULT_CAMPUS["es"])

    # [brand]
    colors = {}
    for key, default in (("primary_color", DEFAULT_PRIMARY), ("accent_color", DEFAULT_ACCENT)):
        value = reader.text("brand", key, default=default)
        if not _HEX_RE.fullmatch(value):
            problems.append(
                f'[brand] {key} must be a color written as # and six digits or letters a to f, such as "{default}", '
                f'but it is "{value}".'
            )
            value = ""
        colors[key] = value.upper()
    if all(colors.values()):
        problems.extend(brand_contrast_problems(colors["primary_color"], colors["accent_color"]))
    logo_text = reader.text("brand", "logo")
    logo: Path | None = None
    if logo_text:
        logo = Path(logo_text)
        if not logo.is_absolute():
            logo = path.parent / logo
        if logo.suffix.lower() not in LOGO_SUFFIXES:
            problems.append(
                f'[brand] logo must be an image file ending in {", ".join(LOGO_SUFFIXES)}, but it is "{logo_text}".'
            )
        elif not logo.is_file():
            problems.append(
                f'[brand] logo says "{logo_text}", but there is no such file. Upload the image to that place, fix the '
                'path, or leave logo empty ("") to use the built-in mark.'
            )

    # [grid]
    subregion = reader.text("grid", "egrid_subregion", required=True)
    if subregion and not _SUBREGION_RE.fullmatch(subregion):
        problems.append(
            f'[grid] egrid_subregion must be the short code EPA\'s Power Profiler shows for your ZIP code, such as "RFCW", '
            f'but it is "{subregion}".'
        )
    subregion = subregion.upper()

    # [baseline]
    year = reader._raw("baseline", "calendar_year")
    if year is None:
        year = DEFAULT_BASELINE_YEAR
    elif isinstance(year, bool) or not isinstance(year, int) or not 2000 <= year <= 2100:
        problems.append(
            f"[baseline] calendar_year must be a year written as a plain number, such as {DEFAULT_BASELINE_YEAR} "
            f"(no quote marks), but it is {year!r}."
        )
        year = DEFAULT_BASELINE_YEAR

    # [travel]
    travel_enabled = reader.boolean("travel", "enabled", True)
    label_en = reader.text("travel", "label_en", default=DEFAULT_TRAVEL_LABEL["en"])
    label_es = reader.text("travel", "label_es", default=DEFAULT_TRAVEL_LABEL["es"])

    # [privacy]
    words = reader.text_list("privacy", "forbidden_words") or []
    for word in words:
        if not _WORD_RE.fullmatch(word):
            problems.append(
                f'[privacy] forbidden_words: "{word}" is not a plain word. Each entry is one word or short phrase made of '
                "letters and digits; list every form you want refused, for example both the singular and the plural."
            )

    if not sample_data and name == SAMPLE_NAME:
        problems.append(
            f'sample_data is false, but [organization] name is still the sample name "{SAMPLE_NAME}". Put in your '
            "parish's own name and details, or set sample_data = true while you are still trying things out."
        )

    if problems:
        raise SettingsError(problems)

    return Settings(
        name=name,
        city=city,
        state=state,
        zip=zip_code,
        contact_email=contact,
        egrid_subregion=subregion,
        slug=slug,
        website=website,
        campus_en=campus_en,
        campus_es=campus_es,
        base_url=base_url,
        languages=tuple(languages),
        primary_color=colors["primary_color"],
        accent_color=colors["accent_color"],
        logo=logo,
        baseline_year=year,
        travel_enabled=travel_enabled,
        travel_label_en=label_en,
        travel_label_es=label_es,
        forbidden_words=tuple(words),
        sample_data=sample_data,
    )
