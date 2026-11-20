"""Normalizers for dates, gender codes, names and MRNs.

Each function is pure and returns the cleaned value; callers record audit entries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

from .models import Gender, Identifier, Precision

# ---------------------------------------------------------------- dates

MONTHS = {
    m: i
    for i, names in enumerate(
        [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",),
         ("jun", "june"), ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"),
         ("oct", "october"), ("nov", "november"), ("dec", "december")],
        start=1,
    )
    for m in names
}

_D = r"(\d{1,2})"
_Y4 = r"(\d{4})"
_MON = r"([A-Za-z]{3,9})\.?"
_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(rf"^{_Y4}-{_D}-{_D}$"), "ymd"),
    (re.compile(r"^(\d{4})(\d{2})(\d{2})$"), "ymd"),
    (re.compile(rf"^{_D}/{_D}/{_Y4}$"), "mdy"),
    (re.compile(rf"^{_D}/{_D}/(\d{{2}})$"), "mdy2"),
    (re.compile(rf"^{_D}-{_D}-{_Y4}$"), "mdy"),
    (re.compile(rf"^{_D}\.{_D}\.{_Y4}$"), "dmy"),
    (re.compile(rf"^{_MON} {_D}(?:st|nd|rd|th)?,? {_Y4}$"), "Mdy"),
    (re.compile(rf"^{_D}(?:st|nd|rd|th)? {_MON},? {_Y4}$"), "dMy"),
    (re.compile(rf"^{_Y4}-{_D}$"), "ym"),
    (re.compile(rf"^{_D}/{_Y4}$"), "my"),
    (re.compile(rf"^{_MON},? {_Y4}$"), "My"),
    (re.compile(rf"^{_Y4}$"), "y"),
]


@dataclass(frozen=True)
class ParsedDate:
    value: date
    precision: Precision
    day_first: bool = False

    @property
    def iso(self) -> str:
        if self.precision == "year":
            return f"{self.value.year:04d}"
        if self.precision == "month":
            return f"{self.value.year:04d}-{self.value.month:02d}"
        return self.value.isoformat()


def _two_digit_year(yy: int, ref_year: int) -> int:
    """Pick the century so the year is not in the future."""
    year = 2000 + yy
    return year if year <= ref_year else year - 100


def parse_date(raw: str | None, ref_year: int | None = None) -> ParsedDate | None:
    """Parse a date written in any of the common formats. Returns None if invalid.

    Slash dates are read as month/day (US). If the first number cannot be a
    month (13 or more), the date is read as day/month instead.
    """
    if not raw:
        return None
    text = " ".join(raw.strip().split())
    ref_year = ref_year or date.today().year
    for pattern, shape in _PATTERNS:
        m = pattern.match(text)
        if not m:
            continue
        g = m.groups()
        try:
            if shape == "ymd":
                return ParsedDate(date(int(g[0]), int(g[1]), int(g[2])), "day")
            if shape in ("mdy", "mdy2"):
                first, second = int(g[0]), int(g[1])
                year = int(g[2]) if shape == "mdy" else _two_digit_year(int(g[2]), ref_year)
                if first > 12 >= second:
                    return ParsedDate(date(year, second, first), "day", day_first=True)
                return ParsedDate(date(year, first, second), "day")
            if shape == "dmy":
                return ParsedDate(date(int(g[2]), int(g[1]), int(g[0])), "day", day_first=True)
            if shape == "Mdy":
                return ParsedDate(date(int(g[2]), MONTHS[g[0].lower()], int(g[1])), "day")
            if shape == "dMy":
                return ParsedDate(date(int(g[2]), MONTHS[g[1].lower()], int(g[0])), "day")
            if shape == "ym":
                return ParsedDate(date(int(g[0]), int(g[1]), 1), "month")
            if shape == "my":
                return ParsedDate(date(int(g[1]), int(g[0]), 1), "month")
            if shape == "My":
                return ParsedDate(date(int(g[1]), MONTHS[g[0].lower()], 1), "month")
            if shape == "y":
                return ParsedDate(date(int(g[0]), 1, 1), "year")
        except (ValueError, KeyError):
            return None
    return None


@dataclass(frozen=True)
class ParsedDateTime:
    value: datetime  # always timezone-aware UTC
    precision: Precision
    assumed_utc: bool = False


def parse_datetime(raw: str | None) -> ParsedDateTime | None:
    """Parse an ISO 8601 date-time (any offset) or a plain date. Result is in UTC."""
    if not raw:
        return None
    text = raw.strip()
    if "T" in text or " " in text and ":" in text:
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            dt = None
        if dt is not None:
            if dt.tzinfo is None:
                return ParsedDateTime(dt.replace(tzinfo=timezone.utc), "datetime", assumed_utc=True)
            return ParsedDateTime(dt.astimezone(timezone.utc), "datetime")
    parsed = parse_date(text)
    if parsed is None:
        return None
    d = parsed.value
    return ParsedDateTime(datetime(d.year, d.month, d.day, tzinfo=timezone.utc), parsed.precision)


_MONTH_WORD = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
DATE_IN_TEXT = re.compile(
    rf"""(?<![\w/.-])(
        \d{{4}}-\d{{2}}-\d{{2}}
      | \d{{1,2}}/\d{{1,2}}/\d{{2,4}}
      | \d{{1,2}}-\d{{1,2}}-\d{{4}}
      | {_MONTH_WORD}\ \d{{1,2}}(?:st|nd|rd|th)?,?\ \d{{4}}
      | \d{{1,2}}\ {_MONTH_WORD},?\ \d{{4}}
      | \d{{1,2}}/\d{{4}}
    )(?![\w/])""",
    re.VERBOSE,
)

# ---------------------------------------------------------------- gender

_GENDER: dict[str, Gender] = {
    "male": "male", "m": "male", "man": "male", "boy": "male", "gentleman": "male",
    "female": "female", "f": "female", "woman": "female", "girl": "female", "lady": "female",
    "other": "other", "o": "other", "x": "other", "non-binary": "other", "nonbinary": "other",
    "unknown": "unknown", "u": "unknown", "unk": "unknown",
    # ISO/IEC 5218 numeric codes
    "1": "male", "2": "female", "0": "unknown", "9": "unknown",
}


def normalize_gender(raw: str | None) -> Gender:
    if raw is None:
        return "unknown"
    return _GENDER.get(raw.strip().lower(), "unknown")


# ---------------------------------------------------------------- names

_ROMAN = {"ii", "iii", "iv", "v", "vi"}
_SUFFIX = {"jr": "Jr", "jr.": "Jr.", "sr": "Sr", "sr.": "Sr."}


def _cap_word(word: str) -> str:
    low = word.lower()
    if low in _ROMAN:
        return low.upper()
    if low in _SUFFIX:
        return _SUFFIX[low]
    return re.sub(r"(^|[-'])([a-z])", lambda m: m.group(1) + m.group(2).upper(), low)


def normalize_name_part(raw: str | None) -> str | None:
    """Trim spaces, drop trailing digits (generator artifacts like 'Abe604'),
    and fix case when a name is written all upper or all lower case."""
    if raw is None:
        return None
    text = " ".join(raw.split())
    text = re.sub(r"(?<=[A-Za-z])\d+\b", "", text)
    if not text:
        return None
    if text.isupper() or text.islower():
        text = " ".join(_cap_word(w) for w in text.split(" "))
    return text


# ---------------------------------------------------------------- MRN

_NOT_MRN_SYSTEMS = ("urn:ietf:rfc:3986", "ssn", "synthea", "nhanes", "passport", "driver")


def _ident_type_text(ident: dict) -> str:
    t = ident.get("type") or {}
    codes = " ".join(c.get("code") or "" for c in t.get("coding", []))
    return f"{t.get('text') or ''} {codes}"


def mrn_rank(ident: dict) -> int | None:
    """How likely an identifier is a medical record number (lower is better). None = not an MRN."""
    system = (ident.get("system") or "").lower()
    value = (ident.get("value") or "").strip()
    if not value or any(s in system for s in _NOT_MRN_SYSTEMS) or value.lower().startswith("urn:"):
        return None
    type_text = _ident_type_text(ident)
    if re.search(r"\bMR\b", type_text) or re.search(r"mrn|medical record", type_text, re.I):
        return 0
    if re.search(r"mrn|medical[-_ ]?record", system):
        return 1
    if value.upper().startswith("MRN"):
        return 2
    if "patient" in system or system.startswith("urn:oid:"):
        return 3
    return None


def normalize_mrn(value: str) -> str:
    """Upper case, drop an 'MRN' prefix and any separators: 'mrn-100002' -> '100002'."""
    text = value.strip().upper()
    text = re.sub(r"^MRN[\s:#-]*", "", text)
    return re.sub(r"[^A-Z0-9]", "", text)


def split_identifiers(identifiers: list[dict]) -> tuple[Identifier | None, list[Identifier]]:
    """Pick the best MRN. Return (mrn, other identifiers)."""
    ranked = [(mrn_rank(i), n, i) for n, i in enumerate(identifiers)]
    candidates = sorted((r, n, i) for r, n, i in ranked if r is not None)
    mrn = None
    if candidates:
        best = candidates[0][2]
        mrn = Identifier(system=best.get("system"), value=best["value"])
    others = [
        Identifier(system=i.get("system"), value=i["value"])
        for i in identifiers
        if i.get("value") and (mrn is None or i.get("value") != mrn.value or i.get("system") != mrn.system)
    ]
    return mrn, others


# ---------------------------------------------------------------- labs

_UNITS = {"mg/dl": "mg/dL", "g/dl": "g/dL", "mmol/l": "mmol/L", "meq/l": "mEq/L", "u/l": "U/L", "iu/l": "IU/L"}
_INTERP = {"h": "high", "hh": "high", "hu": "high", ">": "high", "l": "low", "ll": "low", "lu": "low", "<": "low",
           "n": "normal", "a": "abnormal", "aa": "abnormal"}


def normalize_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    unit = unit.strip()
    return _UNITS.get(unit.lower(), unit)


def normalize_interpretation(code: str | None) -> str | None:
    if not code:
        return None
    return _INTERP.get(code.strip().lower())
