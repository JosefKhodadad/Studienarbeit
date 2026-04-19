from __future__ import annotations

from dataclasses import dataclass
from calendar import monthrange
from datetime import date, datetime
import re

GERMAN_MONTHS = {
    "januar": 1,
    "februar": 2,
    "märz": 3,
    "maerz": 3,
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


@dataclass(frozen=True)
class ReportPeriod:
    month: int
    year: int


def parse_german_date(value: str) -> date | None:
    value = value.strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def extract_report_period(text: str) -> ReportPeriod | None:
    match = re.search(r"(januar|februar|m(?:ä|ae)rz|april|mai|juni|juli|august|september|oktober|november|dezember)\s+(20\d{2})", text, re.IGNORECASE)
    if not match:
        return None
    month_name = match.group(1).lower().replace("ä", "ae")
    return ReportPeriod(month=GERMAN_MONTHS[month_name], year=int(match.group(2)))


def calculate_age_at_report(birth_date: date | None, report_period: ReportPeriod | None) -> int | None:
    if not birth_date or not report_period:
        return None
    last_day = monthrange(report_period.year, report_period.month)[1]
    report_date = date(report_period.year, report_period.month, last_day)
    age = report_date.year - birth_date.year
    if (report_date.month, report_date.day) < (birth_date.month, birth_date.day):
        age -= 1
    return age
