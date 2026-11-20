"""Free-text helpers: attachment decoding, HTML stripping, transcription repair,
and pulling demographics and dates out of note text."""

from __future__ import annotations

import base64
import binascii
import re
from html import unescape
from html.parser import HTMLParser

from .models import DateMention, Gender
from .normalize import DATE_IN_TEXT, parse_date

# ---------------------------------------------------------------- attachments

_B64 = re.compile(r"[A-Za-z0-9+/=\s]+")


def _looks_base64(text: str) -> bool:
    compact = re.sub(r"\s", "", text)
    return len(compact) >= 16 and len(compact) % 4 == 0 and bool(_B64.fullmatch(text))


def decode_attachment(data: str) -> tuple[str, list[str]]:
    """Decode base64 attachment data to text. Returns (text, rules applied)."""
    rules: list[str] = []
    raw = base64.b64decode(data)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
        rules.append("text.latin1_fallback")
    if _looks_base64(text.strip()):
        try:
            text = base64.b64decode(re.sub(r"\s", "", text)).decode("utf-8")
            rules.append("text.double_base64")
        except (binascii.Error, UnicodeDecodeError):
            pass
    return text, rules


class _TextExtractor(HTMLParser):
    BLOCK = {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "table"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return tidy_whitespace(unescape("".join(parser.parts)))


def looks_like_html(text: str) -> bool:
    return bool(re.search(r"<(html|body|div|p|h\d|table|br|span)\b", text[:2000], re.I))


def tidy_whitespace(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.replace("\r", "").split("\n")]
    out = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


# ---------------------------------------------------------------- MTSamples repair

_HEADER = r"[A-Z][A-Z0-9 /&()'-]{2,60}"


def repair_transcription(text: str) -> str:
    """MTSamples lost its line breaks when scraped: they became commas
    ('SUBJECTIVE:,  This...', '...flaring up.,MEDICATIONS: ,'). Put them back."""
    out = re.sub(rf"({_HEADER}):\s*,\s*", r"\1: ", text)
    out = re.sub(rf"\s*,\s*(?={_HEADER}:)", "\n", out)
    out = re.sub(r"(?<=[.:])\s*,\s*(?=\d{1,2}\.\s)", "\n", out)
    return tidy_whitespace(out)


# ---------------------------------------------------------------- demographics

_AGE_SEX = re.compile(
    r"\b(\d{1,3})[- ](?:year|yr)s?[- ]old\b((?:\s+[A-Za-z-]+){0,4}?)\s+"
    r"(male|female|man|woman|gentleman|lady|boy|girl)\b",
    re.I,
)
_AGE = re.compile(r"\b(\d{1,3})[- ](?:year|yr)s?[- ]old\b", re.I)
_TITLE = re.compile(r"\b(Mr|Mrs|Ms|Miss)\.?\s+[A-Z]")
_SEX_WORD: dict[str, Gender] = {
    "male": "male", "man": "male", "gentleman": "male", "boy": "male",
    "female": "female", "woman": "female", "lady": "female", "girl": "female",
}


def extract_age_sex(text: str) -> tuple[int | None, Gender, str | None]:
    """Find age and sex stated in a note, e.g. 'A 23-year-old white female'.

    Returns (age, gender, rule) where rule names how the sex was found.
    """
    head = text[:1500]
    m = _AGE_SEX.search(head)
    if m and int(m.group(1)) <= 120:
        return int(m.group(1)), _SEX_WORD[m.group(3).lower()], "gender.from_text"
    age = None
    m_age = _AGE.search(head)
    if m_age and int(m_age.group(1)) <= 120:
        age = int(m_age.group(1))
    m_title = _TITLE.search(head)
    if m_title:
        return age, ("male" if m_title.group(1) == "Mr" else "female"), "gender.from_title"
    return age, "unknown", None


# ---------------------------------------------------------------- dates in text

_LABELED_DATE = re.compile(
    r"\b(?:DATE OF (?:SERVICE|EXAM(?:INATION)?|PROCEDURE|OPERATION|ADMISSION|VISIT|CONSULTATION|REFERRAL)"
    r"|DATE|Visit|Date)\s*:\s*",
)


def find_date_mentions(text: str) -> list[DateMention]:
    mentions: list[DateMention] = []
    seen: set[str] = set()
    for m in DATE_IN_TEXT.finditer(text):
        raw = m.group(1)
        parsed = parse_date(raw)
        if parsed is None or raw in seen:
            continue
        seen.add(raw)
        mentions.append(DateMention(raw=raw, iso=parsed.iso, precision=parsed.precision))
    return mentions


def find_labeled_date(text: str) -> DateMention | None:
    """The date written after a label like 'DATE OF SERVICE:' or 'Visit:'."""
    for label in _LABELED_DATE.finditer(text):
        m = DATE_IN_TEXT.match(text, label.end())
        if m:
            parsed = parse_date(m.group(1))
            if parsed:
                return DateMention(raw=m.group(1), iso=parsed.iso, precision=parsed.precision)
    return None


# ---------------------------------------------------------------- note header

_HEADER_NAME = re.compile(r"Patient(?: Name)?:[ \t]*([A-Z][\w'.-]*(?:[ \t]+[A-Z][\w'.-]*)+)")
_HEADER_DOB = re.compile(r"(?:\bDOB|Date of Birth):?[ \t]*([0-9A-Za-z/.,\- ]{4,20}?)(?=[)\n]|$)")


def note_patient_header(text: str) -> tuple[str, str | None] | None:
    """Name and ISO birth date written at the top of a note, e.g.
    'Patient: Marcus Johnson (DOB 1971-11-30)' or 'Patient Name: ... / Date of Birth: 07/22/1941'."""
    head = text[:800]
    m_name = _HEADER_NAME.search(head)
    if not m_name:
        return None
    m_dob = _HEADER_DOB.search(head)
    parsed = parse_date(m_dob.group(1).strip()) if m_dob else None
    return m_name.group(1).strip(), parsed.iso if parsed else None
