from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime
import re

GERMAN_MONTHS = {
    "januar": 1,
    "februar": 2,
    "maerz": 3,
    "märz": 3,
    "mÃ¤rz": 3,
    "mã¤rz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
}

ENGLISH_MONTH_ABBREVS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

_DAY_MONTH_YEAR_RE = re.compile(
    r"^\s*(\d{1,2})[\s./,-]+([A-Za-zäöüÄÖÜß]+)[\s./,-]+(\d{2,4})\s*$"
)


@dataclass(frozen=True)
class ReportPeriod:
    month: int
    year: int


def _normalize_month_token(token: str) -> str:
    return (
        token.lower()
        .strip(".")
        .replace("ã¤", "ae")
        .replace("Ã¤", "ae")
        .replace("ä", "ae")
    )


def _month_from_name(token: str) -> int | None:
    normalized = _normalize_month_token(token)
    if normalized in GERMAN_MONTHS:
        return GERMAN_MONTHS[normalized]
    if normalized in ENGLISH_MONTH_ABBREVS:
        return ENGLISH_MONTH_ABBREVS[normalized]
    if len(normalized) >= 3 and normalized[:3] in ENGLISH_MONTH_ABBREVS:
        return ENGLISH_MONTH_ABBREVS[normalized[:3]]
    return None


def _expand_two_digit_year(year: int) -> int:
    if year >= 100:
        return year
    return 2000 + year if year < 70 else 1900 + year


def parse_german_date(value: str) -> date | None:
    value = value.strip()
    if not value:
        return None
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue

    match = _DAY_MONTH_YEAR_RE.match(value)
    if match:
        day = int(match.group(1))
        month = _month_from_name(match.group(2))
        if month is None:
            return None
        year = _expand_two_digit_year(int(match.group(3)))
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def extract_report_period(text: str) -> ReportPeriod | None:
    match = re.search(
        r"(januar|februar|m(?:ä|Ã¤|ae)rz|april|mai|juni|juli|august|september|oktober|november|dezember)"
        r"[\s,\.]+"
        r"(\d{2,4})",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None

    month = _month_from_name(match.group(1))
    if month is None:
        return None
    year = _expand_two_digit_year(int(match.group(2)))
    if year < 2000 or year > 2100:
        return None
    return ReportPeriod(month=month, year=year)


def calculate_age_at_report(birth_date: date | None, report_period: ReportPeriod | None) -> int | None:
    if not birth_date or not report_period:
        return None
    last_day = monthrange(report_period.year, report_period.month)[1]
    report_date = date(report_period.year, report_period.month, last_day)
    age = report_date.year - birth_date.year
    if (report_date.month, report_date.day) < (birth_date.month, birth_date.day):
        age -= 1
    return age
