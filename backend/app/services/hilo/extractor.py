from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .date_parser import (
    ENGLISH_MONTH_ABBREVS,
    GERMAN_MONTHS,
    _expand_two_digit_year,
    _month_from_name,
)


@dataclass
class MeasurementRow:
    date: str
    time: str
    systolic: int
    diastolic: int
    heart_rate: int
    source_column: str
    row_index_on_page: int


_MONTH_NAMES_ALT = (
    "januar|februar|m(?:ä|Ã¤|ae|a)rz|april|mai|juni|juli|august|september|sept?|oktober|november|dezember"
    "|jan|feb|mar|apr|may|jun|jul|aug|oct|nov|dec"
)

_ROW_DOT_DATE = re.compile(
    r"(?P<date>\d{2}\.\d{2}\.\d{4})\s+"
    r"(?P<time>\d{2}:\d{2})\s+"
    r"(?P<sbp>\d{2,3})\s+"
    r"(?P<dbp>\d{2,3})\s+"
    r"(?P<hr>\d{2,3})"
)

_ROW_GERMAN_DATE = re.compile(
    rf"(?P<day>\d{{1,2}})\s+"
    rf"(?P<month>{_MONTH_NAMES_ALT})[.,]?\s+"
    r"(?P<year>\d{2,4})\s+"
    r"(?P<time>\d{1,2}:\d{2})\s+"
    r"(?P<sbp>\d{2,3})\s+"
    r"(?P<dbp>\d{2,3})\s+"
    r"(?P<hr>\d{2,3})(?!\d)",
    re.IGNORECASE,
)


def extract_patient_data(page_text: str) -> dict[str, str | None]:
    """Best-effort patient extraction.

    Supports two layouts:
      * labelled (``Name: ...``) used by mock/test fixtures;
      * positional Hilo export (``Monatsbericht`` header followed by raw
        lines).
    """

    labelled = _extract_patient_labelled(page_text)
    positional = _extract_patient_positional(page_text)

    merged: dict[str, str | None] = {}
    for key in ("full_name", "email", "gender", "birth_date", "height_cm", "weight_kg"):
        merged[key] = labelled.get(key) or positional.get(key)
    return merged


def _extract_patient_labelled(page_text: str) -> dict[str, str | None]:
    patterns = {
        "full_name": r"Name\s*:?\s*(.+)",
        "email": r"E-?Mail\s*:?\s*([\w.+\-]+@[\w.\-]+)",
        "gender": r"Geschlecht\s*:?\s*([A-Za-zäöüÄÖÜßÃ¤Ã¶Ã¼Ã„Ã–ÃœÃŸ]+)",
        "birth_date": r"Geburtsdatum\s*:?\s*(\d{1,2}\.\d{1,2}\.\d{2,4})",
        "height_cm": r"Gr(?:ö|Ã¶|oe)(?:ß|ÃŸ|ss)e\s*:?\s*(\d{2,3})\s*cm",
        "weight_kg": r"Gewicht\s*:?\s*(\d{2,3}(?:[,.]\d+)?)\s*kg",
    }
    parsed: dict[str, str | None] = {}
    for field, pattern in patterns.items():
        match = re.search(pattern, page_text, re.IGNORECASE)
        parsed[field] = match.group(1).strip() if match else None
    return parsed


def _extract_patient_positional(page_text: str) -> dict[str, str | None]:
    parsed: dict[str, str | None] = {
        "full_name": None,
        "email": None,
        "gender": None,
        "birth_date": None,
        "height_cm": None,
        "weight_kg": None,
    }

    lines = [line.strip() for line in page_text.splitlines() if line.strip()]

    email_match = re.search(r"[\w.+\-]+@[\w.\-]+", page_text)
    if email_match:
        parsed["email"] = email_match.group(0)

    for idx, line in enumerate(lines):
        if line.lower().startswith("monatsbericht"):
            if idx + 1 < len(lines) and not _looks_like_email(lines[idx + 1]):
                parsed["full_name"] = lines[idx + 1]
            break
    if not parsed["full_name"] and lines:
        if not _looks_like_email(lines[0]):
            parsed["full_name"] = lines[0]

    gender_birth = re.search(
        r"(?P<gender>[A-Za-zäöüÄÖÜßÃ¤Ã¶Ã¼Ã„Ã–ÃœÃŸ]+)\s*\|\s*"
        r"(?P<birth>\d{1,2}[\s./,-]+[A-Za-zäöüÄÖÜß.]+[\s./,-]+\d{2,4})",
        page_text,
    )
    if gender_birth:
        parsed["gender"] = gender_birth.group("gender")
        parsed["birth_date"] = gender_birth.group("birth").strip()

    height_weight = re.search(
        r"(?P<height>\d{2,3})\s*cm\s*\|\s*(?P<weight>\d{2,3}(?:[,.]\d+)?)\s*kg",
        page_text,
        re.IGNORECASE,
    )
    if height_weight:
        parsed["height_cm"] = height_weight.group("height")
        parsed["weight_kg"] = height_weight.group("weight")

    return parsed


def _looks_like_email(value: str) -> bool:
    return "@" in value


def extract_summary(page_text: str) -> dict[str, dict[str, float | int | None]]:
    tabular = _extract_tabular_summary(page_text)
    if _summary_has_values(tabular):
        return tabular

    return {
        "day_rest": _extract_summary_line(page_text, ["Tag", "Ruhe"]),
        "night": _extract_summary_line(page_text, ["Nacht"]),
        "all_measurements": _extract_summary_line(page_text, ["Alle", "Messungen"]),
    }


def _summary_has_values(summary: dict[str, dict[str, float | int | None]]) -> bool:
    for section in summary.values():
        if any(value is not None for value in section.values()):
            return True
    return False


def _extract_summary_line(page_text: str, labels: list[str]) -> dict[str, float | int | None]:
    numbers = {
        "mean": None,
        "sd": None,
        "max": None,
        "min": None,
        "measurements": None,
    }
    label_pattern = r".*".join(labels)
    line_match = re.search(rf"{label_pattern}[^\n]*", page_text, re.IGNORECASE)
    if not line_match:
        return numbers

    values = re.findall(r"\d+(?:[.,]\d+)?", line_match.group(0))
    if len(values) >= 5:
        numbers["mean"] = float(values[0].replace(",", "."))
        numbers["sd"] = float(values[1].replace(",", "."))
        numbers["max"] = float(values[2].replace(",", "."))
        numbers["min"] = float(values[3].replace(",", "."))
        numbers["measurements"] = int(float(values[4].replace(",", ".")))
    return numbers


def _extract_tabular_summary(page_text: str) -> dict[str, dict[str, float | int | None]]:
    """Read the overview table on page 1 and split values per vital.

    Layout in the Hilo PDF (per row):
        Tag/Ruhe                Nacht                   Alle Messungen
        SBP  DBP  HR            SBP  DBP  HR            SBP  DBP  HR

    Backwards-compat: ``mean`` / ``sd`` / ``max`` / ``min`` keep referring to
    the *systolic* value as before. New keys (``mean_diastolic`` /
    ``mean_heart_rate`` / ``min_diastolic`` / ``min_heart_rate`` /
    ``max_diastolic`` / ``max_heart_rate``) carry the additional vitals so the
    night-reference logic can use them. ``measurements`` stays a single integer
    (count of rows in that section).
    """

    empty: dict[str, float | int | None] = {
        "mean": None, "sd": None, "max": None, "min": None, "measurements": None,
        "mean_diastolic": None, "mean_heart_rate": None,
        "min_diastolic": None, "min_heart_rate": None,
        "max_diastolic": None, "max_heart_rate": None,
    }
    result = {
        "day_rest": dict(empty),
        "night": dict(empty),
        "all_measurements": dict(empty),
    }

    # Mapping label -> primary SBP key, plus optional DBP/HR sister keys.
    # ``Messungen`` is intentionally *only* SBP-style (one count per column),
    # so we keep it on the legacy single-value path further below.
    row_labels = [
        ("Mittelwert", "mean", "mean_diastolic", "mean_heart_rate", float),
        ("SD", "sd", None, None, float),
        ("Max", "max", "max_diastolic", "max_heart_rate", float),
        ("Mindest", "min", "min_diastolic", "min_heart_rate", float),
    ]

    for label, key_sbp, key_dbp, key_hr, caster in row_labels:
        numbers = _numbers_after_label(page_text, label, count=9)
        if not numbers:
            continue
        try:
            day_sbp = _cast_number(numbers[0], caster)
            day_dbp = _cast_number(numbers[1], caster) if key_dbp else None
            day_hr = _cast_number(numbers[2], caster) if key_hr else None
            night_sbp = _cast_number(numbers[3], caster)
            night_dbp = _cast_number(numbers[4], caster) if key_dbp else None
            night_hr = _cast_number(numbers[5], caster) if key_hr else None
            all_sbp = _cast_number(numbers[6], caster)
            all_dbp = _cast_number(numbers[7], caster) if key_dbp else None
            all_hr = _cast_number(numbers[8], caster) if key_hr else None
        except (ValueError, IndexError):
            continue
        result["day_rest"][key_sbp] = day_sbp
        result["night"][key_sbp] = night_sbp
        result["all_measurements"][key_sbp] = all_sbp
        if key_dbp:
            result["day_rest"][key_dbp] = day_dbp
            result["night"][key_dbp] = night_dbp
            result["all_measurements"][key_dbp] = all_dbp
        if key_hr:
            result["day_rest"][key_hr] = day_hr
            result["night"][key_hr] = night_hr
            result["all_measurements"][key_hr] = all_hr

    # ``Messungen`` is a row of three integers (one per column); we still need
    # to populate it so the rest of the pipeline knows the per-class counts.
    counts = _numbers_after_label(page_text, "Messungen", count=3)
    if counts:
        try:
            result["day_rest"]["measurements"] = int(float(counts[0].replace(",", ".")))
            result["night"]["measurements"] = int(float(counts[1].replace(",", ".")))
            result["all_measurements"]["measurements"] = int(float(counts[2].replace(",", ".")))
        except (ValueError, IndexError):
            pass

    return result


def _cast_number(raw: str, caster):
    normalized = raw.replace(",", ".")
    if caster is int:
        return int(float(normalized))
    return caster(normalized)


def _numbers_after_label(page_text: str, label: str, count: int) -> list[str] | None:
    """Find a line containing only ``label`` and collect the next ``count`` numbers."""

    lines = [ln.strip() for ln in page_text.splitlines()]
    for idx, line in enumerate(lines):
        if line != label:
            continue
        numbers: list[str] = []
        for follower in lines[idx + 1 :]:
            if not follower:
                continue
            if re.fullmatch(r"\d+(?:[.,]\d+)?", follower):
                numbers.append(follower)
                if len(numbers) >= count:
                    return numbers[:count]
                continue
            inline_numbers = re.findall(r"\d+(?:[.,]\d+)?", follower)
            if inline_numbers and re.fullmatch(r"[\d\s.,]+", follower):
                numbers.extend(inline_numbers)
                if len(numbers) >= count:
                    return numbers[:count]
                continue
            break
        if len(numbers) >= count:
            return numbers[:count]
    return None


def extract_measurement_rows(page_text: str) -> list[MeasurementRow]:
    rows: list[MeasurementRow] = []
    seen_signatures: set[tuple] = set()
    row_index = 0

    for match in _ROW_DOT_DATE.finditer(page_text):
        row_index += 1
        signature = (match.group("date"), match.group("time"))
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        rows.append(
            MeasurementRow(
                date=match.group("date"),
                time=match.group("time"),
                systolic=int(match.group("sbp")),
                diastolic=int(match.group("dbp")),
                heart_rate=int(match.group("hr")),
                source_column=_guess_column(match.start()),
                row_index_on_page=row_index,
            )
        )

    for match in _ROW_GERMAN_DATE.finditer(page_text):
        iso = _iso_from_match(match)
        if not iso:
            continue
        signature = (iso, match.group("time"))
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        row_index += 1
        rows.append(
            MeasurementRow(
                date=iso,
                time=match.group("time"),
                systolic=int(match.group("sbp")),
                diastolic=int(match.group("dbp")),
                heart_rate=int(match.group("hr")),
                source_column=_guess_column(match.start()),
                row_index_on_page=row_index,
            )
        )

    return rows


def _iso_from_match(match: re.Match[str]) -> str | None:
    month = _month_from_name(match.group("month"))
    if month is None:
        return None
    year = _expand_two_digit_year(int(match.group("year")))
    day = int(match.group("day"))
    try:
        return f"{year:04d}-{month:02d}-{day:02d}"
    except (ValueError, TypeError):
        return None


def _guess_column(char_offset: int) -> str:
    if char_offset < 2500:
        return "left"
    if char_offset < 5000:
        return "middle"
    return "right"


def extract_page_images(page: Any) -> list[dict[str, Any]]:
    images = []
    for image in page.get_images(full=True):
        xref = image[0]
        base = page.parent.extract_image(xref)
        images.append(base)
    return images


# re-export for callers that used the private names historically
__all__ = [
    "MeasurementRow",
    "extract_patient_data",
    "extract_summary",
    "extract_measurement_rows",
    "extract_page_images",
    "GERMAN_MONTHS",
    "ENGLISH_MONTH_ABBREVS",
]
